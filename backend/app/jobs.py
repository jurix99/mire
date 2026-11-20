"""Traitements de fond hors de l'API : `python -m app.jobs <commande>` (jobs Container Apps en production).

  daily              collecte sur les ATS, embeddings des nouvelles offres, présélection (match_user) pour
                     chaque utilisateur, puis évaluation Gemini (Pro d'abord). Ping HEALTHCHECK_DAILY_URL
                     (healthchecks.io) à la fin s'il est défini, /fail en cas d'échec.
  worker             traite la file de tâches (table tasks) jusqu'à ce qu'elle soit vide, ou --max-seconds.
  migrate            alembic upgrade head (lancé par la CI avant chaque mise à jour de l'API).

Code de retour : 0 si succès, 1 sinon (le job Container Apps est alors réessayé selon sa configuration).
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from . import logs

log = logging.getLogger("mire.jobs")

BACKEND_DIR = Path(__file__).resolve().parents[1]  # contient alembic.ini


def daily(db: Session, llm=None, fetch=None, embedder=None) -> dict:
    """Enchaînement quotidien. Lève une exception si la collecte ou les embeddings échouent ; une erreur Gemini
    pour un utilisateur est consignée et son évaluation remise en file (le worker la reprendra)."""
    from . import ats, collector, embeddings, matching, tasks
    from .llm import get_llm

    llm = llm if llm is not None else get_llm()
    run = collector.collect_all(db, fetch=fetch or ats.fetch_postings)
    log.info("%s", run.summary, extra={"step": "collect", "jobs_added": run.jobs_added, "jobs_closed": run.jobs_closed})
    embedded = embeddings.embed_pending_jobs(db, embedder)
    log.info("Embeddings : %s offres", embedded, extra={"step": "embed_jobs", "count": embedded})

    summary = {"collect": run.summary, "embedded": embedded, "users": 0, "evaluated": 0, "errors": 0}
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
    args = parser.parse_args(argv)
    if args.command == "daily":
        return cmd_daily()
    if args.command == "worker":
        return cmd_worker(args.max_seconds)
    return cmd_migrate()


if __name__ == "__main__":
    sys.exit(main())
