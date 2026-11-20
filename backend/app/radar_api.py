"""API du radar partagé : vérification des offres via les ATS, sources, passages de recherche.

Ces données sont partagées entre utilisateurs (comme les entreprises et les postes).
"""

import os
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import assessments, ats, entitlements, services
from .db import get_db
from .entitlements import RequireAdmin
from .models import Company, Job, SearchRun, Source, User
from .users import current_user

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])

DB = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]


class PostingOut(BaseModel):
    ats_job_id: str
    title: str
    url: str | None
    location: str | None
    published_on: date | None


class CheckOut(BaseModel):
    company_id: int
    ats: str | None
    listed: int  # offres listées par l'ATS
    verified: list[int]  # postes de Mire confirmés ouverts
    closed: list[int]  # postes de Mire marqués fermés
    new_postings: list[PostingOut]  # offres listées inconnues de Mire
    error: str | None
    checked_at: datetime | None


class ImportPostingsIn(BaseModel):
    ats_job_ids: Annotated[list[str], Field(min_length=1, max_length=50)]


class ImportPostingsOut(BaseModel):
    created: list[int]
    duplicates: list[int]  # déjà connus (jamais recréés)
    missing: list[str]  # plus listés par l'ATS
    too_old: list[str] = []  # publiées il y a plus de 2 mois : non ajoutées (docs §4)


MAX_AGE_DAYS = 61


def check_and_commit(db: Session, company: Company, fetch=ats.fetch_postings) -> CheckOut:
    result = ats.check_company(db, company, fetch)
    db.commit()
    return CheckOut(
        company_id=company.id, ats=result.ats, listed=result.listed, verified=result.verified, closed=result.closed,
        # Les plus récentes d'abord
        new_postings=[PostingOut(**p.__dict__) for p in sorted(
            result.new_postings, key=lambda p: (p.published_on is None, -(p.published_on or date.min).toordinal()))],
        error=result.error,
        checked_at=company.last_checked_at,
    )


# Vérification et import depuis l'ATS : réservés au Pro (la réponse donne l'ATS et les offres les plus récentes,
# masqués pour un utilisateur Free)
@router.post("/companies/{company_id}/check", dependencies=[RequireAdmin])
def check_company_postings(company_id: int, db: DB) -> CheckOut:
    """Vérifie les offres de l'entreprise dans la liste actuelle de son ATS (ouvertes / fermées / nouvelles)."""
    return check_and_commit(db, services.get_or_404(db, Company, company_id))


def import_postings(db: Session, company: Company, ids: list[str], fetch=ats.fetch_postings,
                    source: str | None = None) -> ImportPostingsOut:
    postings = {p.ats_job_id: p for p in fetch(company.ats, company.ats_slug)}
    out = ImportPostingsOut(created=[], duplicates=[], missing=[])
    now = datetime.now(UTC)
    for ats_id in dict.fromkeys(ids):
        p = postings.get(ats_id)
        if p is None:
            out.missing.append(ats_id)
            continue
        if p.published_on and (date.today() - p.published_on).days > MAX_AGE_DAYS:
            out.too_old.append(ats_id)
            continue
        if dup := services.find_duplicate(db, company.id, p.title, p.url):
            out.duplicates.append(dup.id)
            continue
        job = Job(company_id=company.id, title=p.title[:200], url=(p.url or "")[:1000] or None,
                  location=(p.location or "")[:200] or None, ats_job_id=ats_id, published_on=p.published_on,
                  verified_open_at=now, source=source or f"ATS {company.ats}")
        db.add(job)
        db.flush()
        out.created.append(job.id)
    db.commit()
    if out.created:
        assessments.wake()  # nouvelles offres : évaluation pour chaque utilisateur
    return out


@router.post("/companies/{company_id}/postings/import", dependencies=[RequireAdmin])
def import_company_postings(company_id: int, body: ImportPostingsIn, db: DB) -> ImportPostingsOut:
    """Ajoute au radar des offres listées par l'ATS de l'entreprise (vérifiées ouvertes, sans doublon)."""
    company = services.get_or_404(db, Company, company_id)
    try:
        return import_postings(db, company, body.ats_job_ids)
    except ats.ATSError as exc:
        raise services.ServiceError(502, str(exc)) from None


# --- Sources (gérées par les utilisateurs ; l'agent ne peut qu'en ajouter) ---------------------

SOURCE_KINDS = ("ats", "job_board", "vc_board", "funding_radar", "recruiter", "google_query", "autre")


class SourceOut(BaseModel):
    id: int
    name: str
    kind: str
    query: str | None
    active: bool
    added_by: str
    jobs_found: int
    auto_readable: bool
    notes: str | None
    last_used_at: datetime | None
    created_at: datetime


class SourcePatch(BaseModel):
    name: Annotated[str | None, Field(default=None, min_length=1, max_length=200)] = None
    kind: Annotated[str | None, Field(default=None, pattern="^(" + "|".join(SOURCE_KINDS) + ")$")] = None
    query: Annotated[str | None, Field(default=None, max_length=2000)] = None
    active: bool | None = None
    auto_readable: bool | None = None
    notes: Annotated[str | None, Field(default=None, max_length=5000)] = None


class SourceIn(SourcePatch):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    kind: Annotated[str, Field(pattern="^(" + "|".join(SOURCE_KINDS) + ")$")]


def _source_out(s: Source) -> SourceOut:
    return SourceOut.model_validate(s, from_attributes=True)


@router.get("/sources")
def list_sources(db: DB) -> list[SourceOut]:
    return [_source_out(s) for s in db.scalars(select(Source).order_by(Source.active.desc(), Source.kind, Source.name))]


@router.post("/sources", status_code=201, dependencies=[RequireAdmin])
def create_source(body: SourceIn, db: DB, user: Me) -> SourceOut:
    if db.scalar(select(Source).where(func.lower(Source.name) == body.name.lower())):
        raise HTTPException(409, "Une source porte déjà ce nom")
    source = Source(**body.model_dump(exclude_none=True), added_by="user", added_by_user_id=user.id, jobs_found=0)
    db.add(source)
    db.commit()
    return _source_out(source)


@router.patch("/sources/{source_id}", dependencies=[RequireAdmin])
def update_source(source_id: int, body: SourcePatch, db: DB) -> SourceOut:
    """Modifier, désactiver ou réactiver une source (seul un utilisateur peut désactiver)."""
    source = services.get_or_404(db, Source, source_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field in ("name", "kind", "active", "auto_readable") and value is None:
            continue
        setattr(source, field, value)
    db.commit()
    return _source_out(source)


# --- Passages de recherche -----------------------------------------------------------------------


class RunOut(BaseModel):
    id: int
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    jobs_seen: int
    jobs_added: int
    jobs_duplicates: int
    jobs_closed: int
    companies_checked: int
    sources_added: int
    summary: str | None
    mine: dict | None = None  # résumé pour l'utilisateur connecté (nouvelles offres retenues, top)


class RunsOut(BaseModel):
    schedule: str | None  # passages planifiés (si l'agent est configuré)
    runs: list[RunOut]


@router.get("/runs")
def list_runs(db: DB, user: Me, limit: int = 10) -> RunsOut:
    runs = db.scalars(select(SearchRun).order_by(SearchRun.id.desc()).limit(max(1, min(limit, 50)))).all()
    full = entitlements.plan_of(db, user) == "pro"
    out = []
    for r in runs:
        item = RunOut.model_validate(r, from_attributes=True)
        item.mine = next((u for u in (r.details or {}).get("per_user", []) if u.get("user_id") == user.id), None)
        if item.mine and not full:
            item.mine = {k: v for k, v in item.mine.items() if k != "top"}  # offres récentes : réservées au Pro
        out.append(item)
    from . import collector

    parts = []
    if collector.collector is not None:
        parts.append(f"collecte chaque jour à {collector.collector.at.replace(':', 'h')}")
    if os.environ.get("AGENT_TOKEN"):
        parts.append(f"découverte {os.environ.get('AGENT_SCHEDULE', 'le lundi à 7h43')}")
    schedule = " · ".join(parts).capitalize() if parts else None
    return RunsOut(schedule=schedule, runs=out)


# --- Collecte quotidienne (processus 2) ---------------------------------------------------------


class CollectStatus(BaseModel):
    enabled: bool
    running: bool
    at: str | None  # heure quotidienne (Paris)
    last: RunOut | None


@router.get("/collect/status")
def collect_status(db: DB) -> CollectStatus:
    from . import collector

    c = collector.collector
    last = db.scalars(select(SearchRun).where(SearchRun.kind == "collecte").order_by(SearchRun.id.desc()).limit(1)).first()
    return CollectStatus(enabled=c is not None, running=bool(c and c.running), at=c.at if c else None,
                         last=RunOut.model_validate(last, from_attributes=True) if last else None)


@router.post("/collect/run", status_code=202, dependencies=[RequireAdmin])
def collect_run() -> dict:
    """Lance la collecte tout de suite (en tâche de fond)."""
    from . import collector

    c = collector.collector
    if c is None:
        raise HTTPException(503, "Collecte désactivée (COLLECTOR=false)")
    if not c.trigger():
        raise HTTPException(409, "Une collecte est déjà en cours")
    return {"started": True}
