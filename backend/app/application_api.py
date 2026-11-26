"""API de la page Candidature : préparer, relire, modifier, marquer prête, déclarer envoyée.

Mire n'envoie RIEN : ces routes préparent un formulaire que l'utilisateur copie-colle lui-même sur le site de
l'entreprise. Toutes les données sont celles de l'utilisateur connecté (process, brouillon, PDF) : un identifiant
d'offre ou de fichier d'un autre utilisateur répond 404.

POST  /api/jobs/{id}/application/prepare             crée le process si besoin (statut inchangé), met en file la
                                                     lecture du formulaire puis la préparation ; {refresh: true} :
                                                     « Tout recharger » (relit le formulaire, régénère, compte)
GET   /api/jobs/{id}/application/draft               formulaire + réponses, CV, lettre, statut, avertissements
PATCH /api/jobs/{id}/application/draft               {answers: [{field_id, value}], cv_text, cover_letter, want_letter}
POST  /api/jobs/{id}/application/draft/fields/{field_id}/regenerate   {instruction} (field_id « cover_letter » : la lettre)
GET   /api/jobs/{id}/application/files/{cv|lettre}   PDF (attachment)
POST  /api/jobs/{id}/application/ready               statut « Prête à envoyer » (refusé tant qu'il manque un champ)
POST  /api/jobs/{id}/application/sent                statut « Postulé » + instantané de ce qui a été envoyé
GET   /api/applications/{id}/events/{event_id}/snapshot   instantané envoyé (Mes process)
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, undefer

from . import application_prep as prep
from . import apply_forms, entitlements, services, tasks
from .db import get_db
from .llm import LLM, get_llm, require_llm
from .models import (
    Application,
    ApplicationDraft,
    ApplicationEvent,
    ApplicationGeneration,
    GeneratedFile,
    Job,
    JobAssessment,
    ProcessStatus,
    User,
)
from .profile import get_profile, profile_data
from .schemas import CompanyMini
from .users import current_user

log = logging.getLogger("mire.candidature")

router = APIRouter(prefix="/api/jobs/{job_id}/application", dependencies=[Depends(current_user)])
events_router = APIRouter(prefix="/api/applications", dependencies=[Depends(current_user)])

DB = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]
Gemini = Annotated[LLM | None, Depends(get_llm)]

LOW_SCORE = 5.0
STALE_PREPARATION = timedelta(minutes=10)  # préparation restée « en cours » (worker interrompu) : relançable
SENDABLE = {ProcessStatus.nouveau, ProcessStatus.a_postuler, ProcessStatus.prete}


# --- Schémas ---------------------------------------------------------------------------------------


class PrepareIn(BaseModel):
    refresh: bool = False  # « Tout recharger »
    want_letter: bool | None = None  # lettre demandée même si le formulaire n'en a pas


AnswerValue = str | list[str] | bool | None


class AnswerPatch(BaseModel):
    field_id: Annotated[str, Field(max_length=100)]
    value: AnswerValue = None


class DraftPatch(BaseModel):
    answers: list[AnswerPatch] = Field(default_factory=list, max_length=100)
    cv_text: Annotated[str | None, Field(default=None, max_length=20_000)] = None
    cover_letter: Annotated[str | None, Field(default=None, max_length=6_000)] = None
    want_letter: bool | None = None


class RegenerateIn(BaseModel):
    instruction: Annotated[str, Field(default="", max_length=prep.INSTRUCTION_CHARS)] = ""


# --- Chargement (toujours limité à l'utilisateur connecté) ------------------------------------------


def _job(db: Session, user: User, job_id: int) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id).options(selectinload(Job.company)))
    if job is None:
        raise HTTPException(404, "Poste introuvable")
    if entitlements.access_for(db, user).job_is_locked(job):
        raise entitlements.pro_required("Offre réservée à l'offre Pro")
    return job


def _application(db: Session, user: User, job_id: int) -> Application | None:
    return db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job_id).options(
        selectinload(Application.job).selectinload(Job.company), selectinload(Application.draft),
        selectinload(Application.events)))


def _draft(db: Session, user: User, job_id: int) -> tuple[Application, ApplicationDraft]:
    app = _application(db, user, job_id)
    if app is None or app.draft is None:
        raise HTTPException(404, "Aucune candidature préparée pour ce poste")
    return app, app.draft


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


# --- Sortie ----------------------------------------------------------------------------------------


def _score(db: Session, user: User, job_id: int) -> float | None:
    a = db.scalar(select(JobAssessment).where(JobAssessment.user_id == user.id, JobAssessment.job_id == job_id))
    if a is None:
        return None
    value = a.manual_score if a.manual_score is not None else a.score
    return float(value) if value is not None else None


def draft_out(db: Session, user: User, app: Application, draft: ApplicationDraft) -> dict:
    job = app.job
    form = apply_forms.get_form(db, job.id)
    fields = draft.form_snapshot or apply_forms.as_dicts(apply_forms.form_fields(form))
    answers = {a["field_id"]: a for a in draft.answers or []}
    files = {f.kind: {"filename": f.filename, "created_at": f.created_at}
             for f in db.scalars(select(GeneratedFile).where(GeneratedFile.application_id == app.id))}
    base = prep.base_cv(profile_data(get_profile(db, user)))
    all_sources = base.sources()
    used = {s for a in answers.values() for s in a.get("sources") or []}
    for e in (draft.cv or {}).get("experiences", []):
        used |= {s for b in e.get("bullets", []) for s in b.get("source_ids", [])}
    plan = entitlements.plan_of(db, user)
    score = _score(db, user, job.id)
    return {
        "job": {"id": job.id, "title": job.title, "url": job.url, "location": job.location, "score": score,
                "company": CompanyMini.model_validate(job.company).model_dump(),
                "closed": job.closed_at is not None},
        "application": {"id": app.id, "status": app.status.value, "applied_on": app.applied_on},
        "status": draft.status,
        "language": draft.language,
        "generated_at": draft.generated_at,
        "error": draft.error,
        "apply_url": (form.apply_url if form else None) or job.url,
        "form": {"source": form.source if form else None, "ats": form.ats if form else None,
                 "fetched_at": form.fetched_at if form else None, "error": form.error if form else None},
        "fields": [{**f, "answer": answers.get(f["field_id"]) or prep.answer(f["field_id"])} for f in fields],
        "cv": draft.cv,
        "cv_text": draft.cv_text,
        "cover_letter": draft.cover_letter,
        "want_letter": draft.want_letter,
        "files": files,
        "warnings": draft.warnings or [],
        "missing": prep.missing_fields(fields, draft.answers or []) if draft.status != "en_preparation" else [],
        "sources": {s: all_sources[s] for s in used if s in all_sources},
        "usage": {"model": draft.model, "tokens_in": draft.tokens_in, "tokens_out": draft.tokens_out},
        "low_score": score is not None and score < LOW_SCORE,
        "remaining": {"prepare": entitlements.remaining(db, user, "prepare", plan),
                      "regenerate": entitlements.remaining(db, user, "regenerate", plan)},
    }


# --- Préparer ------------------------------------------------------------------------------------------


def _log_generation(db: Session, user: User, app: Application, kind: str, usage: dict | None = None) -> None:
    usage = usage or {}
    db.add(ApplicationGeneration(user_id=user.id, application_id=app.id, kind=kind, model=usage.get("model"),
                                 tokens_in=usage.get("tokens_in", 0), tokens_out=usage.get("tokens_out", 0)))


@router.post("/prepare")
def prepare(job_id: int, db: DB, user: Me, body: PrepareIn | None = None) -> dict:
    """Prépare la candidature (tâches de fond) et renvoie le brouillon (statut en_preparation)."""
    body = body or PrepareIn()
    job = _job(db, user, job_id)
    app = _application(db, user, job_id)
    draft = app.draft if app else None
    running = draft is not None and draft.status == "en_preparation" and (
        _aware(draft.updated_at) or datetime.now(UTC)) > datetime.now(UTC) - STALE_PREPARATION
    if draft is not None and (running or not body.refresh):
        if body.want_letter and not draft.want_letter and not running:
            body.refresh = True  # lettre demandée après coup : nouvelle préparation
        else:
            return draft_out(db, user, app, draft)
    entitlements.check_generation(db, user, "prepare")
    if app is None:
        app = Application(user_id=user.id, job_id=job.id, status=ProcessStatus.nouveau)  # statut inchangé
        db.add(app)
        db.flush()
    if draft is None:
        draft = ApplicationDraft(application_id=app.id, form_snapshot=[], answers=[], warnings=[], language="fr",
                                 want_letter=bool(body.want_letter))
        db.add(draft)
        app.draft = draft
    elif body.want_letter is not None:
        draft.want_letter = body.want_letter
    draft.status, draft.error = "en_preparation", None
    draft.updated_at = datetime.now(UTC)
    _log_generation(db, user, app, "prepare")
    db.commit()
    form = apply_forms.get_form(db, job.id)
    if apply_forms.is_fresh(form) and not body.refresh:
        tasks.enqueue(db, "prepare_application", {"application_id": app.id}, dedup_key=f"prepare_application:{app.id}")
    else:
        tasks.enqueue(db, "fetch_apply_form", {"job_id": job.id, "application_id": app.id, "force": body.refresh},
                      dedup_key=f"fetch_apply_form:{job.id}:{app.id}")
    log.info("Préparation de candidature mise en file (process %s)", app.id, extra={"application_id": app.id})
    db.refresh(app)
    return draft_out(db, user, app, app.draft)


@router.get("/draft")
def read_draft(job_id: int, db: DB, user: Me) -> dict:
    app, draft = _draft(db, user, job_id)
    return draft_out(db, user, app, draft)


# --- Modifier ------------------------------------------------------------------------------------------


def _check_value(field: dict, value: AnswerValue) -> AnswerValue:
    if isinstance(value, str):
        value = value.strip()
        if len(value) > 20_000:
            raise HTTPException(422, "Réponse trop longue")
    kind, options = field.get("kind"), field.get("options") or []
    if kind == "select" and value not in (None, "") and options and value not in options:
        raise HTTPException(422, f"Choix inconnu pour « {field.get('label')} »")
    if kind == "multiselect" and value not in (None, ""):
        values = value if isinstance(value, list) else [value]
        if options and any(v not in options for v in values):
            raise HTTPException(422, f"Choix inconnu pour « {field.get('label')} »")
        value = values
    if field.get("max_length") and isinstance(value, str) and len(value) > field["max_length"]:
        raise HTTPException(422, f"« {field.get('label')} » : {field['max_length']} caractères au plus")
    return value


def _mirror(draft: ApplicationDraft, category: str, text: str | None) -> None:
    """Champs « coller le texte » (CV, lettre) non modifiés à la main : suivent le texte du CV / de la lettre."""
    fields = {f["field_id"]: f for f in draft.form_snapshot or []}
    answers = []
    for a in draft.answers or []:
        f = fields.get(a["field_id"]) or {}
        if f.get("category") == category and f.get("kind") == "textarea" and not a.get("edited"):
            a = {**a, "value": text, "origin": "genere" if text else "a_toi"}
        answers.append(a)
    draft.answers = answers


@router.patch("/draft")
def update_draft(job_id: int, body: DraftPatch, db: DB, user: Me) -> dict:
    """Modifications de l'utilisateur, enregistrées au fil de l'eau (edited = true)."""
    app, draft = _draft(db, user, job_id)
    if draft.status == "en_preparation":
        raise HTTPException(409, "Préparation en cours : attends la fin pour modifier")
    fields = {f["field_id"]: f for f in draft.form_snapshot or []}
    answers = {a["field_id"]: dict(a) for a in draft.answers or []}
    for patch in body.answers:
        field = fields.get(patch.field_id)
        if field is None:
            raise HTTPException(404, f"Champ inconnu : {patch.field_id}")
        value = _check_value(field, patch.value)
        current = answers.get(patch.field_id) or prep.answer(patch.field_id)
        answers[patch.field_id] = {**current, "value": value, "edited": True}
    if body.answers:
        draft.answers = [answers.get(f["field_id"]) or prep.answer(f["field_id"]) for f in draft.form_snapshot]
    render = False
    if body.cv_text is not None and body.cv_text != draft.cv_text:
        draft.cv_text = body.cv_text.strip() or None
        _mirror(draft, "cv", draft.cv_text)
        render = True
    if body.cover_letter is not None and body.cover_letter != draft.cover_letter:
        draft.cover_letter = body.cover_letter.strip() or None
        _mirror(draft, "lettre", draft.cover_letter)
        render = True
    if body.want_letter is not None:
        draft.want_letter = body.want_letter
    db.commit()
    if render:
        tasks.enqueue(db, "render_application_files", {"application_id": app.id},
                      dedup_key=f"render_application_files:{app.id}")
    return draft_out(db, user, app, draft)


REGENERABLE = {"question", "autre", "lettre"}


@router.post("/draft/fields/{field_id}/regenerate")
def regenerate_field(job_id: int, field_id: str, body: RegenerateIn, db: DB, user: Me, llm: Gemini) -> dict:
    """Nouvelle proposition pour une réponse générée (ou la lettre : field_id « cover_letter »)."""
    app, draft = _draft(db, user, job_id)
    if draft.status == "en_preparation":
        raise HTTPException(409, "Préparation en cours")
    fields = {f["field_id"]: f for f in draft.form_snapshot or []}
    if field_id != "cover_letter":
        field = fields.get(field_id)
        if field is None:
            raise HTTPException(404, "Champ inconnu")
        if field.get("category") not in REGENERABLE or field.get("kind") not in ("text", "textarea", "select"):
            raise HTTPException(400, "Ce champ n'est pas généré : il se remplit avec ton profil ou à la main")
    entitlements.check_generation(db, user, "regenerate")
    llm = require_llm(llm)
    try:
        result, usage = prep.regenerate(db, app, draft, field_id, body.instruction, llm)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001  (contenu personnel : seul le type d'erreur est journalisé)
        log.warning("Régénération impossible (process %s) : %s", app.id, type(exc).__name__)
        raise HTTPException(502, "Gemini n'a pas pu proposer une nouvelle réponse, réessaie dans un instant.") from None
    if field_id == "cover_letter":
        if result["cover_letter"]:
            draft.cover_letter = result["cover_letter"]
            _mirror(draft, "lettre", draft.cover_letter)
        draft.warnings = [w for w in draft.warnings or [] if w.get("kind") != "lettre"] + result["warnings"]
    else:
        draft.answers = [result if a["field_id"] == field_id else a for a in draft.answers or []]
    draft.tokens_in = (draft.tokens_in or 0) + usage.get("tokens_in", 0)
    draft.tokens_out = (draft.tokens_out or 0) + usage.get("tokens_out", 0)
    _log_generation(db, user, app, "regenerate", usage)
    db.commit()
    if field_id == "cover_letter":
        tasks.enqueue(db, "render_application_files", {"application_id": app.id},
                      dedup_key=f"render_application_files:{app.id}")
    return draft_out(db, user, app, draft)


# --- Fichiers ------------------------------------------------------------------------------------------


@router.get("/files/{kind}")
def download_file(job_id: int, kind: str, db: DB, user: Me) -> Response:
    if kind not in ("cv", "lettre"):
        raise HTTPException(404, "Fichier inconnu")
    file = db.scalar(select(GeneratedFile).join(Application, Application.id == GeneratedFile.application_id)
                     .where(Application.job_id == job_id, Application.user_id == user.id,
                            GeneratedFile.user_id == user.id, GeneratedFile.kind == kind)
                     .options(undefer(GeneratedFile.content)).order_by(GeneratedFile.id.desc()).limit(1))
    if file is None:
        raise HTTPException(404, "PDF pas encore disponible")
    return Response(file.content, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{file.filename}"', "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff"})


# --- Statuts : prête, envoyée ----------------------------------------------------------------------------


@router.post("/ready")
def mark_ready(job_id: int, db: DB, user: Me) -> dict:
    """« Marquer prête à envoyer » : la SEULE façon de poser le statut prete (action de l'utilisateur)."""
    app, draft = _draft(db, user, job_id)
    if draft.status == "en_preparation":
        raise HTTPException(409, "Préparation en cours")
    if app.status not in SENDABLE:
        raise HTTPException(409, "Candidature déjà envoyée")
    missing = prep.missing_fields(draft.form_snapshot or [], draft.answers or [])
    if missing:
        raise HTTPException(409, {"code": "incomplete", "missing": missing,
                                  "message": "Champs obligatoires à compléter : "
                                             + ", ".join(m["label"] for m in missing)})
    entitlements.check_process_change(db, user, app.status, ProcessStatus.prete)
    services.apply_status(app, ProcessStatus.prete, note="Candidature prête à envoyer")
    draft.status = "prete"
    db.commit()
    return draft_out(db, user, app, draft)


def snapshot_of(app: Application, draft: ApplicationDraft, files: list[GeneratedFile], apply_url: str | None) -> dict:
    """Ce qui a été envoyé, pour préparer l'entretien : réponses (intitulé + valeur), CV et lettre (texte)."""
    answers = {a["field_id"]: a for a in draft.answers or []}
    return {
        "sent_at": datetime.now(UTC).isoformat(),
        "job": {"id": app.job.id, "title": app.job.title, "company": app.job.company.name},
        "apply_url": apply_url,
        "answers": [{"field_id": f["field_id"], "label": f["label"], "category": f.get("category"),
                     "value": (answers.get(f["field_id"]) or {}).get("value")} for f in draft.form_snapshot or []],
        "cv_text": draft.cv_text,
        "cover_letter": draft.cover_letter,
        "files": [{"id": f.id, "kind": f.kind, "filename": f.filename,
                   "created_at": f.created_at.isoformat() if f.created_at else None} for f in files],
    }


@router.post("/sent")
def mark_sent(job_id: int, db: DB, user: Me) -> dict:
    """« J'ai envoyé ma candidature » : statut Postulé, date de candidature, instantané dans l'historique."""
    app, draft = _draft(db, user, job_id)
    if app.status not in SENDABLE:
        raise HTTPException(409, "Candidature déjà envoyée")
    entitlements.check_process_change(db, user, app.status, ProcessStatus.postule)
    form = apply_forms.get_form(db, job_id)
    files = list(db.scalars(select(GeneratedFile).where(GeneratedFile.application_id == app.id)))
    snapshot = snapshot_of(app, draft, files, (form.apply_url if form else None) or app.job.url)
    services.apply_status(app, ProcessStatus.postule, note="Candidature envoyée (instantané enregistré)",
                          snapshot=snapshot)
    draft.status = "envoyee"
    db.commit()
    return draft_out(db, user, app, draft)


@events_router.get("/{app_id}/events/{event_id}/snapshot")
def read_snapshot(app_id: int, event_id: int, db: DB, user: Me) -> dict[str, Any]:
    event = db.scalar(select(ApplicationEvent).join(Application).where(
        ApplicationEvent.id == event_id, ApplicationEvent.application_id == app_id, Application.user_id == user.id)
        .options(undefer(ApplicationEvent.snapshot)))
    if event is None or event.snapshot is None:
        raise HTTPException(404, "Instantané introuvable")
    return event.snapshot


# --- Lecture pour l'assistant (outil get_application_draft) -----------------------------------------------


def draft_summary(db: Session, user: User, job_id: int) -> dict:
    """Candidature préparée, en lecture, pour l'assistant (chat / MCP) : réponses, CV et lettre en texte."""
    app = _application(db, user, job_id)
    if app is None or app.draft is None:
        raise services.ServiceError(404, "Aucune candidature préparée pour ce poste")
    draft = app.draft
    answers = {a["field_id"]: a for a in draft.answers or []}
    return {
        "job_id": job_id, "statut_candidature": draft.status, "statut_process": app.status.value,
        "reponses": [{"champ": f["label"], "valeur": (answers.get(f["field_id"]) or {}).get("value"),
                      "origine": (answers.get(f["field_id"]) or {}).get("origin")} for f in draft.form_snapshot or []],
        "cv": (draft.cv_text or "")[:6000] or None,
        "lettre": draft.cover_letter,
        "manquants": [m["label"] for m in prep.missing_fields(draft.form_snapshot or [], draft.answers or [])],
    }

