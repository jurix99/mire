"""Droits RGPD de l'utilisateur sur ses données : export (portabilité, accès) et suppression du compte (effacement).

- GET /api/me/export : toutes les données personnelles de l'utilisateur, en JSON (fichier téléchargé).
- DELETE /api/me : supprime le compte et TOUT ce qui s'y rattache (profil, process, candidatures préparées et
  PDF générés, retours, évaluations, conversations, imports, droits Pro). Refusé (409) tant qu'un abonnement se renouvelle : il faut d'abord le
  résilier, sinon Stripe continuerait de prélever. Les données de facturation conservées par Stripe (obligation
  comptable) ne sont pas dans Mire.
- purge_inactive : comptes sans connexion depuis INACTIVE_YEARS ans, supprimés par le job quotidien (durée
  de conservation annoncée dans la politique de confidentialité).

Les données partagées (entreprises, offres, sources) ne sont pas des données personnelles : elles restent.
"""

import base64
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, undefer

from . import entitlements
from .db import get_db
from .models import (
    Application,
    ApplicationDraft,
    ApplicationEvent,
    ApplicationGeneration,
    ChatAction,
    ChatConversation,
    ChatMessage,
    CompanyFeedback,
    GeneratedFile,
    JobAssessment,
    JobFeedback,
    ProfileImport,
    Source,
    Subscription,
    User,
    UserProfile,
)
from .users import current_user

log = logging.getLogger("mire.account")

INACTIVE_YEARS = 3

router = APIRouter(prefix="/api/me")
DB = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]


def _row(obj) -> dict:
    return {c.key: getattr(obj, c.key) for c in obj.__table__.columns if c.key not in ("embedding",)}


def _file_row(f: GeneratedFile) -> dict:
    """PDF générés (CV adapté, lettre) : contenu inclus, encodé en base64."""
    return {**{k: v for k, v in _row(f).items() if k != "content"}, "content_base64": base64.b64encode(f.content).decode(),
            "media_type": "application/pdf"}


def export_data(db: Session, user: User) -> dict:
    """Données personnelles de l'utilisateur, table par table (colonnes brutes, sans les embeddings)."""
    apps = db.scalars(select(Application).where(Application.user_id == user.id)).all()
    convs = db.scalars(select(ChatConversation).where(ChatConversation.user_id == user.id)).all()

    def rows(model, *where):
        return [_row(r) for r in db.scalars(select(model).where(*where))]

    app_ids = [a.id for a in apps] or [-1]
    files = db.scalars(select(GeneratedFile).where(GeneratedFile.user_id == user.id)
                       .options(undefer(GeneratedFile.content))).all()
    return {
        "exported_at": datetime.now(UTC),
        "user": _row(user),
        "profile": rows(UserProfile, UserProfile.user_id == user.id),
        "applications": [_row(a) for a in apps],
        "application_events": [{**_row(e), "snapshot": e.snapshot} for e in db.scalars(
            select(ApplicationEvent).where(ApplicationEvent.application_id.in_(app_ids))
            .options(undefer(ApplicationEvent.snapshot)))],
        "application_drafts": rows(ApplicationDraft, ApplicationDraft.application_id.in_(app_ids)),
        "generated_files": [_file_row(f) for f in files],
        "application_generations": rows(ApplicationGeneration, ApplicationGeneration.user_id == user.id),
        "job_feedback": rows(JobFeedback, JobFeedback.user_id == user.id),
        "company_feedback": rows(CompanyFeedback, CompanyFeedback.user_id == user.id),
        "job_assessments": rows(JobAssessment, JobAssessment.user_id == user.id),
        "profile_imports": rows(ProfileImport, ProfileImport.user_id == user.id),
        "chat_conversations": [_row(c) for c in convs],
        "chat_messages": rows(ChatMessage, ChatMessage.conversation_id.in_([c.id for c in convs] or [-1])),
        "chat_actions": rows(ChatAction, ChatAction.user_id == user.id),
        "subscriptions": rows(Subscription, Subscription.user_id == user.id),
    }


def renewing_subscription(db: Session, user: User) -> bool:
    """Abonnement mensuel en cours qui se renouvellera (un Pass ou un abonnement résilié ne prélève plus)."""
    at = entitlements.now()
    return any(s.kind == "subscription" and not s.cancel_at_period_end and entitlements.grants_pro(s, at)
               for s in db.scalars(select(Subscription).where(Subscription.user_id == user.id)))


def delete_account(db: Session, user: User) -> None:
    """Supprime l'utilisateur et toutes ses données (suppressions explicites : ne dépend pas des ON DELETE)."""
    uid = user.id
    app_ids = select(Application.id).where(Application.user_id == uid)
    conv_ids = select(ChatConversation.id).where(ChatConversation.user_id == uid)
    db.execute(delete(ApplicationEvent).where(ApplicationEvent.application_id.in_(app_ids)))
    # Candidatures préparées et PDF générés (CV, lettre)
    db.execute(delete(GeneratedFile).where(GeneratedFile.user_id == uid))
    db.execute(delete(ApplicationDraft).where(ApplicationDraft.application_id.in_(app_ids)))
    db.execute(delete(ApplicationGeneration).where(ApplicationGeneration.user_id == uid))
    db.execute(delete(Application).where(Application.user_id == uid))
    db.execute(delete(ChatAction).where(ChatAction.user_id == uid))
    db.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(conv_ids)))
    db.execute(delete(ChatConversation).where(ChatConversation.user_id == uid))
    for model in (JobFeedback, CompanyFeedback, JobAssessment, ProfileImport, UserProfile, Subscription):
        db.execute(delete(model).where(model.user_id == uid))
    db.execute(update(Source).where(Source.added_by_user_id == uid).values(added_by_user_id=None))
    db.execute(delete(User).where(User.id == uid))
    db.commit()
    log.info("Compte supprimé", extra={"user_id": uid})


def purge_inactive(db: Session, years: int = INACTIVE_YEARS, now: datetime | None = None) -> int:
    """Supprime les comptes sans connexion depuis `years` ans (jamais un compte dont l'abonnement se renouvelle)."""
    limit = (now or datetime.now(UTC)) - timedelta(days=365 * years)
    users = db.scalars(select(User).where(User.last_login_at.is_not(None), User.last_login_at < limit)).all()
    done = 0
    for user in users:
        if not renewing_subscription(db, user):
            delete_account(db, user)
            done += 1
    return done


@router.get("/export")
def export_me(db: DB, user: Me) -> JSONResponse:
    """Vos données (droit d'accès et à la portabilité), en fichier JSON."""
    data = JSONResponse(content=_jsonable(export_data(db, user)))
    data.headers["Content-Disposition"] = f'attachment; filename="mire-mes-donnees-{date.today().isoformat()}.json"'
    data.headers["Cache-Control"] = "no-store"
    return data


@router.delete("", status_code=204)
def delete_me(request: Request, db: DB, user: Me) -> Response:
    """Suppression définitive du compte et de toutes ses données (droit à l'effacement)."""
    if renewing_subscription(db, user):
        raise HTTPException(409, "Résiliez d'abord votre abonnement Pro (Mon profil > Abonnement), puis supprimez le compte.")
    delete_account(db, user)
    request.session.clear()
    return Response(status_code=204)


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes):
        return None
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)  # Decimal, Enum…
