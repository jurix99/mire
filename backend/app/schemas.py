"""Schémas d'entrée / sortie de l'API."""

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, computed_field

from .models import ProcessStatus


def _http_url(value: str | None) -> str | None:
    """N'accepte que des URLs http(s) (pas de javascript:, data:, etc.)."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not value.lower().startswith(("http://", "https://")):
        # "dust.tt" -> "https://dust.tt", mais "javascript:..." ou "ftp://..." sont refusés
        if re.match(r"^[a-z][a-z0-9+.-]*:(?!\d)", value, re.IGNORECASE):
            raise ValueError("URL http(s) attendue")
        value = f"https://{value}"
    return value


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


HttpUrl = Annotated[str | None, Field(default=None, max_length=1000), AfterValidator(_http_url)]
Text = Annotated[str | None, Field(default=None, max_length=5000), AfterValidator(_blank_to_none)]
JobDescription = Annotated[str | None, Field(default=None, max_length=20000), AfterValidator(_blank_to_none)]
ShortText = Annotated[str | None, Field(default=None, max_length=200), AfterValidator(_blank_to_none)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Locked(BaseModel):
    """Donnée réservée au Pro : la vraie valeur n'est jamais envoyée, seulement un texte factice
    (de longueur voisine) que le front affiche flouté. Voir entitlements.lock()."""

    locked: Literal[True] = True
    placeholder: str


# --- Utilisateur -------------------------------------------------------------


class NavCounts(BaseModel):
    companies: int
    jobs: int
    processes: int


class BillingOut(BaseModel):
    """Offre de l'utilisateur (voir entitlements.billing_summary)."""

    enabled: bool  # BILLING_ENABLED : sinon tout le monde est Pro
    plan: Literal["free", "pro"]
    trial: bool = False  # Pro grâce à l'essai seulement (TRIAL_DAYS)
    trial_until: datetime | None = None
    trial_days: int = 0
    pro_until: datetime | None = None
    cancel_at_period_end: bool = False
    kind: Literal["subscription", "pass"] | None = None
    past_due: bool = False
    limits: dict[str, Any]
    pro_limits: dict[str, Any] = {}  # limites du Pro (fenêtre « Passer Pro »)
    usage: dict[str, int]
    # Chiffres des bandeaux (Free) : offres floutées et levées récentes floutées
    locked_jobs: int = 0
    locked_fundings: int = 0
    prices: dict[str, str] = {}  # libellés des prix (Pro mensuel, Pass 3 mois)


class UserOut(ORM):
    id: int
    name: str | None
    given_name: str | None
    family_name: str | None
    email: str | None
    picture: str | None
    locale: str | None = None
    created_at: datetime
    is_admin: bool = False  # peut modifier le radar partagé (voir entitlements.is_admin)


class MeOut(BaseModel):
    user: UserOut
    counts: NavCounts
    billing: BillingOut | None = None


class StatusOut(BaseModel):
    key: ProcessStatus
    label: str


# --- Classement (ranking.py) -------------------------------------------------


class MatchOut(BaseModel):
    """Pertinence pour l'utilisateur connecté. En Free, les explications ne sont pas envoyées."""

    score: int  # 0-100
    reasons: list[str] = []  # ce qui joue pour
    warnings: list[str] = []  # ce qui joue contre


class TopJob(BaseModel):
    """Une des meilleures offres ouvertes d'une entreprise."""

    id: int
    title: str
    city: str | None = None
    score: int


class Option(BaseModel):
    key: str
    label: str
    group: str | None = None


class OptionsOut(BaseModel):
    """GET /api/options : choix des filtres et du profil."""

    families: list[Option]
    sectors: list[Option]
    seniority: list[Option]
    skills: list[str]  # libellés de la liste : ceux que le classement sait comparer
    locations: list[str]


# --- Entreprises -------------------------------------------------------------


class CompanyMini(ORM):
    # Entreprise d'une offre verrouillée (Free) : id absent, nom verrouillé
    id: int | None
    name: str | Locked
    slug: str | None
    domain: str | None

    @computed_field
    @property
    def logo(self) -> str | None:
        if self.id is None or isinstance(self.name, Locked):
            return None
        return f"/api/companies/{self.id}/logo"


class CompanyOut(CompanyMini):
    """Les champs « | Locked » sont remplacés par un objet Locked pour un utilisateur Free (services.company_out)."""

    locked: bool = False  # entreprise entièrement floutée (aucune offre en clair), sauf le secteur
    website: str | Locked | None
    sector: str | None
    city: str | Locked | None
    funding_stage: str | Locked | None
    funding_amount_meur: float | Locked | None
    funding_announced_on: date | Locked | None
    investors: str | Locked | None
    description: str | Locked | None
    logo_url: str | Locked | None
    ats: str | Locked | None = None
    ats_slug: str | Locked | None = None
    careers_url: str | Locked | None = None
    headcount: str | Locked | None = None
    last_checked_at: datetime | Locked | None = None
    no_relevant_jobs_at: datetime | Locked | None = None
    jobs_count: int | Locked = 0
    # Classement pour l'utilisateur (liste et fiche d'une entreprise ; absent d'une entreprise floutée)
    match: MatchOut | None = None
    open_jobs: int = 0  # offres ouvertes visibles
    top_jobs: list[TopJob] = []
    reaction: str | None = None  # like | dislike
    hidden_reason: str | None = None


class CompanyPatch(BaseModel):
    name: Annotated[str | None, Field(default=None, min_length=1, max_length=200)]
    website: HttpUrl
    sector: ShortText
    city: ShortText
    funding_stage: ShortText
    funding_amount_meur: Annotated[float | None, Field(default=None, ge=0)]
    funding_announced_on: date | None = None
    investors: Text
    description: Text
    logo_url: HttpUrl
    ats: Annotated[str | None, Field(default=None, pattern="^(ashby|lever|greenhouse|teamtailor|recruitee|yc|autre)$")] = None
    ats_slug: Annotated[str | None, Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")] = None
    careers_url: HttpUrl
    headcount: Annotated[str | None, Field(default=None, max_length=40)] = None


class CompanyIn(CompanyPatch):
    name: Annotated[str, Field(min_length=1, max_length=200)]


# --- Postes ------------------------------------------------------------------


class JobPatch(BaseModel):
    company_id: int | None = None
    title: Annotated[str | None, Field(default=None, min_length=1, max_length=200)]
    url: HttpUrl
    category: ShortText
    location: ShortText
    seniority: ShortText
    stack: Text
    source: ShortText
    summary: JobDescription  # texte de l'annonce, affiché dans la fiche du poste
    score: Annotated[float | None, Field(default=None, ge=0, le=10)]
    role_type: ShortText  # backend, platform, MLOps, founding, FDE…
    work_mode: Annotated[str | None, Field(default=None, pattern="^(sur_site|hybride|remote)$")] = None
    travel: ShortText  # niveau de déplacements
    salary: ShortText  # fourchette affichée
    published_on: date | None = None


class JobIn(JobPatch):
    company_id: int
    title: Annotated[str, Field(min_length=1, max_length=200)]


class JobMini(ORM):
    id: int
    title: str | Locked
    url: str | Locked | None
    location: str | None


class CriterionOut(BaseModel):
    key: str
    label: str
    points: float
    max: float


class AssessmentOut(BaseModel):
    """Évaluation calculée de l'offre pour l'utilisateur connecté (champs « | Locked » : masqués en Free)."""

    score: float | Locked | None  # note retenue : correction manuelle, sinon note calculée
    auto_score: float | Locked | None
    manual_score: float | None
    verdict: str  # nouveau | ecartee
    exclusion: str | Locked | None
    why: str | Locked | None
    criteria: list[CriterionOut] | Locked
    skills_have: list[str] | Locked
    skills_missing: list[str] | Locked
    stale: bool  # évaluée avec une ancienne version du profil (réévaluation en attente)
    assessed_at: datetime


class JobOut(JobMini):
    # Offre floutée (Free) : titre, entreprise, salaire, lien et contenu factices ; seule la date reste
    locked: bool = False
    category: str | None
    seniority: str | None
    stack: str | None
    source: str | None
    summary: str | Locked | None
    role_type: str | None = None
    work_mode: str | None = None
    travel: str | None = None
    salary: str | Locked | None = None
    published_on: date | None = None
    verified_open_at: datetime | None = None  # ouverture vérifiée dans la liste de l'entreprise
    closed_at: datetime | None = None  # offre fermée (donnée partagée)
    # Note de l'utilisateur connecté : correction manuelle, sinon évaluation automatique,
    # sinon ancienne note partagée (saisie à la main avant l'évaluation par utilisateur)
    score: float | Locked | None
    created_at: datetime
    company: CompanyMini
    # Statut posé par l'utilisateur connecté (« nouveau » s'il n'a pas de process)
    status: ProcessStatus = ProcessStatus.nouveau
    application_id: int | None = None
    assessment: AssessmentOut | None = None
    # Statut affiché : celui posé par l'utilisateur s'il a un process, sinon le verdict de l'évaluation
    # (« nouveau » ou « ecartee »), sinon « nouveau »
    display_status: str = "nouveau"
    # Dernier retour de l'utilisateur sur l'offre : like | dislike | None (cœur / pouce remplis dans la liste)
    feedback: str | None = None
    # Candidature préparée : en_preparation | brouillon | prete | envoyee | None (bouton « Voir ma candidature »)
    draft_status: str | None = None
    # Classement pour l'utilisateur (ranking.py) et raison pour laquelle l'offre n'est pas proposée
    match: MatchOut | None = None
    hidden_reason: str | None = None
    # Filtres (offre en clair seulement) : métier, niveau, salaire brut annuel en k€, ville principale
    role_family: str | None = None
    seniority_level: str | None = None
    salary_min_keur: int | None = None
    salary_max_keur: int | None = None
    city: str | None = None


class ManualScoreIn(BaseModel):
    score: Annotated[float | None, Field(default=None, ge=0, le=10)] = None  # None = revenir à la note calculée


class JobStatusIn(BaseModel):
    status: ProcessStatus


# --- Process -----------------------------------------------------------------


class ApplicationPatch(BaseModel):
    status: ProcessStatus | None = None
    next_step: Annotated[str | None, Field(default=None, max_length=300), AfterValidator(_blank_to_none)]
    next_step_date: date | None = None
    applied_on: date | None = None
    notes: Text


class ApplicationIn(ApplicationPatch):
    job_id: int
    status: ProcessStatus = ProcessStatus.a_postuler


class EventOut(ORM):
    id: int
    from_status: ProcessStatus | None
    to_status: ProcessStatus
    note: str | None
    created_at: datetime
    has_snapshot: bool = False  # « J'ai envoyé ma candidature » : instantané consultable


class ApplicationOut(ORM):
    id: int
    status: ProcessStatus
    next_step: str | None
    next_step_date: date | None
    applied_on: date | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    job: JobMini
    company: CompanyMini
    events: list[EventOut] = []
    draft_status: str | None = None  # candidature préparée (page Candidature)


# --- Tableau de bord / analytics --------------------------------------------


class DashboardStats(BaseModel):
    jobs: int
    todo: int
    active: int
    interviews: int
    companies: int


class DashboardOut(BaseModel):
    stats: DashboardStats
    priority_jobs: list[JobOut]
    processes: list[ApplicationOut]
    recent_fundings: list[CompanyOut]


class StatusCount(BaseModel):
    status: ProcessStatus
    count: int


class FunnelStep(BaseModel):
    label: str
    count: int


class CompanyActivity(BaseModel):
    company: CompanyMini
    jobs: int
    processes: int


class WeekActivity(BaseModel):
    week: date
    events: int


class AnalyticsOut(BaseModel):
    by_status: list[StatusCount]
    funnel: list[FunnelStep]
    by_company: list[CompanyActivity]
    weekly: list[WeekActivity]


class JobDetailOut(JobOut):
    """GET /api/jobs/{id} : poste + process de l'utilisateur (None s'il n'en a pas)."""

    application: ApplicationOut | None = None
