"""API JSON du site (/api/...). Toutes les routes exigent un utilisateur connecté."""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, undefer

from . import entitlements, logos, ranking, services, tasks
from . import taxonomy as tx
from .assessments_api import invalidate_job
from .db import get_db
from .entitlements import RequireAdmin
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
    BillingOut,
    CompanyActivity,
    CompanyIn,
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
    Option,
    OptionsOut,
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
            Application.user_id == user.id,
            # « Pas intéressé » n'est pas une candidature (voir Offres > Écartées)
            Application.status.not_in((ProcessStatus.nouveau, ProcessStatus.pas_interesse)),
        )
    )
    counts = NavCounts(
        companies=db.scalar(select(func.count(Company.id))) or 0,
        jobs=db.scalar(select(func.count(Job.id))) or 0,
        processes=processes or 0,
    )
    user_out = UserOut.model_validate(user)
    user_out.is_admin = entitlements.is_admin(user)
    return MeOut(user=user_out, counts=counts,
                 billing=BillingOut(**entitlements.billing_summary(db, user)))


@router.get("/statuses")
def statuses() -> list[StatusOut]:
    return [StatusOut(key=s, label=STATUS_LABELS[s]) for s in ProcessStatus]


# --- Entreprises -------------------------------------------------------------


@router.get("/companies")
def list_companies(db: DB, user: Me) -> list[CompanyOut]:
    access = entitlements.access_for(db, user)
    if access.full:
        order = (Company.funding_announced_on.desc().nulls_last(), Company.name)
    else:
        order = (Company.name,)  # l'ordre des levées trahirait leurs dates
    companies = list(db.scalars(select(Company).order_by(*order)))
    if not access.full:
        # Entreprises floutées en dernier, triées par id : l'ordre alphabétique trahirait leur nom
        companies.sort(key=lambda c: (True, c.id) if access.company_is_locked(c.id) else (False, 0))
    return services.ranked_companies(db, user, companies)


@router.post("/companies", status_code=201, dependencies=[RequireAdmin])
def create_company(body: CompanyIn, db: DB, user: Me) -> CompanyOut:
    company = Company(**body.model_dump(), slug=_slugify(db, body.name))
    db.add(company)
    db.commit()
    return _company_out(company, 0, entitlements.access_for(db, user))


@router.get("/companies/{company_id}")
def get_company(company_id: int, db: DB, user: Me) -> CompanyOut:
    return services.ranked_companies(db, user, [_get_or_404(db, Company, company_id)])[0]


@router.patch("/companies/{company_id}", dependencies=[RequireAdmin])
def update_company(company_id: int, body: CompanyPatch, db: DB, user: Me) -> CompanyOut:
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
    access = entitlements.access_for(db, user)
    return _company_out(company, _jobs_count_by_company(db, access).get(company_id, 0), access)


@router.delete("/companies/{company_id}", status_code=204, dependencies=[RequireAdmin])
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
def company_logo(company_id: int, db: DB, user: Me) -> Response:
    company = db.scalar(select(Company).where(Company.id == company_id).options(undefer(Company.logo_data)))
    if company is None:
        raise HTTPException(404, "Introuvable")
    if entitlements.access_for(db, user).company_is_locked(company_id):
        return Response(status_code=404, headers={"Cache-Control": "no-store"})  # entreprise floutée (Free)
    if logos.should_fetch(company):
        found = logos.fetch_logo(company)
        company.logo_fetched_at = datetime.now(UTC)
        if found:
            company.logo_data, company.logo_content_type = found
        db.commit()
    if company.logo_data is None:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    return Response(company.logo_data, media_type=company.logo_content_type, headers=LOGO_HEADERS)


@router.put("/companies/{company_id}/logo", status_code=204, dependencies=[RequireAdmin])
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


@router.delete("/companies/{company_id}/logo", status_code=204, dependencies=[RequireAdmin])
def refresh_company_logo(company_id: int, db: DB) -> Response:
    """Oublie le logo en cache : il sera récupéré à nouveau au prochain affichage."""
    logos.reset_logo(_get_or_404(db, Company, company_id))
    db.commit()
    return Response(status_code=204)


# --- Postes ------------------------------------------------------------------


@router.get("/jobs")
def list_jobs(db: DB, user: Me, company_id: int | None = None, brief: bool = False) -> list[JobOut]:
    """Offres classées par pertinence. brief : sans la description (listes : plusieurs Mo en moins)."""
    jobs = services.list_jobs(db, user, company_id=company_id)
    if brief:
        for job in jobs:
            job.summary = None
    return jobs


@router.get("/options")
def options(db: DB) -> OptionsOut:
    """Choix proposés par les filtres et par le profil : liste fermée, et lieux des offres ouvertes."""
    cities = Counter(city for location in db.scalars(select(Job.location).where(Job.closed_at.is_(None)))
                     if (city := ranking.city_of(location)))
    return OptionsOut(
        families=[Option(key=fid, label=label, group=tx.DOMAINS[domain])
                  for fid, label, domain, _ in tx.ROLE_FAMILIES],
        sectors=[Option(key=sid, label=label) for sid, label, _ in tx.SECTORS],
        seniority=[Option(key=k, label=v) for k, v in tx.SENIORITY_LEVELS.items() if k != "inconnu"],
        skills=sorted((label for _, label, family, *_ in tx.SKILLS if family != "langues"), key=str.lower),
        locations=[city for city, _ in cities.most_common(40)],
    )


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: DB, user: Me) -> JobDetailOut:
    """Détail d'un poste (contenu de l'offre) et process de l'utilisateur pour ce poste, s'il existe."""
    job = services.get_job(db, user, job_id)
    app = services.load_application(db, job.application_id, user) if job.application_id else None
    application = services.application_out(app, entitlements.access_for(db, user)) if app else None
    return JobDetailOut(**job.model_dump(), application=application)


@router.get("/jobs/{job_id}/similar")
def similar_jobs(job_id: int, db: DB, user: Me) -> list[JobOut]:
    """Les 5 offres ouvertes les plus proches (similarité des embeddings, requête SQL, sans LLM).
    Vide tant que l'offre n'a pas d'embedding (calculé par le worker)."""
    from . import embeddings

    services.get_job(db, user, job_id)  # 404 si l'offre n'existe pas
    return [services.get_job(db, user, other) for other, _ in embeddings.similar_jobs(db, job_id)]


@router.post("/jobs", status_code=201, dependencies=[RequireAdmin])
def create_job(body: JobIn, db: DB) -> JobOut:
    out = services.create_job(db, body)
    tasks.jobs_changed(db)  # embeddings, présélection et évaluation de la nouvelle offre pour chaque utilisateur
    return out


@router.patch("/jobs/{job_id}", dependencies=[RequireAdmin])
def update_job(job_id: int, body: JobPatch, db: DB, user: Me) -> JobOut:
    job = _get_or_404(db, Job, job_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("company_id", job.company_id) is None or ("title" in changes and changes["title"] is None):
        raise HTTPException(422, "Entreprise et intitulé obligatoires")
    if "company_id" in changes:
        _get_or_404(db, Company, changes["company_id"])
    if {"company_id", "title", "url"} & changes.keys():
        dup = services.find_duplicate(db, changes.get("company_id", job.company_id), changes.get("title", job.title),
                                      changes.get("url", job.url), exclude_id=job.id)
        if dup:
            raise services.DuplicateJob(dup)
    for field, value in changes.items():
        setattr(job, field, value)
    invalidate_job(db, job.id)  # contenu modifié : réévaluer chez ceux qui ne l'ont pas traitée
    db.commit()
    tasks.jobs_changed(db)
    return services.get_job(db, user, job.id)


@router.delete("/jobs/{job_id}", status_code=204, dependencies=[RequireAdmin])
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
    job = _get_or_404(db, Job, body.job_id)
    if entitlements.access_for(db, user).job_is_locked(job):  # Free : offre floutée (voir set_job_status)
        raise entitlements.pro_required("Offre réservée à l'offre Pro")
    services.check_manual_status(body.status)
    entitlements.check_process_change(db, user, None, body.status)
    app = Application(user_id=user.id, **body.model_dump())
    app.events.append(ApplicationEvent(from_status=None, to_status=body.status))
    if body.status == ProcessStatus.postule and app.applied_on is None:
        app.applied_on = date.today()
    db.add(app)
    try:
        db.commit()
    except IntegrityError:
        raise HTTPException(409, "Un process existe déjà pour ce poste") from None
    return services.application_out(_load_application(db, app.id, user), entitlements.access_for(db, user))


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
        # Candidatures préparées (« Prête à envoyer »), ou déjà envoyées : chaque étape inclut les suivantes
        FunnelStep(label="Prêtes à envoyer",
                   count=sum(1 for s in reached.values() if s & (applied_stages | {ProcessStatus.prete}))),
        FunnelStep(label="Candidatures", count=sum(1 for s in reached.values() if s & applied_stages)),
        FunnelStep(label="Entretiens", count=sum(1 for s in reached.values() if ProcessStatus.entretien in s)),
        FunnelStep(label="Offres", count=sum(1 for s in reached.values() if ProcessStatus.offre in s)),
    ]

    # Par entreprise : en Free, seulement les offres visibles (une offre floutée ne doit pas trahir son entreprise)
    access = entitlements.access_for(db, user)
    companies: dict[int, CompanyActivity] = {}
    for job in jobs:
        if access.job_is_locked(job):
            continue
        row = companies.setdefault(
            job.company_id, CompanyActivity(company=services.company_mini(job.company, access), jobs=0, processes=0)
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
        by_company=sorted(companies.values(), key=lambda r: (-r.processes, -r.jobs, str(r.company.name))),
        weekly=[WeekActivity(week=w, events=per_week.get(w, 0)) for w in weeks],
    )
