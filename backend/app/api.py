"""API JSON du site (/api/...). Toutes les routes exigent un utilisateur connecté."""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, undefer

from . import assessments, logos, services
from .assessments_api import invalidate_job
from .db import get_db
from .models import (
    ACTIVE_STATUSES,
    STATUS_LABELS,
    Application,
    ApplicationEvent,
    Company,
    Job,
    ProcessStatus,
    User,
)
from .schemas import (
    AnalyticsOut,
    ApplicationIn,
    ApplicationOut,
    ApplicationPatch,
    CompanyActivity,
    CompanyIn,
    CompanyMini,
    CompanyOut,
    CompanyPatch,
    DashboardOut,
    FunnelStep,
    JobDetailOut,
    JobIn,
    JobOut,
    JobPatch,
    JobStatusIn,
    MeOut,
    NavCounts,
    StatusCount,
    StatusOut,
    UserOut,
    WeekActivity,
)
from .services import company_out as _company_out
from .services import get_or_404 as _get_or_404
from .services import jobs_count_by_company as _jobs_count_by_company
from .services import load_application as _load_application
from .services import slugify as _slugify
from .services import user_apps as _user_apps
from .users import current_user

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])

DB = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]


# --- Utilisateur -------------------------------------------------------------


@router.get("/me")
def me(db: DB, user: Me) -> MeOut:
    processes = db.scalar(
        select(func.count(Application.id)).where(
            Application.user_id == user.id, Application.status != ProcessStatus.nouveau
        )
    )
    counts = NavCounts(
        companies=db.scalar(select(func.count(Company.id))) or 0,
        jobs=db.scalar(select(func.count(Job.id))) or 0,
        processes=processes or 0,
    )
    return MeOut(user=UserOut.model_validate(user), counts=counts)


@router.get("/statuses")
def statuses() -> list[StatusOut]:
    return [StatusOut(key=s, label=STATUS_LABELS[s]) for s in ProcessStatus]


# --- Entreprises -------------------------------------------------------------


@router.get("/companies")
def list_companies(db: DB) -> list[CompanyOut]:
    counts = _jobs_count_by_company(db)
    companies = db.scalars(
        select(Company).order_by(Company.funding_announced_on.desc().nulls_last(), Company.name)
    )
    return [_company_out(c, counts.get(c.id, 0)) for c in companies]


@router.post("/companies", status_code=201)
def create_company(body: CompanyIn, db: DB) -> CompanyOut:
    company = Company(**body.model_dump(), slug=_slugify(db, body.name))
    db.add(company)
    db.commit()
    return _company_out(company, 0)


@router.get("/companies/{company_id}")
def get_company(company_id: int, db: DB) -> CompanyOut:
    company = _get_or_404(db, Company, company_id)
    return _company_out(company, _jobs_count_by_company(db).get(company_id, 0))


@router.patch("/companies/{company_id}")
def update_company(company_id: int, body: CompanyPatch, db: DB) -> CompanyOut:
    company = _get_or_404(db, Company, company_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise HTTPException(422, "Le nom est obligatoire")
    for field, value in changes.items():
        setattr(company, field, value)
    if "name" in changes:
        company.slug = _slugify(db, company.name, exclude_id=company.id)
    if {"website", "logo_url"} & changes.keys():
        logos.reset_logo(company)
    db.commit()
    return _company_out(company, _jobs_count_by_company(db).get(company_id, 0))


@router.delete("/companies/{company_id}", status_code=204)
def delete_company(company_id: int, db: DB) -> Response:
    db.delete(_get_or_404(db, Company, company_id))
    db.commit()
    return Response(status_code=204)


LOGO_HEADERS = {
    "Cache-Control": "private, max-age=3600",
    # Un SVG ouvert directement ne doit pas pouvoir exécuter de script sur le domaine du site
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
}


@router.get("/companies/{company_id}/logo")
def company_logo(company_id: int, db: DB) -> Response:
    company = db.scalar(select(Company).where(Company.id == company_id).options(undefer(Company.logo_data)))
    if company is None:
        raise HTTPException(404, "Introuvable")
    if logos.should_fetch(company):
        found = logos.fetch_logo(company)
        company.logo_fetched_at = datetime.now(UTC)
        if found:
            company.logo_data, company.logo_content_type = found
        db.commit()
    if company.logo_data is None:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    return Response(company.logo_data, media_type=company.logo_content_type, headers=LOGO_HEADERS)


@router.put("/companies/{company_id}/logo", status_code=204)
async def upload_company_logo(company_id: int, file: UploadFile, db: DB) -> Response:
    company = _get_or_404(db, Company, company_id)
    ctype = (file.content_type or "").lower()
    if ctype not in logos.ALLOWED_TYPES:
        raise HTTPException(415, "Format accepté : PNG, JPEG, GIF, WebP, SVG ou ICO")
    data = await file.read(logos.MAX_LOGO_BYTES + 1)
    if not data or len(data) > logos.MAX_LOGO_BYTES:
        raise HTTPException(413, "Image vide ou trop lourde (512 Ko max)")
    company.logo_data, company.logo_content_type = data, ctype
    company.logo_fetched_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=204)


@router.delete("/companies/{company_id}/logo", status_code=204)
def refresh_company_logo(company_id: int, db: DB) -> Response:
    """Oublie le logo en cache : il sera récupéré à nouveau au prochain affichage."""
    logos.reset_logo(_get_or_404(db, Company, company_id))
    db.commit()
    return Response(status_code=204)


# --- Postes ------------------------------------------------------------------


@router.get("/jobs")
def list_jobs(db: DB, user: Me, company_id: int | None = None) -> list[JobOut]:
    return services.list_jobs(db, user, company_id=company_id)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: DB, user: Me) -> JobDetailOut:
    """Détail d'un poste (contenu de l'offre) et process de l'utilisateur pour ce poste, s'il existe."""
    job = services.get_job(db, user, job_id)
    app = services.load_application(db, job.application_id, user) if job.application_id else None
    return JobDetailOut(**job.model_dump(), application=ApplicationOut.model_validate(app) if app else None)


@router.post("/jobs", status_code=201)
def create_job(body: JobIn, db: DB) -> JobOut:
    out = services.create_job(db, body)
    assessments.wake()  # évaluer la nouvelle offre pour chaque utilisateur
    return out


@router.patch("/jobs/{job_id}")
def update_job(job_id: int, body: JobPatch, db: DB, user: Me) -> JobOut:
    job = _get_or_404(db, Job, job_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("company_id", job.company_id) is None or ("title" in changes and changes["title"] is None):
        raise HTTPException(422, "Entreprise et intitulé obligatoires")
    if "company_id" in changes:
        _get_or_404(db, Company, changes["company_id"])
    for field, value in changes.items():
        setattr(job, field, value)
    invalidate_job(db, job.id)  # contenu modifié : réévaluer chez ceux qui ne l'ont pas traitée
    db.commit()
    assessments.wake()
    return services.get_job(db, user, job.id)


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: int, db: DB) -> Response:
    db.delete(_get_or_404(db, Job, job_id))
    db.commit()
    return Response(status_code=204)


@router.put("/jobs/{job_id}/status")
def set_job_status(job_id: int, body: JobStatusIn, db: DB, user: Me) -> JobOut:
    """Change le statut d'un poste pour l'utilisateur (crée le process si besoin)."""
    return services.set_job_status(db, user, job_id, body.status)


# --- Process -----------------------------------------------------------------


@router.get("/applications")
def list_applications(db: DB, user: Me) -> list[ApplicationOut]:
    return services.list_applications(db, user)


@router.post("/applications", status_code=201)
def create_application(body: ApplicationIn, db: DB, user: Me) -> ApplicationOut:
    _get_or_404(db, Job, body.job_id)
    app = Application(user_id=user.id, **body.model_dump())
    app.events.append(ApplicationEvent(from_status=None, to_status=body.status))
    if body.status == ProcessStatus.postule and app.applied_on is None:
        app.applied_on = date.today()
    db.add(app)
    try:
        db.commit()
    except IntegrityError:
        raise HTTPException(409, "Un process existe déjà pour ce poste") from None
    return ApplicationOut.model_validate(_load_application(db, app.id, user))


@router.patch("/applications/{app_id}")
def update_application(app_id: int, body: ApplicationPatch, db: DB, user: Me) -> ApplicationOut:
    return services.update_application(db, user, app_id, body)


@router.delete("/applications/{app_id}", status_code=204)
def delete_application(app_id: int, db: DB, user: Me) -> Response:
    db.delete(_load_application(db, app_id, user))
    db.commit()
    return Response(status_code=204)


# --- Tableau de bord / analytics --------------------------------------------


@router.get("/dashboard")
def dashboard(db: DB, user: Me) -> DashboardOut:
    return services.dashboard(db, user)


@router.get("/analytics")
def analytics(db: DB, user: Me) -> AnalyticsOut:
    jobs = list(db.scalars(select(Job).options(selectinload(Job.company))))
    apps = _user_apps(db, user)

    by_status = Counter(apps[j.id].status if j.id in apps else ProcessStatus.nouveau for j in jobs)

    # Entonnoir : process ayant atteint au moins une fois chaque étape
    reached: dict[int, set[ProcessStatus]] = {a.id: {e.to_status for e in a.events} | {a.status} for a in apps.values()}
    applied_stages = ACTIVE_STATUSES | {ProcessStatus.refuse}
    funnel = [
        FunnelStep(label="Postes suivis", count=len(jobs)),
        FunnelStep(label="Candidatures", count=sum(1 for s in reached.values() if s & applied_stages)),
        FunnelStep(label="Entretiens", count=sum(1 for s in reached.values() if ProcessStatus.entretien in s)),
        FunnelStep(label="Offres", count=sum(1 for s in reached.values() if ProcessStatus.offre in s)),
    ]

    companies: dict[int, CompanyActivity] = {}
    for job in jobs:
        row = companies.setdefault(
            job.company_id, CompanyActivity(company=CompanyMini.model_validate(job.company), jobs=0, processes=0)
        )
        row.jobs += 1
        if job.id in apps and apps[job.id].status != ProcessStatus.nouveau:
            row.processes += 1

    # Activité (changements de statut) sur les 8 dernières semaines, semaine commençant le lundi
    this_monday = date.today() - timedelta(days=date.today().weekday())
    weeks = [this_monday - timedelta(weeks=i) for i in range(7, -1, -1)]
    per_week = Counter(
        e.created_at.date() - timedelta(days=e.created_at.weekday()) for a in apps.values() for e in a.events
    )

    return AnalyticsOut(
        by_status=[StatusCount(status=s, count=by_status.get(s, 0)) for s in ProcessStatus],
        funnel=funnel,
        by_company=sorted(companies.values(), key=lambda r: (-r.processes, -r.jobs, r.company.name)),
        weekly=[WeekActivity(week=w, events=per_week.get(w, 0)) for w in weeks],
    )
