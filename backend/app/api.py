"""API JSON du site (/api/...). Toutes les routes exigent un utilisateur connecté."""

import re
import unicodedata
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, undefer

from . import logos
from .db import get_db
from .models import (
    ACTIVE_STATUSES,
    STATUS_LABELS,
    TODO_STATUSES,
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
    DashboardStats,
    FunnelStep,
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
from .users import current_user

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])

DB = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]


# --- Helpers -----------------------------------------------------------------


def _get_or_404[T](db: Session, model: type[T], obj_id: int) -> T:
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, "Introuvable")
    return obj


def _slugify(db: Session, name: str, exclude_id: int | None = None) -> str:
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "entreprise"
    slug, n = base, 2
    while True:
        existing = db.scalar(select(Company.id).where(Company.slug == slug))
        if existing is None or existing == exclude_id:
            return slug
        slug, n = f"{base}-{n}", n + 1


def _user_apps(db: Session, user: User) -> dict[int, Application]:
    apps = db.scalars(
        select(Application)
        .where(Application.user_id == user.id)
        .options(selectinload(Application.job).selectinload(Job.company), selectinload(Application.events))
    )
    return {a.job_id: a for a in apps}


def _job_out(job: Job, app: Application | None) -> JobOut:
    out = JobOut.model_validate(job)
    if app is not None:
        out.status = app.status
        out.application_id = app.id
    return out


def _company_out(company: Company, jobs_count: int) -> CompanyOut:
    out = CompanyOut.model_validate(company)
    out.jobs_count = jobs_count
    return out


def _jobs_count_by_company(db: Session) -> dict[int, int]:
    rows = db.execute(select(Job.company_id, func.count(Job.id)).group_by(Job.company_id))
    return dict(rows.tuples().all())


def _load_application(db: Session, app_id: int, user: User) -> Application:
    app = db.scalar(
        select(Application)
        .where(Application.id == app_id, Application.user_id == user.id)
        .options(selectinload(Application.job).selectinload(Job.company), selectinload(Application.events))
    )
    if app is None:
        raise HTTPException(404, "Process introuvable")
    return app


def _apply_status(app: Application, status: ProcessStatus, note: str | None = None) -> None:
    if app.status == status:
        return
    app.events.append(ApplicationEvent(from_status=app.status, to_status=status, note=note))
    app.status = status
    if status == ProcessStatus.postule and app.applied_on is None:
        app.applied_on = date.today()


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
    query = select(Job).options(selectinload(Job.company)).order_by(Job.score.desc().nulls_last(), Job.title)
    if company_id is not None:
        query = query.where(Job.company_id == company_id)
    apps = _user_apps(db, user)
    return [_job_out(job, apps.get(job.id)) for job in db.scalars(query)]


@router.post("/jobs", status_code=201)
def create_job(body: JobIn, db: DB) -> JobOut:
    _get_or_404(db, Company, body.company_id)
    job = Job(**body.model_dump())
    db.add(job)
    db.commit()
    db.refresh(job, ["company"])
    return _job_out(job, None)


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
    db.commit()
    db.refresh(job, ["company"])
    return _job_out(job, _user_apps(db, user).get(job.id))


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: int, db: DB) -> Response:
    db.delete(_get_or_404(db, Job, job_id))
    db.commit()
    return Response(status_code=204)


@router.put("/jobs/{job_id}/status")
def set_job_status(job_id: int, body: JobStatusIn, db: DB, user: Me) -> JobOut:
    """Change le statut d'un poste pour l'utilisateur (crée le process si besoin)."""
    job = _get_or_404(db, Job, job_id)
    app = _user_apps(db, user).get(job_id)
    if app is None:
        app = Application(user_id=user.id, job_id=job_id, status=ProcessStatus.nouveau)
        db.add(app)
    _apply_status(app, body.status)
    db.commit()
    db.refresh(job, ["company"])
    return _job_out(job, app)


# --- Process -----------------------------------------------------------------


@router.get("/applications")
def list_applications(db: DB, user: Me) -> list[ApplicationOut]:
    apps = [a for a in _user_apps(db, user).values() if a.status != ProcessStatus.nouveau]
    apps.sort(key=lambda a: (a.next_step_date or date.max, -a.updated_at.timestamp()))
    return [ApplicationOut.model_validate(a) for a in apps]


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
    app = _load_application(db, app_id, user)
    changes = body.model_dump(exclude_unset=True)
    status = changes.pop("status", None)
    for field, value in changes.items():
        setattr(app, field, value)
    if status is not None:
        _apply_status(app, status)
    db.commit()
    return ApplicationOut.model_validate(_load_application(db, app_id, user))


@router.delete("/applications/{app_id}", status_code=204)
def delete_application(app_id: int, db: DB, user: Me) -> Response:
    db.delete(_load_application(db, app_id, user))
    db.commit()
    return Response(status_code=204)


# --- Tableau de bord / analytics --------------------------------------------


@router.get("/dashboard")
def dashboard(db: DB, user: Me) -> DashboardOut:
    jobs = list(db.scalars(select(Job).options(selectinload(Job.company))))
    apps = _user_apps(db, user)
    status_of = {j.id: apps[j.id].status if j.id in apps else ProcessStatus.nouveau for j in jobs}

    todo = [j for j in jobs if status_of[j.id] in TODO_STATUSES]
    todo.sort(key=lambda j: -(j.score or 0))
    active = [a for a in apps.values() if a.status in ACTIVE_STATUSES]
    active.sort(key=lambda a: (a.next_step_date or date.max, -a.updated_at.timestamp()))

    counts = _jobs_count_by_company(db)
    fundings = db.scalars(
        select(Company).order_by(Company.funding_announced_on.desc().nulls_last(), Company.name).limit(3)
    )
    return DashboardOut(
        stats=DashboardStats(
            jobs=len(jobs),
            todo=len(todo),
            active=len(active),
            interviews=sum(1 for a in active if a.status == ProcessStatus.entretien),
            companies=db.scalar(select(func.count(Company.id))) or 0,
        ),
        priority_jobs=[_job_out(j, apps.get(j.id)) for j in todo[:3]],
        processes=[ApplicationOut.model_validate(a) for a in active[:3]],
        recent_fundings=[_company_out(c, counts.get(c.id, 0)) for c in fundings],
    )


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
