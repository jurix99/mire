"""Retours de l'utilisateur sur les offres : « J'aime » / « Pas intéressé » (+ raisons facultatives).

- « Pas intéressé » : statut pas_interesse (posé par l'utilisateur, historisé) + retour enregistré ; rien d'autre
  n'est modifié. La raison « déjà passé en entretien » renvoie une PROPOSITION d'exclure l'entreprise.
- « J'aime » : l'offre est ajoutée directement aux offres de référence (action explicite de l'utilisateur).
Les retours non encore synthétisés alimentent la synthèse des règles (rules_synthesis.py).
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import assessments, entitlements, rules_synthesis, services
from .db import get_db
from .entitlements import RequirePro
from .llm import LLM, get_llm, require_llm
from .models import Job, JobFeedback, ProcessStatus, User
from .profile import ReferenceJob, get_profile, profile_data, save_fields
from .profile_api import ProposalPreview, create_proposal
from .proposals import item, number
from .schemas import JobOut
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
    # Retour donné pendant une période Pro sur une offre encore récente : omis en Free (son libellé la révélerait)
    recent = [feedback_out(f) for f in rows if not access.job_is_fresh(f.job)]
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


@router.post("/jobs/{job_id}/feedback")
def give_feedback(job_id: int, body: FeedbackIn, db: DB, user: Me) -> FeedbackResult:
    job = services.get_or_404(db, Job, job_id)
    if entitlements.access_for(db, user).job_is_fresh(job):
        # Offre verrouillée (Free) : la réponse (offre de référence, libellé) révélerait son titre et son entreprise
        raise entitlements.pro_required("Cette offre récente est réservée à l'offre Pro")
    text = body.text.strip() if body.text and body.text.strip() else None
    fb = JobFeedback(user_id=user.id, job_id=job.id, sentiment=body.sentiment, reasons=list(body.reasons), text=text)
    db.add(fb)
    proposal = None

    if body.sentiment == "dislike":
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
        profile = get_profile(db, user)
        data = profile_data(profile)
        if all(r.job_id != job.id for r in data.reference_jobs):
            ref = ReferenceJob(job_id=job.id, title=job.title, company=job.company.name,
                               why=_describe_reasons(body))
            save_fields(profile, data.model_copy(update={"reference_jobs": [*data.reference_jobs, ref]}),
                        {"reference_jobs"})
    db.commit()
    db.refresh(fb)
    assessments.wake()
    new = new_feedback_count(db, user)
    return FeedbackResult(feedback=feedback_out(fb), job=services.get_job(db, user, job.id), new_feedback=new,
                          suggest_synthesis=new >= SYNTHESIS_THRESHOLD, proposal=proposal)
