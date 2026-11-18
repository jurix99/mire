"""Fonctions métier partagées par l'API (api.py) et les outils du chat (chat_tools.py).

Toutes les fonctions qui touchent aux process prennent l'utilisateur courant en paramètre
et ne lisent / modifient que ses données. Les erreurs sont des ServiceError (= HTTPException),
renvoyées telles quelles par l'API et transformées en message d'erreur par le chat.
"""

import re
import unicodedata
from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    ACTIVE_STATUSES,
    TODO_STATUSES,
    Application,
    ApplicationEvent,
    Company,
    Job,
    ProcessStatus,
    User,
)
from .schemas import (
    ApplicationOut,
    ApplicationPatch,
    CompanyOut,
    DashboardOut,
    DashboardStats,
    JobIn,
    JobOut,
)


class ServiceError(HTTPException):
    """Erreur métier (introuvable, invalide…) avec un message lisible par l'utilisateur."""


# --- Helpers -----------------------------------------------------------------


def get_or_404[T](db: Session, model: type[T], obj_id: int) -> T:
    obj = db.get(model, obj_id)
    if obj is None:
        raise ServiceError(404, "Introuvable")
    return obj


def slugify(db: Session, name: str, exclude_id: int | None = None) -> str:
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "entreprise"
    slug, n = base, 2
    while True:
        existing = db.scalar(select(Company.id).where(Company.slug == slug))
        if existing is None or existing == exclude_id:
            return slug
        slug, n = f"{base}-{n}", n + 1


def user_apps(db: Session, user: User) -> dict[int, Application]:
    """Process de l'utilisateur, indexés par id de poste."""
    apps = db.scalars(
        select(Application)
        .where(Application.user_id == user.id)
        .options(selectinload(Application.job).selectinload(Job.company), selectinload(Application.events))
    )
    return {a.job_id: a for a in apps}


def job_out(job: Job, app: Application | None) -> JobOut:
    out = JobOut.model_validate(job)
    if app is not None:
        out.status = app.status
        out.application_id = app.id
    return out


def company_out(company: Company, jobs_count: int) -> CompanyOut:
    out = CompanyOut.model_validate(company)
    out.jobs_count = jobs_count
    return out


def jobs_count_by_company(db: Session) -> dict[int, int]:
    rows = db.execute(select(Job.company_id, func.count(Job.id)).group_by(Job.company_id))
    return dict(rows.tuples().all())


def load_application(db: Session, app_id: int, user: User) -> Application:
    app = db.scalar(
        select(Application)
        .where(Application.id == app_id, Application.user_id == user.id)
        .options(selectinload(Application.job).selectinload(Job.company), selectinload(Application.events))
    )
    if app is None:
        raise ServiceError(404, "Process introuvable")
    return app


def apply_status(app: Application, status: ProcessStatus, note: str | None = None) -> None:
    """Change le statut d'un process et l'historise dans application_events."""
    if app.status == status:
        return
    app.events.append(ApplicationEvent(from_status=app.status, to_status=status, note=note))
    app.status = status
    if status == ProcessStatus.postule and app.applied_on is None:
        app.applied_on = date.today()


# --- Postes ------------------------------------------------------------------


def list_jobs(
    db: Session,
    user: User,
    *,
    company_id: int | None = None,
    company_name: str | None = None,
    status: ProcessStatus | None = None,
    min_score: float | None = None,
) -> list[JobOut]:
    query = select(Job).options(selectinload(Job.company)).order_by(Job.score.desc().nulls_last(), Job.title)
    if company_id is not None:
        query = query.where(Job.company_id == company_id)
    if company_name:
        query = query.join(Company).where(Company.name.ilike(f"%{company_name.strip()}%"))
    if min_score is not None:
        query = query.where(Job.score >= min_score)
    apps = user_apps(db, user)
    out = [job_out(job, apps.get(job.id)) for job in db.scalars(query)]
    if status is not None:
        out = [j for j in out if j.status == status]
    return out


def get_job(db: Session, user: User, job_id: int) -> JobOut:
    job = db.scalar(select(Job).where(Job.id == job_id).options(selectinload(Job.company)))
    if job is None:
        raise ServiceError(404, "Poste introuvable")
    return job_out(job, user_apps(db, user).get(job_id))


def create_job(db: Session, body: JobIn) -> JobOut:
    get_or_404(db, Company, body.company_id)
    job = Job(**body.model_dump())
    db.add(job)
    db.commit()
    db.refresh(job, ["company"])
    return job_out(job, None)


def set_job_status(db: Session, user: User, job_id: int, status: ProcessStatus, note: str | None = None) -> JobOut:
    """Change le statut d'un poste pour l'utilisateur (crée le process si besoin)."""
    job = get_or_404(db, Job, job_id)
    app = user_apps(db, user).get(job_id)
    if app is None:
        app = Application(user_id=user.id, job_id=job_id, status=ProcessStatus.nouveau)
        db.add(app)
    apply_status(app, status, note)
    db.commit()
    db.refresh(job, ["company"])
    return job_out(job, app)


# --- Process -----------------------------------------------------------------


def list_applications(db: Session, user: User, status: ProcessStatus | None = None) -> list[ApplicationOut]:
    apps = [a for a in user_apps(db, user).values() if a.status != ProcessStatus.nouveau]
    if status is not None:
        apps = [a for a in apps if a.status == status]
    apps.sort(key=lambda a: (a.next_step_date or date.max, -a.updated_at.timestamp()))
    return [ApplicationOut.model_validate(a) for a in apps]


def update_application(db: Session, user: User, app_id: int, body: ApplicationPatch) -> ApplicationOut:
    app = load_application(db, app_id, user)
    changes = body.model_dump(exclude_unset=True)
    status = changes.pop("status", None)
    for field, value in changes.items():
        setattr(app, field, value)
    if status is not None:
        apply_status(app, status)
    db.commit()
    return ApplicationOut.model_validate(load_application(db, app_id, user))


# --- Tableau de bord ---------------------------------------------------------


def dashboard(db: Session, user: User) -> DashboardOut:
    jobs = list(db.scalars(select(Job).options(selectinload(Job.company))))
    apps = user_apps(db, user)
    status_of = {j.id: apps[j.id].status if j.id in apps else ProcessStatus.nouveau for j in jobs}

    todo = [j for j in jobs if status_of[j.id] in TODO_STATUSES]
    todo.sort(key=lambda j: -(j.score or 0))
    active = [a for a in apps.values() if a.status in ACTIVE_STATUSES]
    active.sort(key=lambda a: (a.next_step_date or date.max, -a.updated_at.timestamp()))

    counts = jobs_count_by_company(db)
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
        priority_jobs=[job_out(j, apps.get(j.id)) for j in todo[:3]],
        processes=[ApplicationOut.model_validate(a) for a in active[:3]],
        recent_fundings=[company_out(c, counts.get(c.id, 0)) for c in fundings],
    )
