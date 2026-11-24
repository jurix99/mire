"""Test de restauration de la sauvegarde PostgreSQL : vérifie, EN LECTURE SEULE, une base restaurée.

Déroulé complet (workflow GitHub .github/workflows/restore-test.yml, chaque mois) : restauration à un instant
donné (PITR) de la base de production vers un serveur temporaire au nom fixe, puis job Container Apps
mire-restore-check (dans le VNet : la base n'a pas d'accès public) qui lance `python -m app.jobs restore-check`,
puis suppression du serveur temporaire.

La connexion reprend DATABASE_URL (identifiants : ceux de la base d'origine, copiés par la restauration) en
remplaçant l'hôte par RESTORE_HOST. Vérifications :
- l'hôte n'est PAS celui de la production (sinon on ne testerait rien) ;
- schéma : alembic_version = dernière révision des migrations du code déployé ; extension vector présente ;
- données : chaque table principale existe et se lit ; les utilisateurs et les offres ne sont pas vides ;
- fraîcheur : la dernière offre ajoutée date de moins de RESTORE_MAX_LAG_H heures (défaut 72) : une
  restauration qui rend une base ancienne ne vaut rien.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

TABLES = ("users", "user_profiles", "companies", "jobs", "job_assessments", "job_feedback", "applications",
          "subscriptions", "search_runs", "tasks")
NON_EMPTY = ("users", "companies", "jobs")


def restored_url(database_url: str, host: str) -> str:
    url = make_url(database_url)
    if not host or host == url.host:
        raise ValueError("RESTORE_HOST doit désigner le serveur RESTAURÉ, pas la base d'origine")
    return url.set(host=host).render_as_string(hide_password=False)


def read_only_engine(url: str) -> Engine:
    """Toute transaction est en lecture seule (PostgreSQL) : la vérification ne peut rien modifier."""
    if url.startswith("postgresql"):
        return create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"})
    return create_engine(url)


def alembic_head() -> str | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = Path(__file__).resolve().parents[1]
    return ScriptDirectory.from_config(Config(str(backend / "alembic.ini"))).get_current_head()


def check(engine: Engine, head: str | None, now: datetime | None = None,
          max_lag: timedelta = timedelta(hours=72)) -> dict:
    """Vérifications ; retourne {"ok", "problemes", "lignes", "revision", "derniere_offre"}."""
    now = now or datetime.now(UTC)
    problems: list[str] = []
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if head and revision != head:
            problems.append(f"Révision du schéma {revision} au lieu de {head}")
        if engine.dialect.name == "postgresql" and not conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar():
            problems.append("Extension vector absente")
        for table in TABLES:
            try:
                counts[table] = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() or 0  # noqa: S608
            except Exception as exc:  # table absente ou illisible
                conn.rollback()
                problems.append(f"Table {table} illisible : {type(exc).__name__}")
        problems += [f"Table {t} vide" for t in NON_EMPTY if counts.get(t) == 0]
        latest = conn.execute(text("SELECT max(created_at) FROM jobs")).scalar() if "jobs" in counts else None
    if isinstance(latest, str):  # SQLite
        latest = datetime.fromisoformat(latest)
    if latest is not None and latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    if latest is None or now - latest > max_lag:
        problems.append(f"Dernière offre trop ancienne ({latest.isoformat() if latest else 'aucune'}) : la "
                        f"restauration ne rend pas une base récente")
    return {"ok": not problems, "problemes": problems, "lignes": counts, "revision": revision,
            "derniere_offre": latest.isoformat() if latest else None}


def run() -> dict:
    url = restored_url(os.environ["DATABASE_URL"], os.environ.get("RESTORE_HOST", "").strip())
    lag = timedelta(hours=float(os.environ.get("RESTORE_MAX_LAG_H") or 72))
    engine = read_only_engine(url)
    try:
        return check(engine, alembic_head(), max_lag=lag)
    finally:
        engine.dispose()
