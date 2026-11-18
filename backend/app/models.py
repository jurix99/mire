"""Modèle de données.

users ──< applications >── jobs >── companies
  │           │
  │           └──< application_events (historique des changements de statut)
  ├── user_profiles      profil riche (1-1, créé à la volée), contexte du chat
  ├──< profile_imports   aperçus d'import de profil en attente de confirmation
  ├──< chat_conversations ──< chat_messages   conversations avec l'assistant Gemini
  ├──< chat_actions      écritures proposées par l'assistant (confirmées ou non) = journal des actions
  ├──< job_feedback      retours sur les offres (j'aime / pas intéressé + raisons) -> synthèse de règles
  └──< job_assessments   évaluation CALCULÉE d'une offre pour l'utilisateur (note, verdict, raisons)

Règle d'or : l'automatisation (évaluation) n'écrit jamais dans applications (statuts posés par
l'utilisateur) ni dans job_assessments.manual_score (note corrigée par l'utilisateur).

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
    Integer,
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
    a_postuler = "a_postuler"  # libellé « À regarder »
    postule = "postule"
    relance = "relance"
    entretien = "entretien"
    offre = "offre"
    refuse = "refuse"
    pas_interesse = "pas_interesse"  # rejet par l'utilisateur (raison facultative -> job_feedback)
    abandonne = "abandonne"


STATUS_LABELS: dict[ProcessStatus, str] = {
    ProcessStatus.nouveau: "Nouveau",
    ProcessStatus.a_postuler: "À regarder",
    ProcessStatus.postule: "Postulé",
    ProcessStatus.relance: "Relance",
    ProcessStatus.entretien: "Entretien",
    ProcessStatus.offre: "Offre",
    ProcessStatus.refuse: "Refusé",
    ProcessStatus.pas_interesse: "Pas intéressé",
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

    # Positionnement / point fort, entreprises exclues, offres de référence, barème de notation
    positioning: Mapped[str | None] = mapped_column(Text)
    excluded_companies: Mapped[list] = mapped_column(JSONType, default=list)
    reference_jobs: Mapped[list] = mapped_column(JSONType, default=list)
    scoring: Mapped[dict] = mapped_column(JSONType, default=dict)
    # Incrémentée à chaque changement du contenu du profil : les évaluations d'une version
    # antérieure sont réévaluées (seulement pour les offres que l'utilisateur n'a pas traitées)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    user: Mapped[User] = relationship(back_populates="profile")


class ProfileImport(Base):
    """Propositions de modification du profil, en attente de validation (élément par élément).

    kind = import (fichier / texte), analyse (déductions sur le profil), regles (synthèse des retours).
    """

    __tablename__ = "profile_imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="import", server_default="import")
    source: Mapped[str] = mapped_column(String(40))  # linkedin_export | pdf | text | analyse | feedback
    filename: Mapped[str | None] = mapped_column(String(255))
    data: Mapped[dict | None] = mapped_column(JSONType)  # ProfileData extrait (imports)
    proposals: Mapped[list | None] = mapped_column(JSONType)  # éléments proposés (voir proposals.py)
    extra: Mapped[dict | None] = mapped_column(JSONType)  # ex. questions à poser (analyse)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatConversation(TimestampMixin, Base):
    """Conversation avec l'assistant (une conversation active par utilisateur : la plus récente)."""

    __tablename__ = "chat_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ChatMessage.id"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("chat_conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    # Outils appelés pendant ce tour : [{name, args, ok, action_id?}] (traçabilité, pas renvoyé au modèle)
    tool_calls: Mapped[list | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    conversation: Mapped[ChatConversation] = relationship(back_populates="messages")
    actions: Mapped[list["ChatAction"]] = relationship(back_populates="message", order_by="ChatAction.id")


class ChatAction(TimestampMixin, Base):
    """Écriture proposée par l'assistant : exécutée seulement après confirmation de l'utilisateur.

    Sert aussi de journal des actions exécutées (status = confirmed, executed_at, result).
    """

    __tablename__ = "chat_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(60))
    args: Mapped[dict] = mapped_column(JSONType)
    summary: Mapped[str] = mapped_column(Text)  # description lisible de la proposition
    changes: Mapped[list] = mapped_column(JSONType)  # [{label, before, after}]
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|confirmed|cancelled|failed|expired
    result: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    message: Mapped[ChatMessage | None] = relationship(back_populates="actions")


class FeedbackSentiment(str, enum.Enum):
    like = "like"
    dislike = "dislike"


class JobFeedback(Base):
    """Retour de l'utilisateur sur une offre : alimente la synthèse des règles."""

    __tablename__ = "job_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    sentiment: Mapped[str] = mapped_column(String(10))  # like | dislike
    reasons: Mapped[list] = mapped_column(JSONType, default=list)  # clés de raisons (chips)
    text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Date à laquelle une synthèse de règles a pris ce retour en compte (None = nouveau retour)
    synthesized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped["Job"] = relationship()


class JobAssessment(TimestampMixin, Base):
    """Évaluation calculée d'une offre pour un utilisateur (donnée dérivée de son profil)."""

    __tablename__ = "job_assessments"
    __table_args__ = (UniqueConstraint("user_id", "job_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))  # note /10 calculée (None si exclue)
    criteria: Mapped[dict] = mapped_column(JSONType, default=dict)  # {critère: points}
    verdict: Mapped[str] = mapped_column(String(20))  # nouveau | ecartee
    exclusion: Mapped[str | None] = mapped_column(Text)  # raison d'écart en une ligne
    why: Mapped[str | None] = mapped_column(Text)  # « pourquoi c'est intéressant » (salaire inclus si affiché)
    skills_have: Mapped[list] = mapped_column(JSONType, default=list)
    skills_missing: Mapped[list] = mapped_column(JSONType, default=list)
    profile_version: Mapped[int] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String(60))  # « regles » si exclusion dure, sinon modèle Gemini
    manual_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))  # correction de l'utilisateur
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    job: Mapped["Job"] = relationship()
