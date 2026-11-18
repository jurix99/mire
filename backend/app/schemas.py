"""Schémas d'entrée / sortie de l'API."""

import re
from datetime import date, datetime
from typing import Annotated

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


# --- Utilisateur -------------------------------------------------------------


class NavCounts(BaseModel):
    companies: int
    jobs: int
    processes: int


class UserOut(ORM):
    id: int
    name: str | None
    given_name: str | None
    family_name: str | None
    email: str | None
    picture: str | None
    locale: str | None = None
    created_at: datetime


class MeOut(BaseModel):
    user: UserOut
    counts: NavCounts


class StatusOut(BaseModel):
    key: ProcessStatus
    label: str


# --- Entreprises -------------------------------------------------------------


class CompanyMini(ORM):
    id: int
    name: str
    slug: str
    domain: str | None

    @computed_field
    @property
    def logo(self) -> str:
        return f"/api/companies/{self.id}/logo"


class CompanyOut(CompanyMini):
    website: str | None
    sector: str | None
    city: str | None
    funding_stage: str | None
    funding_amount_meur: float | None
    funding_announced_on: date | None
    investors: str | None
    description: str | None
    logo_url: str | None
    jobs_count: int = 0


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


class JobIn(JobPatch):
    company_id: int
    title: Annotated[str, Field(min_length=1, max_length=200)]


class JobMini(ORM):
    id: int
    title: str
    url: str | None
    location: str | None


class JobOut(JobMini):
    category: str | None
    seniority: str | None
    stack: str | None
    source: str | None
    summary: str | None
    score: float | None
    created_at: datetime
    company: CompanyMini
    # Statut du point de vue de l'utilisateur connecté (« nouveau » s'il n'a pas de process)
    status: ProcessStatus = ProcessStatus.nouveau
    application_id: int | None = None


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
    from_status: ProcessStatus | None
    to_status: ProcessStatus
    note: str | None
    created_at: datetime


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
