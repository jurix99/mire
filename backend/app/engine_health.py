"""Santé du moteur : indicateurs pour l'exploitation (page Analytics des administrateurs, journaux du job quotidien).

Ce qui est mesuré, sur une fenêtre glissante (14 jours par défaut) :
- arrivée des offres : ajoutées et fermées par jour ;
- évaluations : nombre par jour, modèle utilisé, part de BASCULE sur un modèle de secours (le premier de
  GEMINI_EVAL_MODEL est le modèle nominal), délai médian entre l'arrivée d'une offre et sa première évaluation ;
- file et étapes en attente : tâches en file (la plus ancienne), en échec, offres sans embedding ou à classer ;
- retours des utilisateurs : « J'aime » contre « Pas intéressé », raisons les plus fréquentes ;
- derniers passages par type (collecte, découverte, recherche, offres hors ATS).
Puis des ALERTES à seuils fixes (ALERTS) : collecte arrêtée, découverte arrêtée (PC éteint, job en échec), file
bloquée, bascule fréquente, aucune offre nouvelle. Aucune donnée personnelle : que des comptes.
"""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Job, JobAssessment, JobFeedback, SearchRun, Task

# Seuils des alertes
COLLECT_MAX_AGE = timedelta(hours=36)
DISCOVERY_MAX_AGE = timedelta(days=8)
QUEUE_MAX_WAIT = timedelta(hours=1)
EMBED_MAX_WAIT = timedelta(hours=24)
FALLBACK_MAX_SHARE = 0.3
FALLBACK_MIN_SAMPLE = 20
NO_NEW_JOBS_DAYS = 3

DISCOVERY_KINDS = ("decouverte", "approfondi", "planifie", "manuel")
RUN_KINDS = ("collecte", "decouverte", "recherche", "offres")


def _aware(value: datetime | None) -> datetime | None:
    """SQLite rend des dates sans fuseau : elles sont en UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 1)


def _per_day(values: list[datetime], days: list[date]) -> list[int]:
    counts = Counter(_aware(v).date() for v in values)
    return [counts.get(d, 0) for d in days]


def _last_run(db: Session, kinds: tuple[str, ...], finished: bool = False) -> SearchRun | None:
    query = select(SearchRun).where(SearchRun.kind.in_(kinds))
    if finished:
        query = query.where(SearchRun.status == "termine")
    return db.scalars(query.order_by(SearchRun.started_at.desc(), SearchRun.id.desc()).limit(1)).first()


def engine_health(db: Session, days: int = 14, now: datetime | None = None,
                  primary_model: str | None = None) -> dict:
    from .classify import count_pending
    from .llm import GEMINI_EVAL_MODELS

    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    day_list = [(now - timedelta(days=i)).date() for i in reversed(range(days))]
    primary = primary_model or (GEMINI_EVAL_MODELS[0] if GEMINI_EVAL_MODELS else None)

    # --- Offres ---
    added = list(db.scalars(select(Job.created_at).where(Job.created_at >= since)))
    closed = list(db.scalars(select(Job.closed_at).where(Job.closed_at >= since)))
    open_jobs = db.scalar(select(func.count(Job.id)).where(Job.closed_at.is_(None))) or 0

    # --- Évaluations ---
    rows = db.execute(select(JobAssessment.assessed_at, JobAssessment.model)
                      .where(JobAssessment.assessed_at >= since)).all()
    models = Counter(m or "inconnu" for _, m in rows)
    llm_models = {m: n for m, n in models.items() if m != "regles"}
    known = {m: n for m, n in llm_models.items() if m not in ("gemini", "inconnu")}  # « gemini » : avant le suivi
    known_total = sum(known.values())
    fallback = sum(n for m, n in known.items() if m != primary)
    fallback_share = round(fallback / known_total, 3) if known_total else None

    # Délai entre l'arrivée d'une offre et sa première évaluation (tous utilisateurs confondus)
    first = db.execute(select(Job.created_at, func.min(JobAssessment.assessed_at))
                       .join(JobAssessment, JobAssessment.job_id == Job.id)
                       .where(Job.created_at >= since).group_by(Job.id)).all()
    delays = [_hours(_aware(at) - _aware(created)) for created, at in first if at and created]

    # --- File et étapes en attente ---
    queued = db.execute(select(func.count(Task.id), func.min(Task.run_after))
                        .where(Task.status == "queued", Task.run_after <= now)).one()
    failed_24h = db.scalar(select(func.count(Task.id)).where(
        Task.status == "failed", Task.updated_at >= now - timedelta(hours=24))) or 0
    embed_pending = db.execute(select(func.count(Job.id), func.min(Job.created_at)).where(
        Job.embedded_at.is_(None), Job.closed_at.is_(None))).one()

    # --- Retours ---
    feedback = db.execute(select(JobFeedback.sentiment, JobFeedback.reasons)
                          .where(JobFeedback.created_at >= since)).all()
    likes = sum(s == "like" for s, _ in feedback)
    dislikes = sum(s == "dislike" for s, _ in feedback)
    reasons = Counter(r for s, rs in feedback if s == "dislike" for r in (rs or []))

    # --- Passages ---
    runs = {}
    for kind in RUN_KINDS:
        run = _last_run(db, DISCOVERY_KINDS if kind == "decouverte" else (kind,))
        runs[kind] = None if run is None else {
            "debut": _aware(run.started_at), "statut": run.status, "offres_ajoutees": run.jobs_added,
            "entreprises": run.companies_checked, "il_y_a_h": _hours(now - _aware(run.started_at)),
        }

    health = {
        "fenetre_jours": days,
        "jours": [d.isoformat() for d in day_list],
        "offres": {"ajoutees": _per_day(added, day_list), "fermees": _per_day(closed, day_list), "ouvertes": open_jobs},
        "evaluations": {
            "par_jour": _per_day([at for at, _ in rows], day_list), "total": len(rows),
            "par_modele": dict(models.most_common()), "modele_nominal": primary,
            "bascule": fallback, "part_bascule": fallback_share,
            "delai_median_h": median(delays) if delays else None,
        },
        "file": {
            "en_attente": queued[0] or 0,
            "plus_ancienne_h": _hours(now - _aware(queued[1])) if queued[1] else None,
            "echecs_24h": failed_24h,
            "embeddings_en_attente": embed_pending[0] or 0,
            "embedding_plus_ancien_h": _hours(now - _aware(embed_pending[1])) if embed_pending[1] else None,
            "a_classer": count_pending(db),
        },
        "retours": {"jaime": likes, "pas_interesse": dislikes,
                    "part_jaime": round(likes / (likes + dislikes), 3) if likes + dislikes else None,
                    "raisons": dict(reasons.most_common(5))},
        "passages": runs,
    }
    health["alertes"] = alerts(db, health, now)
    return health


def alerts(db: Session, h: dict, now: datetime) -> list[str]:
    out = []
    collect = _last_run(db, ("collecte",), finished=True)
    if collect is None or now - _aware(collect.started_at) > COLLECT_MAX_AGE:
        out.append("Aucune collecte terminée depuis plus de 36 h (job mire-daily ?)")
    discovery = _last_run(db, DISCOVERY_KINDS, finished=True)
    if discovery is None or now - _aware(discovery.started_at) > DISCOVERY_MAX_AGE:
        out.append("Aucune découverte d'entreprises terminée depuis plus de 8 jours (agent arrêté ?)")
    q = h["file"]
    if q["plus_ancienne_h"] is not None and q["plus_ancienne_h"] > _hours(QUEUE_MAX_WAIT):
        out.append(f"File bloquée : une tâche attend depuis {q['plus_ancienne_h']} h (job mire-worker ?)")
    if q["echecs_24h"]:
        out.append(f"{q['echecs_24h']} tâche(s) en échec définitif ces dernières 24 h")
    if q["embedding_plus_ancien_h"] is not None and q["embedding_plus_ancien_h"] > _hours(EMBED_MAX_WAIT):
        out.append(f"{q['embeddings_en_attente']} offre(s) sans embedding depuis plus de 24 h")
    ev = h["evaluations"]
    known = sum(n for m, n in ev["par_modele"].items() if m not in ("regles", "gemini", "inconnu"))
    if ev["part_bascule"] is not None and known >= FALLBACK_MIN_SAMPLE and ev["part_bascule"] > FALLBACK_MAX_SHARE:
        out.append(f"{ev['part_bascule']:.0%} des évaluations sur un modèle de secours (quota ou surcharge de "
                   f"{ev['modele_nominal']})")
    if sum(h["offres"]["ajoutees"][-NO_NEW_JOBS_DAYS:]) == 0:
        out.append(f"Aucune offre ajoutée depuis {NO_NEW_JOBS_DAYS} jours")
    return out
