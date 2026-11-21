"""Traitements de fond hors de l'API : `python -m app.jobs <commande>` (jobs Container Apps en production).

  daily              collecte sur les ATS, embeddings des nouvelles offres, classement sur la liste fermée
                     (CLASSIFY_MAX_PER_RUN offres, le reste en tâche classify_jobs), présélection (match_user) pour
                     chaque utilisateur, évaluation Gemini (Pro d'abord), puis rafraîchissement des agrégats du
                     marché. Ping HEALTHCHECK_DAILY_URL (healthchecks.io) à la fin s'il est défini, /fail sinon.
  worker             traite la file de tâches (table tasks) jusqu'à ce qu'elle soit vide, ou --max-seconds.
  migrate            alembic upgrade head (lancé par la CI avant chaque mise à jour de l'API).
  taxonomy           charge la liste fermée (taxonomy.py) dans les tables de référence.
  classify [--all]   classe les offres en attente, par lots, jusqu'au bout ou au quota Gemini ; --all : reclasse
                     tout le stock (premier déploiement, changement de la liste).

Code de retour : 0 si succès, 1 sinon (le job Container Apps est alors réessayé selon sa configuration).
"""

import argparse
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from . import logs

log = logging.getLogger("mire.jobs")

BACKEND_DIR = Path(__file__).resolve().parents[1]  # contient alembic.ini


CLASSIFY_RETRY_DELAY = timedelta(minutes=30)  # classement interrompu (quota Gemini) : reprise plus tard


def daily(db: Session, llm=None, fetch=None, embedder=None) -> dict:
    """Enchaînement quotidien. Lève une exception si la collecte ou les embeddings échouent ; une erreur Gemini
    pour un utilisateur est consignée et son évaluation remise en file (le worker la reprendra)."""
    from . import account, ats, classify, collector, embeddings, matching, taxonomy, tasks
    from .llm import get_llm

    llm = llm if llm is not None else get_llm()
    # Durée de conservation (politique de confidentialité) : comptes inactifs depuis 3 ans supprimés
    purged = account.purge_inactive(db)
    if purged:
        log.info("Comptes inactifs supprimés : %s", purged, extra={"step": "purge", "count": purged})
    run = collector.collect_all(db, fetch=fetch or ats.fetch_postings)
    log.info("%s", run.summary, extra={"step": "collect", "jobs_added": run.jobs_added, "jobs_closed": run.jobs_closed})
    embedded = embeddings.embed_pending_jobs(db, embedder)
    log.info("Embeddings : %s offres", embedded, extra={"step": "embed_jobs", "count": embedded})
    # Classement sur la liste fermée : plafonné par passage, le reste en tâche de fond (file)
    taxonomy.sync(db)
    db.commit()
    classified = classify.classify_pending_jobs(db, llm)
    if classified["remaining"]:
        tasks.enqueue(db, "classify_jobs", dedup_key="classify_jobs",
                      delay=CLASSIFY_RETRY_DELAY if classified["stopped"] else None)

    summary = {"collect": run.summary, "embedded": embedded, "classified": classified["classified"], "users": 0,
               "evaluated": 0, "errors": 0}
    for user, _scope in matching.users_by_priority(db):  # Pro d'abord
        summary["users"] += 1
        candidates = matching.match_user(db, user)
        if llm is None or not candidates["job_ids"]:
            continue
        try:
            stats = matching.assess_candidates(db, user, llm, candidates)
            summary["evaluated"] += stats["evaluated"]
            log.info("Évaluation utilisateur %s : %s", user.id, stats, extra={"step": "assess", "user_id": user.id})
        except Exception as exc:  # quota Gemini, réseau… : repris par le worker
            db.rollback()
            summary["errors"] += 1
            log.warning("Évaluation utilisateur %s en échec : %s", user.id, exc, extra={"user_id": user.id})
            tasks.enqueue(db, "assess_user", {"user_id": user.id, **candidates})
    # Agrégats du marché (page Analytics, baromètre) : vue matérialisée rafraîchie une fois par jour
    from . import market

    market.refresh_facts(db)
    log.info("Daily terminé", extra={"step": "daily", **{k: v for k, v in summary.items() if k != "collect"}})
    return summary


def ping_healthcheck(ok: bool) -> None:
    """Ping healthchecks.io (URL secrète : jamais journalisée). Valeur vide ou autre qu'une URL (ex. « off ») :
    pas de ping."""
    url = os.environ.get("HEALTHCHECK_DAILY_URL", "").strip().rstrip("/")
    if not url.startswith(("https://", "http://")):
        return
    import httpx

    from .net import TLS_CONTEXT

    try:
        httpx.get(url if ok else f"{url}/fail", timeout=10, verify=TLS_CONTEXT)
    except httpx.HTTPError as exc:
        log.warning("Ping healthcheck impossible : %s", type(exc).__name__)


def cmd_daily() -> int:
    from .db import SessionLocal

    ok = False
    try:
        with SessionLocal() as db:
            daily(db)
        ok = True
    except Exception:
        log.exception("Daily en échec")
    ping_healthcheck(ok)
    return 0 if ok else 1


def cmd_worker(max_seconds: float | None) -> int:
    from . import tasks

    stats = tasks.run_worker(until_empty=True, max_seconds=max_seconds)
    log.info("Worker terminé : %s", stats, extra=stats)
    return 0


def cmd_taxonomy() -> int:
    from . import taxonomy
    from .db import SessionLocal

    with SessionLocal() as db:
        counts = taxonomy.sync(db)
        db.commit()
    log.info("Liste fermée chargée (version %s) : %s", taxonomy.TAXONOMY_VERSION, counts, extra=counts)
    return 0


def cmd_classify(reclassify_all: bool) -> int:
    """Classe tout ce qui est en attente, lot par lot ; s'arrête proprement sur quota (reprise au prochain
    passage : les offres restantes sont toujours « en attente »)."""
    from . import classify, taxonomy
    from .db import SessionLocal
    from .llm import get_llm

    llm = get_llm()
    total = {"classified": 0, "llm_calls": 0, "autre": 0}
    with SessionLocal() as db:
        taxonomy.sync(db)
        db.commit()
        if reclassify_all:
            log.info("Reclassement complet : %s offres remises en attente", classify.reset_all(db))
        while True:
            stats = classify.classify_pending_jobs(db, llm)
            for k in total:
                total[k] += stats[k]
            if stats["stopped"] or not stats["remaining"] or not stats["classified"]:
                break
        from . import market

        market.refresh_facts(db)  # les analytics voient tout de suite le nouveau classement
    log.info("Classement terminé : %s", total, extra={**total, "remaining": stats["remaining"],
                                                      "stopped": stats["stopped"]})
    return 0


def cmd_migrate() -> int:
    from alembic.config import main as alembic_main

    os.chdir(BACKEND_DIR)
    alembic_main(argv=["-c", str(BACKEND_DIR / "alembic.ini"), "upgrade", "head"])
    log.info("Migrations appliquées")
    return 0


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    parser = argparse.ArgumentParser(prog="python -m app.jobs", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("daily", help="collecte, embeddings, présélection et évaluation")
    worker = sub.add_parser("worker", help="traite la file de tâches jusqu'à ce qu'elle soit vide")
    worker.add_argument("--max-seconds", type=float, default=None, help="budget de temps (défaut : illimité)")
    sub.add_parser("migrate", help="alembic upgrade head")
    sub.add_parser("taxonomy", help="charge la liste fermée dans les tables de référence")
    cls = sub.add_parser("classify", help="classe les offres en attente sur la liste fermée")
    cls.add_argument("--all", action="store_true", help="reclasse tout le stock")
    args = parser.parse_args(argv)
    if args.command == "daily":
        return cmd_daily()
    if args.command == "worker":
        return cmd_worker(args.max_seconds)
    if args.command == "taxonomy":
        return cmd_taxonomy()
    if args.command == "classify":
        return cmd_classify(args.all)
    return cmd_migrate()


if __name__ == "__main__":
    sys.exit(main())
