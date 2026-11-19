"""Offres Free / Pro : le SEUL endroit qui décide du plan d'un utilisateur, de ses limites et de ce qu'il voit.

- plan_of(db, user) : « free » ou « pro », calculé à partir de la table subscriptions (écrite par le webhook
  Stripe, voir billing.py). Pas d'essai gratuit.
- PLAN_LIMITS : toutes les limites, à ajuster ici sans toucher au reste.
- usage / check_quota : quotas calculés sur les tables existantes (pas de table de compteurs).
- access_for : contexte de lecture (fenêtre des offres récentes, notes visibles…) utilisé par services.job_out
  et services.company_out pour MASQUER côté serveur. Une donnée réservée au Pro n'est jamais envoyée à un
  utilisateur Free : elle est remplacée par un objet Locked dont le texte est factice (lock()).

BILLING_ENABLED=false (défaut) : tout le monde est Pro (dev local, tests existants).
PRO_USER_EMAILS : comptes toujours Pro (séparés par des virgules).
"""

import os
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import (
    ACTIVE_STATUSES,
    Application,
    ChatConversation,
    ChatMessage,
    Company,
    Job,
    JobAssessment,
    ProcessStatus,
    ProfileImport,
    Subscription,
    User,
)
from .schemas import Locked
from .users import current_user

# None = illimité. Booléens = fonction ouverte ou non.
PLAN_LIMITS: dict[str, dict[str, Any]] = {
    "free": {
        "fresh_delay_days": 7,  # offres de moins de N jours (publication, sinon ajout) : floutées
        "top_scores_per_week": 10,  # notes visibles : les N mieux notées parmi les évaluations de la semaine
        "assessments_per_week": 20,  # évaluations Gemini en tâche de fond (7 jours glissants)
        "assessment_priority": 1,  # ordre de passage dans la tâche de fond (0 = en premier)
        "active_processes": 10,  # process « À regarder » + en cours
        "imports_total": 1,  # imports de profil (LinkedIn, PDF ou texte), au total
        "profile_analysis": False,  # /api/profile/analyze
        "rules_synthesis": False,  # /api/profile/rules/synthesize
        "chat_per_month": 10,  # messages à l'assistant par mois civil
        "digest_email": False,  # (plus tard) e-mail récapitulatif
    },
    "pro": {
        "fresh_delay_days": 0,
        "top_scores_per_week": None,
        "assessments_per_week": None,
        "assessment_priority": 0,
        "active_processes": None,
        "imports_total": None,
        "profile_analysis": True,
        "rules_synthesis": True,
        "chat_per_month": 300,  # garde-fou contre les abus
        "digest_email": True,
        # Prix affichés (TTC). Les montants facturés sont ceux des prix Stripe (STRIPE_PRICE_MONTHLY / _PASS_3M).
        "price_monthly_label": "15 € TTC / mois",
        "price_pass_3m_label": "39 € TTC pour 3 mois",
        "pass_days": 90,  # durée d'un Pass (prolongée si un Pass est racheté avant la fin)
    },
}

PAST_DUE_GRACE = timedelta(days=3)  # abonnement impayé : encore Pro pendant ce délai (mise à jour de la carte)
PASS_DURATION = timedelta(days=PLAN_LIMITS["pro"]["pass_days"])
RECENT_FUNDING_DAYS = 183  # « levées récentes » du bandeau Entreprises

# Statuts qui comptent dans le quota de process (les statuts terminaux restent toujours possibles)
PROCESS_QUOTA_STATUSES = ACTIVE_STATUSES | {ProcessStatus.a_postuler}

# Quota -> clé de PLAN_LIMITS
QUOTAS = {
    "chat": "chat_per_month",
    "imports": "imports_total",
    "processes": "active_processes",
    "assessments": "assessments_per_week",
}
QUOTA_MESSAGES = {
    "chat": "messages à l'assistant ce mois-ci",
    "imports": "import de profil",
    "processes": "process actifs",
    "assessments": "évaluations cette semaine",
}


def _env_bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def billing_enabled() -> bool:
    """Lu à chaque appel : les tests changent la variable avec monkeypatch."""
    return _env_bool("BILLING_ENABLED")


def pro_emails() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("PRO_USER_EMAILS", "").split(",") if e.strip()}


def now() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite renvoie des dates naïves (UTC) : on les rend comparables."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# --- Plan ---------------------------------------------------------------------------------------


def _subscriptions(db: Session, user: User) -> list[Subscription]:
    return list(db.scalars(select(Subscription).where(Subscription.user_id == user.id)))


def grants_pro(s: Subscription, at: datetime) -> bool:
    end = aware(s.current_period_end)
    if s.kind == "pass":
        return s.status == "paid" and end is not None and end > at
    if s.status == "active":
        return True
    if s.status == "past_due" and s.past_due_since and aware(s.past_due_since) > at - PAST_DUE_GRACE:
        return True
    # Résilié à la fin de la période : Pro jusqu'à cette date
    return bool(s.cancel_at_period_end and end and end > at
                and s.status not in ("canceled", "incomplete", "incomplete_expired", "unpaid"))


def _forced_pro(user: User) -> bool:
    return not billing_enabled() or bool(user.email and user.email.strip().lower() in pro_emails())


def plan_of(db: Session, user: User) -> str:
    if _forced_pro(user):
        return "pro"
    at = now()
    return "pro" if any(grants_pro(s, at) for s in _subscriptions(db, user)) else "free"


def pro_until(db: Session, user: User) -> datetime | None:
    """Date de fin la plus lointaine parmi les droits en cours (None si aucun, ou Pro « forcé »)."""
    at = now()
    ends = [aware(s.current_period_end) for s in _subscriptions(db, user)
            if grants_pro(s, at) and s.current_period_end is not None]
    return max(ends) if ends else None


def limits(plan: str) -> dict[str, Any]:
    return PLAN_LIMITS[plan]


# --- Usage et quotas -------------------------------------------------------------------------------


def _month_start(at: datetime) -> datetime:
    return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def week_start(at: datetime | None = None) -> datetime:
    return (at or now()) - timedelta(days=7)


def usage_of(db: Session, user: User, kind: str) -> int:
    at = now()
    match kind:
        case "chat":
            return db.scalar(
                select(func.count(ChatMessage.id)).join(ChatConversation)
                .where(ChatConversation.user_id == user.id, ChatMessage.role == "user",
                       ChatMessage.created_at >= _month_start(at))
            ) or 0
        case "imports":
            return db.scalar(select(func.count(ProfileImport.id)).where(
                ProfileImport.user_id == user.id, ProfileImport.kind == "import")) or 0
        case "processes":
            return db.scalar(select(func.count(Application.id)).where(
                Application.user_id == user.id, Application.status.in_(PROCESS_QUOTA_STATUSES))) or 0
        case "assessments":
            # Évaluations par Gemini seulement (les exclusions par règles et les notes manuelles sont gratuites)
            return db.scalar(select(func.count(JobAssessment.id)).where(
                JobAssessment.user_id == user.id, JobAssessment.assessed_at >= week_start(at),
                JobAssessment.model.not_in(("regles", "manuel")))) or 0
    raise ValueError(kind)


def usage(db: Session, user: User) -> dict[str, int]:
    return {kind: usage_of(db, user, kind) for kind in QUOTAS}


class PlanError(HTTPException):
    """402 : fonction réservée au Pro, ou quota du plan Free atteint. detail = {code, message, …}."""

    def __init__(self, detail: dict):
        super().__init__(402, detail)
        self.message = detail.get("message", "Réservé à l'offre Pro")


def pro_required(message: str = "Réservé à l'offre Pro") -> PlanError:
    return PlanError({"code": "pro_required", "message": message})


def remaining(db: Session, user: User, kind: str, plan: str | None = None) -> int | None:
    """Unités restantes pour ce quota (None = illimité)."""
    limit = limits(plan or plan_of(db, user))[QUOTAS[kind]]
    return None if limit is None else max(0, limit - usage_of(db, user, kind))


def check_quota(db: Session, user: User, kind: str) -> None:
    plan = plan_of(db, user)
    limit = limits(plan)[QUOTAS[kind]]
    if limit is None:
        return
    used = usage_of(db, user, kind)
    if used >= limit:
        what = QUOTA_MESSAGES[kind]
        prefix = "Limite Free atteinte" if plan == "free" else "Limite atteinte"
        raise PlanError({"code": "quota", "kind": kind, "limit": limit, "used": used, "plan": plan,
                         "message": f"{prefix} ({used}/{limit} {what})"})


def check_process_change(db: Session, user: User, before: ProcessStatus | None, after: ProcessStatus) -> None:
    """Quota des process actifs : seulement quand un process ENTRE dans les statuts comptés
    (un passage à un statut terminal, ou d'un statut actif à un autre, reste toujours possible)."""
    if after in PROCESS_QUOTA_STATUSES and before not in PROCESS_QUOTA_STATUSES:
        check_quota(db, user, "processes")


def RequirePro(feature: str | None = None):  # noqa: N802 (dépendance FastAPI, nommée comme un type)
    """Dépendance FastAPI : 402 {code: pro_required} si le plan n'a pas accès.

    RequirePro() : plan Pro exigé ; RequirePro("profile_analysis") : selon le drapeau de PLAN_LIMITS.
    """

    def dependency(db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]) -> None:
        plan = plan_of(db, user)
        allowed = plan == "pro" if feature is None else bool(limits(plan)[feature])
        if not allowed:
            raise pro_required()

    return Depends(dependency)


# --- Contexte de lecture (masquage) -----------------------------------------------------------------


def _ref_date(published_on: date | None, created_at: datetime | None) -> datetime:
    if published_on is not None:
        return datetime(published_on.year, published_on.month, published_on.day, tzinfo=UTC)
    return aware(created_at) or now()


def job_date(job: Job) -> datetime:
    """Date de référence d'une offre : publication, sinon ajout dans Mire."""
    return _ref_date(job.published_on, job.created_at)


@dataclass
class Access:
    """Ce que l'utilisateur courant peut voir. full = Pro (rien n'est masqué)."""

    plan: str = "pro"
    fresh_cutoff: datetime | None = None  # offres plus récentes : verrouillées
    top_ids: set[int] = field(default_factory=set)  # offres dont la note est visible (Free)
    locked_company_ids: set[int] = field(default_factory=set)  # entreprises entièrement floutées (Free)

    @property
    def full(self) -> bool:
        return self.plan == "pro"

    def job_is_fresh(self, job: Job) -> bool:
        return self.fresh_cutoff is not None and job_date(job) > self.fresh_cutoff

    def company_is_locked(self, company_id: int) -> bool:
        return company_id in self.locked_company_ids


FULL_ACCESS = Access()


def access_for(db: Session, user: User) -> Access:
    plan = plan_of(db, user)
    if plan == "pro":
        return FULL_ACCESS
    lim = limits(plan)
    at = now()
    access = Access(plan=plan, fresh_cutoff=at - timedelta(days=lim["fresh_delay_days"]))

    # Entreprises entièrement floutées : toutes leurs offres sont récentes (ou aucune offre et ajoutée récemment)
    rows = db.execute(select(Job.company_id, Job.published_on, Job.created_at)).all()
    visible: set[int] = set()
    with_jobs: set[int] = set()
    for company_id, published_on, created_at in rows:
        with_jobs.add(company_id)
        if _ref_date(published_on, created_at) <= access.fresh_cutoff:
            visible.add(company_id)
    access.locked_company_ids = with_jobs - visible
    for cid, created_at in db.execute(select(Company.id, Company.created_at)).all():
        if cid not in with_jobs and (aware(created_at) or at) > access.fresh_cutoff:
            access.locked_company_ids.add(cid)

    # Notes visibles : les N mieux notées parmi les offres (visibles) évaluées cette semaine
    n = lim["top_scores_per_week"]
    if n:
        scored = db.execute(
            select(JobAssessment.job_id, JobAssessment.score, Job.published_on, Job.created_at)
            .join(Job, Job.id == JobAssessment.job_id)
            .where(JobAssessment.user_id == user.id, JobAssessment.score.is_not(None),
                   JobAssessment.assessed_at >= week_start(at))
        ).all()
        candidates = [(float(score), job_id) for job_id, score, published_on, created_at in scored
                      if _ref_date(published_on, created_at) <= access.fresh_cutoff]
        candidates.sort(key=lambda t: (-t[0], t[1]))
        access.top_ids = {job_id for _, job_id in candidates[:n]}
    return access


# --- Texte factice ----------------------------------------------------------------------------------

_rng = random.Random()  # jamais initialisé avec la vraie valeur
_LOREM = ("lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore "
          "et dolore magna aliqua enim ad minim veniam quis nostrud exercitation ullamco laboris nisi aliquip "
          "ex ea commodo consequat duis aute irure in reprehenderit voluptate velit esse cillum fugiat nulla "
          "pariatur excepteur sint occaecat cupidatat non proident sunt culpa qui officia deserunt mollit anim "
          "id est laborum").split()


def placeholder(length: int) -> str:
    """Texte factice de longueur voisine (arrondie) : seule la longueur de la vraie valeur est utilisée."""
    step = 10 if length < 200 else 100
    target = max(6, min(1500, round(length / step) * step))
    words: list[str] = []
    size = 0
    while size < target:
        w = _rng.choice(_LOREM)
        words.append(w)
        size += len(w) + 1
    return " ".join(words)[:target].strip().capitalize()


def lock(value: Any) -> Locked | None:
    """Valeur réservée au Pro -> Locked (None reste None : rien à flouter)."""
    if value is None or value == "" or value == []:
        return None if value is None else value
    if isinstance(value, bool):
        return Locked(placeholder="Oui")
    if isinstance(value, int | float | Decimal):
        return Locked(placeholder=f"{_rng.randint(1, 99)},{_rng.randint(0, 9)}")
    if isinstance(value, datetime | date):
        return Locked(placeholder=f"{_rng.randint(1, 28):02d}/{_rng.randint(1, 12):02d}/20{_rng.randint(20, 29)}")
    if isinstance(value, list):
        return Locked(placeholder=placeholder(sum(len(str(v)) + 2 for v in value)))
    return Locked(placeholder=placeholder(len(str(value))))


# --- Résumé pour /api/me --------------------------------------------------------------------------------


def locked_counts(db: Session, plan: str) -> tuple[int, int]:
    """Bandeaux du plan Free : (offres récentes verrouillées, levées récentes floutées)."""
    if plan == "pro":
        return 0, 0
    at = now()
    cutoff = at - timedelta(days=limits(plan)["fresh_delay_days"])
    jobs = sum(1 for p, c in db.execute(select(Job.published_on, Job.created_at)).all() if _ref_date(p, c) > cutoff)
    fundings = db.scalar(select(func.count(Company.id)).where(
        Company.funding_announced_on >= (at - timedelta(days=RECENT_FUNDING_DAYS)).date())) or 0
    return jobs, fundings


def billing_summary(db: Session, user: User) -> dict:
    plan = plan_of(db, user)
    at = now()
    granting = [s for s in _subscriptions(db, user) if grants_pro(s, at)]
    current = max(granting, key=lambda s: aware(s.current_period_end) or at, default=None)
    locked_jobs, locked_fundings = locked_counts(db, plan)
    pro = PLAN_LIMITS["pro"]
    return {
        "enabled": billing_enabled(),
        "plan": plan,
        "pro_until": pro_until(db, user),
        "cancel_at_period_end": bool(current and current.kind == "subscription" and current.cancel_at_period_end),
        "kind": current.kind if current else None,
        "past_due": any(s.status == "past_due" for s in granting),
        "limits": limits(plan),
        "pro_limits": pro,
        "usage": usage(db, user),
        "locked_jobs": locked_jobs,
        "locked_fundings": locked_fundings,
        "prices": {"monthly": pro["price_monthly_label"], "pass_3m": pro["price_pass_3m_label"]},
    }
