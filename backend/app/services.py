"""Fonctions métier partagées par l'API (api.py) et les outils du chat (chat_tools.py).

Toutes les fonctions qui touchent aux process prennent l'utilisateur courant en paramètre
et ne lisent / modifient que ses données. Les erreurs sont des ServiceError (= HTTPException),
renvoyées telles quelles par l'API et transformées en message d'erreur par le chat.
"""

import re
import unicodedata
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    ACTIVE_STATUSES,
    TODO_STATUSES,
    Application,
    ApplicationEvent,
    Company,
    Job,
    JobAssessment,
    ProcessStatus,
    User,
)
from .schemas import (
    ApplicationOut,
    ApplicationPatch,
    AssessmentOut,
    CompanyOut,
    CriterionOut,
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


@dataclass
class AssessmentContext:
    """Évaluations de l'utilisateur + barème et version de son profil (pour construire les JobOut)."""

    assessments: dict[int, JobAssessment] = dc_field(default_factory=dict)
    weights: dict[str, float] = dc_field(default_factory=dict)
    version: int = 1


def assessment_context(db: Session, user: User) -> AssessmentContext:
    from .profile import get_profile, profile_data

    profile = get_profile(db, user)
    rows = db.scalars(select(JobAssessment).where(JobAssessment.user_id == user.id))
    return AssessmentContext({a.job_id: a for a in rows}, profile_data(profile).scoring.weights, profile.version)


def assessment_out(a: JobAssessment, ctx: AssessmentContext, treated: bool = False) -> AssessmentOut:
    from .profile import CRITERIA

    auto = float(a.score) if a.score is not None else None
    manual = float(a.manual_score) if a.manual_score is not None else None
    return AssessmentOut(
        score=manual if manual is not None else auto, auto_score=auto, manual_score=manual,
        verdict=a.verdict, exclusion=a.exclusion, why=a.why,
        criteria=[CriterionOut(key=k, label=CRITERIA[k][0], points=float(a.criteria.get(k, 0)), max=w)
                  for k, w in ctx.weights.items() if a.criteria],
        skills_have=a.skills_have or [], skills_missing=a.skills_missing or [],
        # À réévaluer : même règle que assessments.jobs_to_assess (jamais si traitée ou note corrigée)
        stale=a.profile_version < ctx.version and not treated and a.manual_score is None,
        assessed_at=a.assessed_at,
    )


def job_out(job: Job, app: Application | None, ctx: AssessmentContext | None = None) -> JobOut:
    out = JobOut.model_validate(job)
    if app is not None:
        out.status = app.status
        out.application_id = app.id
    a = ctx.assessments.get(job.id) if ctx else None
    if a is not None:
        out.assessment = assessment_out(a, ctx, treated=app is not None)
        out.score = out.assessment.score
    # Statut affiché : l'utilisateur a toujours le dernier mot (règle d'or)
    if app is not None:
        out.display_status = app.status.value
    elif job.closed_at is not None:
        out.display_status = "fermee"  # offre fermée, pas encore traitée par l'utilisateur
    else:
        out.display_status = a.verdict if a is not None else "nouveau"
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


def _by_score(jobs: list[JobOut]) -> list[JobOut]:
    return sorted(jobs, key=lambda j: (j.score is None, -(j.score or 0), j.title.lower()))


def list_jobs(
    db: Session,
    user: User,
    *,
    company_id: int | None = None,
    company_name: str | None = None,
    status: ProcessStatus | str | None = None,
    min_score: float | None = None,
) -> list[JobOut]:
    """Postes avec le statut et la note DE L'UTILISATEUR, triés par note décroissante.

    status : statut affiché (statut posé par l'utilisateur, ou verdict « nouveau » / « ecartee »).
    """
    query = select(Job).options(selectinload(Job.company))
    if company_id is not None:
        query = query.where(Job.company_id == company_id)
    if company_name:
        query = query.join(Company).where(Company.name.ilike(f"%{company_name.strip()}%"))
    apps = user_apps(db, user)
    ctx = assessment_context(db, user)
    out = [job_out(job, apps.get(job.id), ctx) for job in db.scalars(query)]
    if status is not None:
        wanted = status.value if isinstance(status, ProcessStatus) else status
        out = [j for j in out if j.display_status == wanted]
    if min_score is not None:
        out = [j for j in out if j.score is not None and j.score >= min_score]
    return _by_score(out)


def get_job(db: Session, user: User, job_id: int) -> JobOut:
    job = db.scalar(select(Job).where(Job.id == job_id).options(selectinload(Job.company)))
    if job is None:
        raise ServiceError(404, "Poste introuvable")
    return job_out(job, user_apps(db, user).get(job_id), assessment_context(db, user))


def find_duplicate(db: Session, company_id: int, title: str, url: str | None,
                   exclude_id: int | None = None) -> Job | None:
    """Offre déjà connue : même URL normalisée, ou même entreprise + même titre normalisé."""
    from .dedup import normalize_url, title_key

    conditions = []
    if (key := normalize_url(url)) is not None:
        conditions.append(Job.url_key == key)
    if (tkey := title_key(title)) is not None:
        conditions.append((Job.company_id == company_id) & (Job.title_key == tkey))
    if not conditions:
        return None
    query = select(Job).where(or_(*conditions))
    if exclude_id is not None:
        query = query.where(Job.id != exclude_id)
    return db.scalars(query.limit(1)).first()


class DuplicateJob(ServiceError):
    def __init__(self, job: Job):
        super().__init__(409, f"Ce poste existe déjà (#{job.id} : {job.title}).")
        self.job = job


def create_job(db: Session, body: JobIn, **extra) -> JobOut:
    get_or_404(db, Company, body.company_id)
    if dup := find_duplicate(db, body.company_id, body.title, body.url):
        raise DuplicateJob(dup)
    job = Job(**body.model_dump(), **extra)
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
    return job_out(job, app, assessment_context(db, user))


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
    ctx = assessment_context(db, user)
    outs = [job_out(j, apps.get(j.id), ctx) for j in jobs]

    # À traiter : nouveau ou « à regarder », jamais les offres écartées par l'évaluation
    todo_keys = {s.value for s in TODO_STATUSES}
    todo = _by_score([o for o in outs if o.display_status in todo_keys])
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
        priority_jobs=todo[:3],
        processes=[ApplicationOut.model_validate(a) for a in active[:3]],
        recent_fundings=[company_out(c, counts.get(c.id, 0)) for c in fundings],
    )
