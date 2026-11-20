"""API publique de la landing (/api/public) : sans session, rien de réservé au Pro.

- GET /landing : prix et limites des offres (lus dans PLAN_LIMITS, jamais en dur dans le front), chiffres du
  radar, entreprises de la bande de logos (seulement celles qu'un utilisateur Free voit déjà en clair).
- GET /logos/{id} : logo d'une de ces entreprises (404 pour les autres).
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, undefer

from . import logos
from .db import get_db
from .entitlements import PLAN_LIMITS, public_company_ids
from .models import Company, Job

router = APIRouter(prefix="/api/public")

DB = Annotated[Session, Depends(get_db)]

LOGO_STRIP = 18  # logos affichés sur la landing
PUBLIC_LIMITS = ("fresh_delay_days", "top_scores_per_week", "assessments_per_week", "active_processes",
                 "imports_total", "profile_analysis", "rules_synthesis", "chat_per_month")


class PublicCompany(BaseModel):
    id: int
    name: str
    sector: str | None
    logo: str


class LandingOut(BaseModel):
    prices: dict[str, str]
    limits: dict[str, dict]
    stats: dict[str, int]
    companies: list[PublicCompany]


@router.get("/landing")
def landing(db: DB) -> LandingOut:
    pro = PLAN_LIMITS["pro"]
    week_ago = datetime.now(UTC) - timedelta(days=7)
    public = public_company_ids(db)
    rows = db.execute(
        select(Company.id, Company.name, Company.sector, func.count(Job.id))
        .join(Job, Job.company_id == Company.id)
        .where(Company.id.in_(public or {-1}))
        .group_by(Company.id)
        # Logos déjà en cache d'abord, puis les entreprises qui recrutent le plus
        .order_by(Company.logo_data.is_(None), func.count(Job.id).desc(), Company.name)
        .limit(LOGO_STRIP)
    ).all()
    return LandingOut(
        prices={"monthly": pro["price_monthly_label"], "pass_3m": pro["price_pass_3m_label"],
                "pass_days": str(pro["pass_days"])},
        limits={plan: {k: PLAN_LIMITS[plan][k] for k in PUBLIC_LIMITS} for plan in ("free", "pro")},
        stats={
            "companies": db.scalar(select(func.count(Company.id))) or 0,
            "jobs_open": db.scalar(select(func.count(Job.id)).where(Job.closed_at.is_(None))) or 0,
            "jobs_week": db.scalar(select(func.count(Job.id)).where(Job.created_at >= week_ago)) or 0,
        },
        companies=[PublicCompany(id=cid, name=name, sector=sector, logo=f"/api/public/logos/{cid}")
                   for cid, name, sector, _ in rows],
    )


@router.get("/logos/{company_id}")
def public_logo(company_id: int, db: DB) -> Response:
    missing = Response(status_code=404, headers={"Cache-Control": "no-store"})
    if company_id not in public_company_ids(db):
        return missing  # entreprise réservée au Pro (ou inconnue) : même réponse
    company = db.scalar(select(Company).where(Company.id == company_id).options(undefer(Company.logo_data)))
    if company is None:
        return missing
    if logos.should_fetch(company):
        found = logos.fetch_logo(company)
        company.logo_fetched_at = datetime.now(UTC)
        if found:
            company.logo_data, company.logo_content_type = found
        db.commit()
    if company.logo_data is None:
        return missing
    return Response(company.logo_data, media_type=company.logo_content_type,
                    headers={"Cache-Control": "public, max-age=86400",
                             "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                             "X-Content-Type-Options": "nosniff"})
