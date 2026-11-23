"""Tests qui exigent PostgreSQL + pgvector : migrations Alembic, SKIP LOCKED, opérateur <=> et index HNSW.

Ignorés sans TEST_POSTGRES_URL. En CI : service pgvector/pgvector:pg17 (voir .github/workflows/ci.yml).
En local (base JETABLE : le schéma public est recréé) :
  docker run --rm -d -p 55432:5432 -e POSTGRES_PASSWORD=test pgvector/pgvector:pg17
  TEST_POSTGRES_URL=postgresql+psycopg://postgres:test@localhost:55432/postgres uv run pytest -m postgres
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app import embeddings, tasks
from app.models import EMBEDDING_DIM, Company, Job, Task

PG_URL = os.environ.get("TEST_POSTGRES_URL", "")
pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not PG_URL, reason="TEST_POSTGRES_URL non défini")]

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pg():
    engine = create_engine(PG_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    # Le vrai chemin de production : alembic upgrade head (via python -m app.jobs migrate)
    env = {**os.environ, "DATABASE_URL": PG_URL, "SESSION_SECRET": "x"}
    subprocess.run([sys.executable, "-m", "app.jobs", "migrate"], cwd=BACKEND, env=env, check=True)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def pgdb(pg):
    with pg() as s:
        s.execute(text("TRUNCATE tasks, jobs, companies, taxonomy_misses RESTART IDENTITY CASCADE"))
        s.commit()
        yield s


def test_migrations_create_vector_schema(pg, pgdb):
    insp = inspect(pgdb.get_bind())
    assert {"embedding", "embedded_at"} <= {c["name"] for c in insp.get_columns("jobs")}
    assert "similarity" in {c["name"] for c in insp.get_columns("job_assessments")}
    indexes = {i["name"] for i in insp.get_indexes("jobs")} | {i["name"] for i in insp.get_indexes("tasks")}
    assert {"ix_jobs_embedding_hnsw", "ix_tasks_queued_run_after", "uq_tasks_queued_dedup_key"} <= indexes
    assert pgdb.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))


def test_skip_locked_never_gives_the_same_task_twice(pg, pgdb):
    first = tasks.enqueue(pgdb, "a")
    second = tasks.enqueue(pgdb, "b")
    with pg() as holder:  # un worker a verrouillé la première tâche (transaction en cours)
        locked = holder.scalars(select(Task).order_by(Task.id).limit(1).with_for_update(skip_locked=True)).first()
        assert locked.id == first.id
        with pg() as other:
            assert tasks.claim(other).id == second.id
            assert tasks.claim(other) is None
        holder.rollback()


def test_parallel_workers_run_each_task_once(pg, pgdb):
    runs: list[int] = []
    lock = threading.Lock()

    def slow(_db, payload):
        time.sleep(0.05)
        with lock:
            runs.append(payload["n"])

    from app import task_handlers  # noqa: F401  (handlers enregistrés AVANT la sauvegarde, sinon on restaure un registre vide)

    saved = dict(tasks.HANDLERS)
    try:
        tasks.handler("slow")(slow)
        for n in range(20):
            tasks.enqueue(pgdb, "slow", {"n": n})
        workers = [threading.Thread(target=tasks.run_worker, kwargs={"session_factory": pg}) for _ in range(3)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
    finally:
        tasks.HANDLERS.clear()
        tasks.HANDLERS.update(saved)
    assert sorted(runs) == list(range(20))


def test_keda_query_counts_due_tasks(pg, pgdb):
    from datetime import timedelta

    tasks.enqueue(pgdb, "a")
    tasks.enqueue(pgdb, "b", delay=timedelta(minutes=5))
    # Requête de la règle KEDA du job mire-worker (infra/main/containerapps.tf)
    assert pgdb.scalar(text("SELECT count(*) FROM tasks WHERE status = 'queued' AND run_after <= now()")) == 1


def unit(i: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[i] = 1.0
    return v


def test_cosine_ordering_with_pgvector(pgdb):
    company = Company(name="Acme", slug="acme")
    a, b, c = (Job(company=company, title=t) for t in ("A", "B", "C"))
    pgdb.add_all([a, b, c])
    pgdb.flush()
    a.embedding = unit(0)
    b.embedding = [0.9 if i == 0 else (0.1 if i == 1 else 0.0) for i in range(EMBEDDING_DIM)]
    c.embedding = unit(1)
    pgdb.commit()
    ranked = embeddings.nearest_jobs(pgdb, unit(0), limit=2)
    assert [job_id for job_id, _ in ranked] == [a.id, b.id]
    assert ranked[0][1] == pytest.approx(1.0)
    assert [job_id for job_id, _ in embeddings.similar_jobs(pgdb, a.id)] == [b.id, c.id]
    plan = "\n".join(pgdb.scalars(text(
        "EXPLAIN SELECT id FROM jobs ORDER BY embedding <=> CAST(:v AS vector) LIMIT 5"), {"v": str(unit(0))}))
    assert "jobs" in plan  # requête valide (l'index HNSW n'est choisi que sur une table assez grande)


def test_taxonomy_loaded_and_market_view_refreshes(pg, pgdb):
    """Migrations : liste fermée chargée, vue matérialisée créée ; REFRESH CONCURRENTLY après classement."""
    from app import classify, market
    from app import taxonomy as tx

    assert pgdb.scalar(text("SELECT count(*) FROM skills")) == len(tx.SKILLS)
    assert pgdb.scalar(text("SELECT count(*) FROM role_families")) == len(tx.ROLE_FAMILIES) + 1
    company = Company(name="Acme", slug="acme")
    pgdb.add_all([Job(company=company, title="Account Executive", summary="HubSpot, closing. OTE 80-100 k€"),
                  Job(company=company, title="SRE", summary="k8s, Terraform")])
    pgdb.commit()
    classify.classify_pending_jobs(pgdb)
    market.refresh_facts(pgdb)
    rows = pgdb.execute(text("SELECT role_family, domain, salary_kind FROM mv_market_jobs ORDER BY job_id")).all()
    assert [tuple(r) for r in rows] == [("sales", "gtm_ventes", "ote"), ("platform_devops_sre", "tech", "inconnu")]
    facts = market.load_facts(pgdb)
    assert {s for skills in facts.skills.values() for s in skills} >= {"hubspot", "kubernetes", "terraform"}
