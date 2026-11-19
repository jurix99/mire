"""API du radar partagé : vérification des offres via les ATS, sources, passages de recherche.

Ces données sont partagées entre utilisateurs (comme les entreprises et les postes).
"""

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import assessments, ats, services
from .db import get_db
from .models import Company, Job, User
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


def check_and_commit(db: Session, company: Company, fetch=ats.fetch_postings) -> CheckOut:
    result = ats.check_company(db, company, fetch)
    db.commit()
    return CheckOut(
        company_id=company.id, ats=result.ats, listed=result.listed, verified=result.verified, closed=result.closed,
        new_postings=[PostingOut(**p.__dict__) for p in result.new_postings], error=result.error,
        checked_at=company.last_checked_at,
    )


@router.post("/companies/{company_id}/check")
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


@router.post("/companies/{company_id}/postings/import")
def import_company_postings(company_id: int, body: ImportPostingsIn, db: DB) -> ImportPostingsOut:
    """Ajoute au radar des offres listées par l'ATS de l'entreprise (vérifiées ouvertes, sans doublon)."""
    company = services.get_or_404(db, Company, company_id)
    try:
        return import_postings(db, company, body.ats_job_ids)
    except ats.ATSError as exc:
        raise services.ServiceError(502, str(exc)) from None
