"""Modèle de données.

users ──< applications >── jobs >── companies
  │           │
  │           └──< application_events (historique des changements de statut)
  ├── user_profiles      profil riche (1-1, créé à la volée), contexte du chat
  └──< profile_imports   aperçus d'import de profil en attente de confirmation

Les entreprises et les postes sont partagés (le « radar »), les process sont propres à chaque utilisateur.
Un poste sans process pour un utilisateur est considéré comme « nouveau » pour lui.
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlparse

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from .db import Base


class ProcessStatus(str, enum.Enum):
    nouveau = "nouveau"
    a_postuler = "a_postuler"
    postule = "postule"
    relance = "relance"
    entretien = "entretien"
    offre = "offre"
    refuse = "refuse"
    abandonne = "abandonne"


STATUS_LABELS: dict[ProcessStatus, str] = {
    ProcessStatus.nouveau: "Nouveau",
    ProcessStatus.a_postuler: "À postuler",
    ProcessStatus.postule: "Postulé",
    ProcessStatus.relance: "Relance",
    ProcessStatus.entretien: "Entretien",
    ProcessStatus.offre: "Offre",
    ProcessStatus.refuse: "Refusé",
    ProcessStatus.abandonne: "Abandonné",
}
TODO_STATUSES = {ProcessStatus.nouveau, ProcessStatus.a_postuler}
ACTIVE_STATUSES = {ProcessStatus.postule, ProcessStatus.relance, ProcessStatus.entretien, ProcessStatus.offre}

status_enum = Enum(
    ProcessStatus,
    name="process_status",
    values_callable=lambda e: [m.value for m in e],
)


# JSONB sous PostgreSQL, JSON ailleurs (tests sur SQLite).
# Les colonnes JSON sont toujours réaffectées (jamais modifiées sur place) pour être détectées par SQLAlchemy.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    linkedin_sub: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    given_name: Mapped[str | None] = mapped_column(String(100))
    family_name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(320))
    picture: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str | None] = mapped_column(String(20))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    applications: Mapped[list["Application"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    profile: Mapped["UserProfile | None"] = relationship(back_populates="user", cascade="all, delete-orphan")


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200), unique=True)
    website: Mapped[str | None] = mapped_column(String(1000))
    sector: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(120))
    funding_stage: Mapped[str | None] = mapped_column(String(60))
    funding_amount_meur: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    funding_announced_on: Mapped[date | None] = mapped_column(Date)
    investors: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)

    # Logo : URL explicite (sinon favicon du site), et cache de l'image en base
    logo_url: Mapped[str | None] = mapped_column(String(1000))
    logo_data: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    logo_content_type: Mapped[str | None] = mapped_column(String(100))
    logo_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    jobs: Mapped[list["Job"]] = relationship(back_populates="company", cascade="all, delete-orphan")

    @property
    def domain(self) -> str | None:
        if not self.website:
            return None
        url = self.website if "://" in self.website else f"https://{self.website}"
        host = urlparse(url).hostname
        return host.removeprefix("www.") if host else None


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(1000))
    category: Mapped[str | None] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(200))
    seniority: Mapped[str | None] = mapped_column(String(60))
    stack: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(120))
    summary: Mapped[str | None] = mapped_column(Text)
    score: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))

    company: Mapped[Company] = relationship(back_populates="jobs")
    applications: Mapped[list["Application"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class Application(TimestampMixin, Base):
    """Un process de candidature : un utilisateur × un poste."""

    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("user_id", "job_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    status: Mapped[ProcessStatus] = mapped_column(status_enum, default=ProcessStatus.nouveau)
    next_step: Mapped[str | None] = mapped_column(String(300))
    next_step_date: Mapped[date | None] = mapped_column(Date)
    applied_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="applications")
    job: Mapped[Job] = relationship(back_populates="applications")
    events: Mapped[list["ApplicationEvent"]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="ApplicationEvent.created_at"
    )

    @property
    def company(self) -> Company:
        return self.job.company


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[ProcessStatus | None] = mapped_column(status_enum)
    to_status: Mapped[ProcessStatus] = mapped_column(status_enum)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    application: Mapped[Application] = relationship(back_populates="events")


class UserProfile(TimestampMixin, Base):
    """Profil riche de l'utilisateur. Structure des champs JSON : voir app/profile.py (ProfileData)."""

    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)

    # Identité professionnelle
    headline: Mapped[str | None] = mapped_column(String(300))
    summary: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200))
    years_experience: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    languages: Mapped[list] = mapped_column(JSONType, default=list)

    # Parcours
    experiences: Mapped[list] = mapped_column(JSONType, default=list)
    education: Mapped[list] = mapped_column(JSONType, default=list)
    skills: Mapped[list] = mapped_column(JSONType, default=list)

    # Préférences de recherche et règles libres (« à éviter » / « préférer »)
    preferences: Mapped[dict] = mapped_column(JSONType, default=dict)
    rules: Mapped[list] = mapped_column(JSONType, default=list)

    # Trace des imports appliqués : [{source, filename, imported_at}] (jamais le fichier brut)
    raw_imports: Mapped[list] = mapped_column(JSONType, default=list)

    user: Mapped[User] = relationship(back_populates="profile")


class ProfileImport(Base):
    """Import de profil analysé, en attente de confirmation (aperçu des changements)."""

    __tablename__ = "profile_imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(40))  # linkedin_export | pdf | text
    filename: Mapped[str | None] = mapped_column(String(255))
    data: Mapped[dict] = mapped_column(JSONType)  # ProfileData extrait
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
