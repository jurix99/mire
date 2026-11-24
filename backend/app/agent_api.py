"""API d'ingestion de l'agent de recherche (/api/agent), protégée par jeton (pas la session LinkedIn).

L'agent (Claude Code lancé en local, voir agent/ et docs/agent.md) lit le contexte, ajoute entreprises, offres
et sources, et enregistre ses passages. Garde-fous (docs/processus-recherche.md) :
- jeton AGENT_TOKEN (Authorization: Bearer …), comparé en temps constant ; sans jeton configuré : 503 ;
- aucune suppression ; une source n'est jamais désactivée par l'agent ; il ne complète que les champs vides
  des entreprises (les valeurs saisies par un utilisateur sont conservées) ;
- dédoublonnage (URL normalisée, entreprise + titre) : une offre déjà vue n'est jamais recréée (409) ;
- offre encore listée par l'entreprise (open_verified) : acceptée quelle que soit sa date de publication ;
  ouverture non vérifiée : acceptée avec l'indicateur, sauf si elle a été publiée il y a plus de 2 mois (une
  vieille annonce de job board, absente du site de l'entreprise, est le plus souvent pourvue) ;
- l'agent ne touche à aucune donnée propre à un utilisateur (statuts, notes, profil) : l'évaluation est faite
  par Mire, pour chaque utilisateur, avec son profil.
"""

import os
import secrets
import time
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import assessments, ats, services, tasks
from .db import get_db
from .dedup import normalize_url
from .llm import LLM, get_llm
from .models import Company, Job, JobAssessment, SearchRun, Source, User
from .profile import get_profile, job_labels, profile_data, profile_summary
from .radar_api import CheckOut, check_and_commit
from .schemas import HttpUrl, JobIn, ShortText, Text

MAX_AGE_UNVERIFIED = timedelta(days=61)  # offre NON vérifiée ouverte, publiée il y a plus de 2 mois : refusée
SOURCE_KINDS = ("ats", "job_board", "vc_board", "funding_radar", "recruiter", "google_query", "autre")


def agent_token() -> str:
    return os.environ.get("AGENT_TOKEN", "").strip()


def require_agent(request: Request) -> None:
    expected = agent_token()
    if not expected:
        raise HTTPException(503, "API de l'agent désactivée : AGENT_TOKEN n'est pas configuré dans .env")
    auth = request.headers.get("authorization", "")
    given = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    if not given or not secrets.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(401, "Jeton de l'agent invalide")


router = APIRouter(prefix="/api/agent", dependencies=[Depends(require_agent)])
DB = Annotated[Session, Depends(get_db)]


# --- Contexte -----------------------------------------------------------------------------


@router.get("/context")
def context(db: DB) -> dict:
    """Tout ce qu'il faut pour un passage : sources actives, entreprises, offres connues, profils (anonymisés)."""
    sources = db.scalars(select(Source).where(Source.active.is_(True)).order_by(Source.kind, Source.name)).all()
    companies = db.scalars(select(Company).order_by(Company.name)).all()
    jobs = db.scalars(select(Job).options(selectinload(Job.company))).all()
    profiles = []
    for user in db.scalars(select(User)).all():
        data = profile_data(get_profile(db, user))
        if data.skills or data.experiences or data.positioning:
            label = SimpleNamespace(name=f"Utilisateur {user.id}", given_name=None)  # pas de nom ni d'e-mail
            profiles.append({"user_id": user.id, "profil": profile_summary(label, data, job_labels(db, data))})
    runs = db.scalars(select(SearchRun).order_by(SearchRun.id.desc()).limit(3)).all()
    return {
        "date": date.today().isoformat(),
        "sources": [{"id": s.id, "nom": s.name, "type": s.kind, "requete": s.query, "lecture_auto": s.auto_readable,
                     "notes": s.notes, "offres_trouvees": s.jobs_found} for s in sources],
        "entreprises": [{
            "id": c.id, "nom": c.name, "site": c.website, "ats": c.ats, "ats_slug": c.ats_slug,
            "carrieres": c.careers_url, "stade": c.funding_stage, "levee_date": c.funding_announced_on,
            "verifiee_le": c.last_checked_at, "sans_offre_pertinente_le": c.no_relevant_jobs_at,
            # ATS lu chaque jour par Mire (collecte) : rien à faire pour ses offres
            "ats_lisible": c.ats in ats.SUPPORTED and bool(c.ats_slug),
        } for c in companies],
        # Offres déjà vues (jamais à reproposer, quel que soit leur statut)
        "offres_connues": [{"id": j.id, "entreprise_id": j.company_id, "entreprise": j.company.name, "titre": j.title,
                            "url": j.url, "source": j.source,
                            "fermee": j.closed_at is not None} for j in jobs],
        "profils": profiles,
        "derniers_passages": [{"id": r.id, "type": r.kind, "debut": r.started_at, "statut": r.status,
                               "resume": r.summary} for r in runs],
    }


# --- Passages ------------------------------------------------------------------------------


class RunIn(BaseModel):
    # offres : passage qui lit les pages carrières que Mire ne sait pas lire seul (Welcome to the Jungle, Notion…)
    # recherche : passage qui part des offres correspondant aux profils pour trouver des entreprises inconnues
    kind: Literal["decouverte", "planifie", "manuel", "approfondi", "offres", "recherche"] = "decouverte"


class RunFinishIn(BaseModel):
    status: Literal["termine", "echec"] = "termine"
    jobs_seen: Annotated[int, Field(ge=0)] = 0
    companies_checked: Annotated[int, Field(ge=0)] = 0
    summary: Annotated[str | None, Field(default=None, max_length=5000)] = None
    errors: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list, max_length=50)


def _run(db: Session, run_id: int | None) -> SearchRun | None:
    if run_id is None:
        return None
    run = db.get(SearchRun, run_id)
    if run is None:
        raise HTTPException(404, "Passage introuvable")
    if run.status != "en_cours":
        raise HTTPException(409, "Passage déjà terminé")
    return run


def _add_to_run(run: SearchRun | None, key: str, value) -> None:
    if run is not None:
        run.details = {**(run.details or {}), key: [*(run.details or {}).get(key, []), value]}


@router.post("/runs", status_code=201)
def start_run(body: RunIn, db: DB) -> dict:
    run = SearchRun(kind=body.kind, status="en_cours", details={}, jobs_seen=0, jobs_added=0, jobs_duplicates=0,
                    jobs_closed=0, companies_checked=0, sources_added=0)
    db.add(run)
    db.commit()
    return {"run_id": run.id}


@router.post("/runs/{run_id}/finish")
def finish_run(run_id: int, body: RunFinishIn, db: DB, llm: Annotated[LLM | None, Depends(get_llm)]) -> dict:
    """Clôt le passage : évalue tout de suite les offres ajoutées pour chaque utilisateur, puis résume.

    Synchrone (exception à la règle « traitement de fond = tâche en file ») : l'agent attend le résumé par
    utilisateur. Borné à FINISH_BUDGET_S secondes (l'ingress Container Apps coupe à 240 s) ; le reste est
    traité par la file (tâche embed_jobs -> présélection -> évaluation), mise en file dans tous les cas.
    """
    run = _run(db, run_id)
    run.status, run.finished_at = body.status, datetime.now(UTC)
    run.jobs_seen = max(run.jobs_seen, body.jobs_seen)
    run.companies_checked = max(run.companies_checked, body.companies_checked)
    run.details = {**(run.details or {}), "errors": body.errors}
    db.commit()

    added = list((run.details or {}).get("jobs_added", []))
    deadline = time.monotonic() + float(os.environ.get("FINISH_BUDGET_S", "90"))
    per_user = []
    for user in db.scalars(select(User)).all():
        if added and llm is not None and time.monotonic() < deadline:
            try:  # seulement les offres de ce passage, dans le quota hebdomadaire des Free
                assessments.assess_within_plan(db, user, llm, len(added), job_ids=added)
            except HTTPException:  # quota / Gemini indisponible : la file reprendra
                db.rollback()
        rows = db.scalars(select(JobAssessment).where(JobAssessment.user_id == user.id,
                                                      JobAssessment.job_id.in_(added or [-1]))
                          .options(selectinload(JobAssessment.job).selectinload(Job.company))).all()
        kept = sorted([a for a in rows if a.verdict == "nouveau"], key=lambda a: -(float(a.score or 0)))
        per_user.append({
            "user_id": user.id, "nouvelles": len(kept), "ecartees": sum(a.verdict == "ecartee" for a in rows),
            "en_attente": len(added) - len(rows),
            "top": [{"note": float(a.score) if a.score is not None else None, "titre": a.job.title,
                     "entreprise": a.job.company.name, "pourquoi": a.why, "lien": a.job.url} for a in kept[:5]],
        })
    lines = [body.summary or "",
             f"{run.jobs_seen} offres vues, {run.jobs_added} ajoutées, {run.jobs_duplicates} déjà connues, "
             f"{run.jobs_closed} fermées, {run.sources_added} sources ajoutées."]
    run.summary = "\n".join(filter(None, lines))
    run.details = {**run.details, "per_user": per_user}
    db.commit()
    if added:
        tasks.jobs_changed(db)  # embeddings des nouvelles offres, puis présélection et évaluation restantes
    return {"run_id": run.id, "summary": run.summary, "par_utilisateur": per_user}


# --- Entreprises ----------------------------------------------------------------------------


class AgentCompanyIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    website: HttpUrl
    sector: ShortText
    city: ShortText
    funding_stage: ShortText
    funding_amount_meur: Annotated[float | None, Field(default=None, ge=0)] = None
    funding_announced_on: date | None = None
    investors: Text
    description: Text
    ats: Annotated[str | None, Field(default=None, pattern="^(ashby|lever|greenhouse|teamtailor|recruitee|yc|autre)$")] = None
    ats_slug: Annotated[str | None, Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")] = None
    careers_url: HttpUrl
    headcount: Annotated[str | None, Field(default=None, max_length=40)] = None


class CompanyCheckedIn(BaseModel):
    no_relevant_jobs: bool = False  # vérifiée sans offre pertinente : ne pas la revérifier au prochain passage


def _find_company(db: Session, name: str, website: str | None) -> Company | None:
    from .dedup import title_key

    key = title_key(name)
    for c in db.scalars(select(Company)).all():
        if title_key(c.name) == key:
            return c
        if website and c.website and normalize_url(c.website + "/x") == normalize_url(website + "/x"):
            return c
    return None


def _upsert(db: Session, body: AgentCompanyIn) -> dict:
    existing = _find_company(db, body.name, body.website)
    if existing is None:
        company = Company(**body.model_dump(), slug=services.slugify(db, body.name))
        db.add(company)
        db.flush()
        return {"id": company.id, "name": body.name, "created": True, "completed": []}
    completed = []
    for field, value in body.model_dump(exclude={"name"}).items():
        if value not in (None, "") and getattr(existing, field) in (None, ""):
            setattr(existing, field, value)
            completed.append(field)
    return {"id": existing.id, "name": existing.name, "created": False, "completed": completed}


@router.post("/companies")
def upsert_company(body: AgentCompanyIn, db: DB) -> dict:
    """Crée l'entreprise, ou complète les champs VIDES d'une entreprise existante (même nom ou même site)."""
    out = _upsert(db, body)
    db.commit()
    return out


class BulkCompaniesIn(BaseModel):
    companies: Annotated[list[AgentCompanyIn], Field(min_length=1, max_length=100)]
    run_id: int | None = None


@router.post("/companies/bulk")
def upsert_companies(body: BulkCompaniesIn, db: DB) -> dict:
    """Jusqu'à 100 entreprises en un appel (même règle : création, ou complément des champs vides)."""
    run = _run(db, body.run_id)
    results = [_upsert(db, c) for c in body.companies]
    if run is not None:
        run.companies_checked += len(results)
    db.commit()
    return {"created": sum(r["created"] for r in results), "existing": sum(not r["created"] for r in results),
            "companies": results}


@router.post("/companies/{company_id}/check")
def agent_check_company(company_id: int, db: DB, run_id: int | None = None) -> CheckOut:
    """Vérification par l'ATS (faite par Mire) : offres ouvertes, fermées, nouvelles."""
    run = _run(db, run_id)
    out = check_and_commit(db, services.get_or_404(db, Company, company_id))
    if run is not None:
        run.companies_checked += 1
        run.jobs_closed += len(out.closed)
        db.commit()
    return out


class AtsIn(BaseModel):
    ats: Literal[ats.SUPPORTED]  # type: ignore[valid-type]  (ashby, lever, greenhouse, recruitee, teamtailor)
    slug: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]


@router.post("/companies/{company_id}/ats")
def set_company_ats(company_id: int, body: AtsIn, db: DB) -> dict:
    """Mire teste l'ATS + identifiant proposés : enregistrés seulement si le tableau répond et contient les offres
    de CETTE entreprise. Une valeur existante n'est remplacée que si elle ne fonctionne pas."""
    company = services.get_or_404(db, Company, company_id)
    try:
        postings = ats.fetch_postings(body.ats, body.slug)
    except ats.ATSError as exc:
        return {"ok": False, "error": str(exc)}
    if not postings:
        # Un tableau vide ne prouve rien (n'importe quel identifiant inutilisé peut répondre) : refusé
        return {"ok": False, "error": "Tableau vide : impossible de vérifier qu'il appartient à cette entreprise. "
                                      "Renseignez plutôt careers_url ; Mire réessaiera la détection plus tard."}
    if not ats.mentions_company(company, postings):
        return {"ok": False, "error": "Ce tableau ne semble pas être celui de cette entreprise (nom absent des offres)"}
    if company.ats in ats.SUPPORTED and company.ats_slug and (company.ats, company.ats_slug) != (body.ats, body.slug):
        try:
            ats.fetch_postings(company.ats, company.ats_slug)
            return {"ok": False, "error": f"Un ATS fonctionnel est déjà enregistré ({company.ats}/{company.ats_slug})"}
        except ats.ATSError:
            pass  # l'ancien identifiant ne fonctionne pas : on le remplace
    company.ats, company.ats_slug = body.ats, body.slug
    db.commit()
    # Collecte de cette entreprise tout de suite, en tâche de fond (sans attendre la collecte quotidienne)
    tasks.enqueue(db, "check_company", {"company_id": company.id}, dedup_key=f"check_company:{company.id}")
    return {"ok": True, "postings": len(postings), "note": "Les offres seront collectées par Mire (tâche de fond)"}


@router.post("/companies/{company_id}/checked")
def mark_company_checked(company_id: int, body: CompanyCheckedIn, db: DB) -> dict:
    company = services.get_or_404(db, Company, company_id)
    company.last_checked_at = datetime.now(UTC)
    if body.no_relevant_jobs:
        company.no_relevant_jobs_at = company.last_checked_at
    db.commit()
    return {"id": company.id}


# --- Offres -----------------------------------------------------------------------------------


class AgentJobIn(JobIn):
    open_verified: bool = Field(False, description="Présente dans la liste actuelle des offres de l'entreprise")
    ats_job_id: Annotated[str | None, Field(default=None, max_length=200)] = None
    source_id: int | None = None
    run_id: int | None = None


@router.post("/jobs", status_code=201)
def add_job(body: AgentJobIn, db: DB) -> dict:
    run = _run(db, body.run_id)
    if not body.open_verified and body.published_on and body.published_on < date.today() - MAX_AGE_UNVERIFIED:
        raise HTTPException(422, "Offre publiée il y a plus de 2 mois et absente de la liste des offres de "
                                 "l'entreprise : non ajoutée")
    if body.source_id is not None:
        services.get_or_404(db, Source, body.source_id)
    fields = JobIn.model_validate(body.model_dump(include=set(JobIn.model_fields)))
    try:
        out = services.create_job(
            db, fields, ats_job_id=body.ats_job_id, source_id=body.source_id,
            verified_open_at=datetime.now(UTC) if body.open_verified else None,
        )
    except services.DuplicateJob as exc:
        if run is not None:
            run.jobs_duplicates += 1
            db.commit()
        raise HTTPException(409, {"message": exc.detail, "existing_id": exc.job.id}) from None
    if body.source_id is not None:
        source = db.get(Source, body.source_id)
        source.jobs_found += 1
        source.last_used_at = datetime.now(UTC)
    if run is not None:
        run.jobs_added += 1
        _add_to_run(run, "jobs_added", out.id)
    db.commit()
    tasks.jobs_changed(db)
    return {"id": out.id, "open_verified": body.open_verified}


@router.post("/jobs/{job_id}/close")
def close_job(job_id: int, db: DB, run_id: int | None = None) -> dict:
    """L'offre n'est plus dans la liste de l'entreprise : fermée (affichée « Offre fermée » si non traitée)."""
    run = _run(db, run_id)
    job = services.get_or_404(db, Job, job_id)
    if job.closed_at is None:
        job.closed_at = datetime.now(UTC)
        if run is not None:
            run.jobs_closed += 1
    db.commit()
    return {"id": job.id, "closed_at": job.closed_at}


# --- Sources ---------------------------------------------------------------------------------


class AgentSourceIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    kind: Literal[SOURCE_KINDS]  # type: ignore[valid-type]
    query: Annotated[str | None, Field(default=None, max_length=2000)] = None
    auto_readable: bool = True
    notes: Annotated[str | None, Field(default=None, max_length=2000)] = None
    run_id: int | None = None


class AgentSourcePatch(BaseModel):
    query: Annotated[str | None, Field(default=None, max_length=2000)] = None  # seulement si vide
    notes_append: Annotated[str | None, Field(default=None, max_length=1000)] = None
    used: bool = False  # marque la source comme lue lors de ce passage


@router.post("/sources")
def add_source(body: AgentSourceIn, db: DB) -> dict:
    """Ajoute une source (ajoutée par : agent). Si elle existe déjà, ne complète que son URL vide."""
    run = _run(db, body.run_id)
    existing = db.scalar(select(Source).where(func.lower(Source.name) == body.name.lower()))
    if existing is not None:
        if body.query and not existing.query:
            existing.query = body.query
        db.commit()
        return {"id": existing.id, "created": False}
    source = Source(name=body.name, kind=body.kind, query=body.query, auto_readable=body.auto_readable,
                    notes=body.notes, added_by="agent", active=True, jobs_found=0)
    db.add(source)
    if run is not None:
        run.sources_added += 1
    db.commit()
    return {"id": source.id, "created": True}


@router.patch("/sources/{source_id}")
def update_source(source_id: int, body: AgentSourcePatch, db: DB) -> dict:
    """Complète une source : URL manquante, notes (ajoutées à la suite). Jamais de désactivation."""
    source = services.get_or_404(db, Source, source_id)
    if body.query and not source.query:
        source.query = body.query
    if body.notes_append:
        stamp = date.today().strftime("%d/%m")
        source.notes = f"{source.notes}\n{stamp} : {body.notes_append}" if source.notes else f"{stamp} : {body.notes_append}"
    if body.used:
        source.last_used_at = datetime.now(UTC)
    db.commit()
    return {"id": source.id}

