"""Retours de l'utilisateur sur les offres et les entreprises : « J'aime » / « Pas intéressé ».

Un retour reclasse tout de suite (ranking.py), se retire à tout moment et ne relance pas la notation Gemini.
- « Pas intéressé » sur une offre : statut pas_interesse (posé par l'utilisateur, historisé) + retour enregistré
  (raisons facultatives). La raison « déjà passé en entretien » renvoie une PROPOSITION d'exclure l'entreprise.
- « J'aime » sur une offre : elle rejoint les offres de référence (action explicite de l'utilisateur).
- Entreprises : un retour par entreprise (table company_feedback) ; « Pas intéressé » écarte ses offres.
Les retours non encore synthétisés alimentent la synthèse des règles (rules_synthesis.py).
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import entitlements, rules_synthesis, services, tasks
from .db import get_db
from .entitlements import RequirePro
from .llm import LLM, get_llm, require_llm
from .models import Company, CompanyFeedback, Job, JobFeedback, ProcessStatus, User
from .profile import ReferenceJob, get_profile, profile_data
from .profile_api import ProposalPreview, create_proposal
from .proposals import item, number
from .schemas import CompanyOut, JobOut
from .users import current_user

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])

DB = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]
Gemini = Annotated[LLM | None, Depends(get_llm)]

SYNTHESIS_THRESHOLD = 3  # nouveaux retours avant de proposer « Mettre à jour mes règles »

REASONS: dict[str, str] = {
    "trop_front": "Trop front / TypeScript",
    "trop_sql": "Trop SQL / data",
    "trop_ia": "Trop IA / ML",
    "trop_senior": "Trop senior",
    "cloud_impose": "Cloud imposé",
    "deplacements": "Trop de déplacements",
    "salaire_bas": "Salaire trop bas",
    "trop_grosse": "Entreprise trop grosse",
    "deja_entretien": "Déjà passé en entretien",
}


class FeedbackIn(BaseModel):
    sentiment: Literal["like", "dislike"]
    reasons: list[Literal[tuple(REASONS)]] = Field(default_factory=list, max_length=len(REASONS))  # type: ignore[valid-type]
    text: Annotated[str | None, Field(default=None, max_length=1000)] = None


class CompanyReactionIn(BaseModel):
    value: Literal["like", "dislike"] | None = None


class FeedbackOut(BaseModel):
    id: int
    job_id: int
    job_label: str
    sentiment: str
    reasons: list[str]
    reason_labels: list[str]
    text: str | None
    created_at: datetime
    synthesized: bool


class FeedbackResult(BaseModel):
    feedback: FeedbackOut
    job: JobOut
    new_feedback: int  # retours pas encore pris en compte par une synthèse des règles
    suggest_synthesis: bool
    proposal: ProposalPreview | None = None  # ex. exclure l'entreprise (à confirmer)


class FeedbackSummary(BaseModel):
    new: int
    total: int
    threshold: int = SYNTHESIS_THRESHOLD
    recent: list[FeedbackOut]


def job_label(job: Job) -> str:
    return f"{job.company.name} – {job.title}"


def feedback_out(fb: JobFeedback) -> FeedbackOut:
    return FeedbackOut(
        id=fb.id, job_id=fb.job_id, job_label=job_label(fb.job), sentiment=fb.sentiment, reasons=fb.reasons or [],
        reason_labels=[REASONS.get(r, r) for r in fb.reasons or []], text=fb.text, created_at=fb.created_at,
        synthesized=fb.synthesized_at is not None,
    )


def new_feedback_count(db: Session, user: User) -> int:
    return db.scalar(select(func.count(JobFeedback.id)).where(
        JobFeedback.user_id == user.id, JobFeedback.synthesized_at.is_(None))) or 0


def _describe_reasons(body: FeedbackIn) -> str | None:
    parts = [REASONS[r] for r in body.reasons] + ([body.text.strip()] if body.text and body.text.strip() else [])
    return " ; ".join(parts) or None


@router.get("/feedback/reasons")
def feedback_reasons() -> list[dict]:
    return [{"key": k, "label": v} for k, v in REASONS.items()]


@router.get("/feedback")
def feedback_summary(db: DB, user: Me) -> FeedbackSummary:
    rows = db.scalars(select(JobFeedback).where(JobFeedback.user_id == user.id)
                      .order_by(JobFeedback.id.desc()).limit(30)).all()
    total = db.scalar(select(func.count(JobFeedback.id)).where(JobFeedback.user_id == user.id)) or 0
    access = entitlements.access_for(db, user)
    # Retour donné pendant une période Pro sur une offre aujourd'hui floutée : omis en Free (son libellé la révélerait)
    recent = [feedback_out(f) for f in rows if not access.job_is_locked(f.job)]
    return FeedbackSummary(new=new_feedback_count(db, user), total=total, recent=recent)


@router.post("/profile/rules/synthesize", dependencies=[RequirePro("rules_synthesis")])
def synthesize_rules(db: DB, user: Me, llm: Gemini) -> ProposalPreview:
    """« Mettre à jour mes règles » : règles proposées à partir des retours, validées une par une."""
    llm = require_llm(llm)
    data = profile_data(get_profile(db, user))
    items, used = rules_synthesis.synthesize(db, user, data, llm, REASONS)
    if not used:
        raise HTTPException(422, "Aucun retour sur des offres pour l'instant : utilisez « J'aime » ou « Pas intéressé ».")
    db.commit()  # retours marqués « synthétisés »
    return create_proposal(db, user, kind="regles", source="feedback", items=items,
                           extra={"details": [f"{used} retour{'s' if used > 1 else ''} analysé{'s' if used > 1 else ''}"]})


def _reactable_job(db: Session, user: User, job_id: int) -> Job:
    job = services.get_or_404(db, Job, job_id)
    if entitlements.access_for(db, user).job_is_locked(job):
        # Offre verrouillée (Free) : la réponse (offre de référence, libellé) révélerait son titre et son entreprise
        raise entitlements.pro_required("Cette offre est réservée à l'offre Pro")
    return job


def _set_reference(db: Session, user: User, job: Job, why: str | None, *, liked: bool) -> None:
    """Tient la liste des offres de référence à jour (contexte de la notation Gemini), SANS changer la version du
    profil : un J'aime reclasse tout de suite (ranking.py) et ne relance pas la notation de toutes les offres."""
    profile = get_profile(db, user)
    data = profile_data(profile)
    others = [r for r in data.reference_jobs if r.job_id != job.id]
    if liked and len(others) != len(data.reference_jobs):
        return  # déjà une référence : on garde la première raison donnée
    if liked:
        ref = ReferenceJob(job_id=job.id, title=job.title, company=job.company.name, why=why)
        others = [*others, ref][-50:]
    profile.reference_jobs = [r.model_dump(mode="json") for r in others]


def _undo_dislike(db: Session, user: User, job_id: int) -> None:
    """Retire le statut « pas intéressé » : retour au statut précédent, ou suppression du process s'il n'avait
    été créé que par ce retour."""
    app = services.user_apps(db, user).get(job_id)
    if app is None or app.status != ProcessStatus.pas_interesse:
        return
    previous = next((e.from_status for e in reversed(app.events) if e.to_status == ProcessStatus.pas_interesse), None)
    kept = app.notes or app.next_step or app.next_step_date or app.applied_on or app.draft
    if previous in (None, ProcessStatus.nouveau) and not kept:
        db.delete(app)
    else:
        services.apply_status(app, previous or ProcessStatus.nouveau)


def _forget(db: Session, user: User, job_id: int) -> None:
    db.execute(delete(JobFeedback).where(JobFeedback.user_id == user.id, JobFeedback.job_id == job_id))


@router.delete("/jobs/{job_id}/feedback")
def remove_feedback(job_id: int, db: DB, user: Me) -> JobOut:
    """Retire le J'aime ou le « Pas intéressé » : le classement revient comme avant."""
    job = _reactable_job(db, user, job_id)
    _forget(db, user, job.id)
    _set_reference(db, user, job, None, liked=False)
    _undo_dislike(db, user, job.id)
    db.commit()
    tasks.profile_changed(db, user.id)
    return services.get_job(db, user, job.id)


@router.put("/companies/{company_id}/reaction")
def react_to_company(company_id: int, body: CompanyReactionIn, db: DB, user: Me) -> CompanyOut:
    """J'aime / Pas intéressé sur une entreprise (value null : retiré). « Pas intéressé » écarte ses offres."""
    company = services.get_or_404(db, Company, company_id)
    if entitlements.access_for(db, user).company_is_locked(company.id):
        raise entitlements.pro_required("Cette entreprise est réservée à l'offre Pro")
    current = db.scalar(select(CompanyFeedback).where(
        CompanyFeedback.user_id == user.id, CompanyFeedback.company_id == company.id))
    if body.value is None:
        if current is not None:
            db.delete(current)
    elif current is None:
        db.add(CompanyFeedback(user_id=user.id, company_id=company.id, sentiment=body.value))
    else:
        current.sentiment = body.value
    db.commit()
    tasks.profile_changed(db, user.id)  # nouvelle présélection : les offres à évaluer en priorité changent
    return services.ranked_companies(db, user, [company])[0]


@router.post("/jobs/{job_id}/feedback")
def give_feedback(job_id: int, body: FeedbackIn, db: DB, user: Me) -> FeedbackResult:
    """Un seul retour courant par offre : le nouveau remplace le précédent."""
    job = _reactable_job(db, user, job_id)
    text = body.text.strip() if body.text and body.text.strip() else None
    _forget(db, user, job.id)
    fb = JobFeedback(user_id=user.id, job_id=job.id, sentiment=body.sentiment, reasons=list(body.reasons), text=text)
    db.add(fb)
    proposal = None

    if body.sentiment == "dislike":
        _set_reference(db, user, job, None, liked=False)
        # Statut posé par l'utilisateur (historisé dans application_events, comme l'API)
        services.set_job_status(db, user, job.id, ProcessStatus.pas_interesse, note=_describe_reasons(body))
        if "deja_entretien" in body.reasons:
            data = profile_data(get_profile(db, user))
            if all(ex.company_id != job.company_id and ex.name.lower() != job.company.name.lower()
                   for ex in data.excluded_companies):
                db.commit()
                proposal = create_proposal(db, user, kind="regles", source="feedback", items=number([
                    item("excluded_companies", "add",
                         {"name": job.company.name, "company_id": job.company_id, "reason": "Déjà passé en entretien"},
                         inferred=True, reason=f"Vous avez indiqué être déjà passé en entretien ({job_label(job)}).",
                         examples=[job_label(job)]),
                ]))
    else:
        _undo_dislike(db, user, job.id)
        _set_reference(db, user, job, _describe_reasons(body), liked=True)
    db.commit()
    db.refresh(fb)
    tasks.profile_changed(db, user.id)
    new = new_feedback_count(db, user)
    return FeedbackResult(feedback=feedback_out(fb), job=services.get_job(db, user, job.id), new_feedback=new,
                          suggest_synthesis=new >= SYNTHESIS_THRESHOLD, proposal=proposal)
