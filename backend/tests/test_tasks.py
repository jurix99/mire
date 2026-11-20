"""File de tâches (tasks.py) et traitements de fond (jobs.py), sur SQLite.

SKIP LOCKED (plusieurs workers en parallèle) n'existe pas sous SQLite : voir test_postgres.py.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import jobs, tasks
from app.db import SessionLocal
from app.models import Company, Job, JobAssessment, SearchRun, Task
from tests.test_collector import boards, posting


@pytest.fixture
def handlers():
    """Registre isolé : les handlers de test ne polluent pas le registre réel."""
    from app import task_handlers  # noqa: F401

    saved = dict(tasks.HANDLERS)
    yield tasks.HANDLERS
    tasks.HANDLERS.clear()
    tasks.HANDLERS.update(saved)


def count(db, **where) -> int:
    q = select(func.count(Task.id))
    for k, v in where.items():
        q = q.where(getattr(Task, k) == v)
    return db.scalar(q)


def test_enqueue_dedup_only_while_queued(db):
    first = tasks.enqueue(db, "match_user", {"user_id": 1}, dedup_key="match_user:1")
    assert first is not None
    assert tasks.enqueue(db, "match_user", {"user_id": 1}, dedup_key="match_user:1") is None
    assert tasks.enqueue(db, "match_user", {"user_id": 2}, dedup_key="match_user:2") is not None
    assert count(db, kind="match_user") == 2
    # Une fois prise (running), une nouvelle demande est de nouveau acceptée : le profil a pu changer entre-temps
    claimed = tasks.claim(db)
    assert claimed.id == first.id and claimed.status == "running" and claimed.attempts == 1
    assert tasks.enqueue(db, "match_user", {"user_id": 1}, dedup_key="match_user:1") is not None
    # Sans clé : jamais dédoublonnée
    tasks.enqueue(db, "assess_user", {"user_id": 1})
    tasks.enqueue(db, "assess_user", {"user_id": 1})
    assert count(db, kind="assess_user") == 2


def test_claim_respects_run_after(db):
    tasks.enqueue(db, "x", delay=timedelta(minutes=5))
    assert tasks.claim(db) is None
    due = tasks.enqueue(db, "y")
    assert tasks.claim(db).id == due.id


def test_worker_runs_handlers_and_retries_with_backoff(db, handlers):
    seen = []
    tasks.handler("ok")(lambda _db, payload: seen.append(payload["n"]))

    def boom(_db, _payload):
        raise RuntimeError("Gemini indisponible")

    tasks.handler("boom")(boom)
    tasks.enqueue(db, "ok", {"n": 1})
    failing = tasks.enqueue(db, "boom")
    stats = tasks.run_worker(until_empty=True)
    assert seen == [1] and stats == {"done": 1, "failed": 1}

    db.expire_all()
    t = db.get(Task, failing.id)
    assert (t.status, t.attempts) == ("queued", 1) and "Gemini indisponible" in t.error
    run_after = t.run_after if t.run_after.tzinfo else t.run_after.replace(tzinfo=UTC)
    assert timedelta(seconds=50) < run_after - datetime.now(UTC) <= timedelta(minutes=1)
    assert tasks.retry_delay(1) == timedelta(minutes=1) and tasks.retry_delay(2) == timedelta(minutes=4)

    # Deux nouvelles tentatives (délais écoulés) : la troisième échoue définitivement
    for _ in range(2):
        t.run_after = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        tasks.run_worker(until_empty=True)
        db.expire_all()
        t = db.get(Task, failing.id)
    assert (t.status, t.attempts) == ("failed", 3)


def test_unknown_kind_fails_immediately(db, handlers):
    t = tasks.enqueue(db, "inconnu")
    tasks.run_worker()
    db.expire_all()
    assert db.get(Task, t.id).status == "failed"


def test_stale_running_task_is_requeued(db, handlers):
    tasks.handler("ok")(lambda _db, _p: None)
    t = tasks.enqueue(db, "ok")
    tasks.claim(db)
    t = db.get(Task, t.id)
    t.locked_at = datetime.now(UTC) - timedelta(minutes=31)
    db.commit()
    assert tasks.requeue_stale(db) == 1
    db.expire_all()
    assert db.get(Task, t.id).status == "queued"
    # Bloquée après la dernière tentative : abandonnée
    t = db.get(Task, t.id)
    t.status, t.attempts, t.locked_at = "running", 3, datetime.now(UTC) - timedelta(hours=1)
    db.commit()
    tasks.requeue_stale(db)
    db.expire_all()
    assert db.get(Task, t.id).status == "failed"


def test_max_seconds_stops_taking_tasks(db, handlers):
    import time

    tasks.handler("slow")(lambda _db, _p: time.sleep(0.1))
    for _ in range(3):
        tasks.enqueue(db, "slow")
    assert tasks.run_worker(max_seconds=0.05) == {"done": 1, "failed": 0}  # budget dépassé : on s'arrête
    assert count(db, status="queued") == 2


def test_profile_change_enqueues_match_user(alice, as_user, db):
    as_user(alice).patch("/api/profile", json={"positioning": "Backend Python"})
    as_user(alice).patch("/api/profile", json={"positioning": "Backend Python, plateforme"})
    [t] = db.scalars(select(Task)).all()
    assert (t.kind, t.payload, t.dedup_key) == ("match_user", {"user_id": alice.id}, f"match_user:{alice.id}")


def test_collect_run_enqueues_task(db, as_user, alice, monkeypatch):
    monkeypatch.setenv("COLLECTOR", "true")
    client = as_user(alice)
    assert client.post("/api/collect/run").status_code == 202
    assert client.get("/api/collect/status").json()["running"] is True
    assert client.post("/api/collect/run").status_code == 409
    assert count(db, kind="collect", status="queued") == 1


PROFILE = {"years_experience": 5, "skills": [{"name": "Python"}], "positioning": "Ingénieur backend Python"}


def test_daily_collects_and_assesses_pro_first(db, monkeypatch, fake_llm, as_user, alice, bob):
    acme = Company(name="Acme", slug="acme", ats="ashby", ats_slug="acme")
    db.add(acme)
    db.commit()
    for user in (alice, bob):
        as_user(user).patch("/api/profile", json=PROFILE)
    fetch = boards({("ashby", "acme"): [posting("1", "Backend Engineer", desc="Python chez Acme"),
                                        posting("2", "Stage développeur", desc="Stage chez Acme")]})
    summary = jobs.daily(db, llm=fake_llm, fetch=fetch)
    assert summary["users"] == 2 and summary["errors"] == 0
    assert db.scalar(select(func.count(Job.id))) == 2
    rows = db.scalars(select(JobAssessment).where(JobAssessment.user_id == alice.id)).all()
    by_model = {a.model for a in rows}
    assert len(rows) == 2 and "regles" in by_model  # le stage est écarté sans LLM
    assert db.scalar(select(SearchRun).where(SearchRun.kind == "collecte")).status == "termine"


def test_daily_requeues_assessment_when_gemini_fails(db, fake_llm, as_user, alice):
    db.add(Job(company=Company(name="Acme", slug="acme"), title="Backend Engineer"))
    db.commit()
    as_user(alice).patch("/api/profile", json=PROFILE)

    def unavailable(*_a, **_k):
        raise RuntimeError("503")

    fake_llm.evaluate_jobs = unavailable
    summary = jobs.daily(db, llm=fake_llm, fetch=boards({}))
    assert summary["errors"] == 1
    [t] = db.scalars(select(Task).where(Task.kind == "assess_user")).all()
    assert t.payload["user_id"] == alice.id and t.payload["job_ids"]


def test_healthcheck_ping(monkeypatch):
    calls = []
    import httpx

    monkeypatch.setattr(httpx, "get", lambda url, **_k: calls.append(url))
    monkeypatch.setenv("HEALTHCHECK_DAILY_URL", "https://hc-ping.com/abc/")
    jobs.ping_healthcheck(True)
    jobs.ping_healthcheck(False)
    assert calls == ["https://hc-ping.com/abc", "https://hc-ping.com/abc/fail"]
    monkeypatch.delenv("HEALTHCHECK_DAILY_URL")
    jobs.ping_healthcheck(False)
    assert len(calls) == 2


def test_cmd_daily_pings_fail_on_error(monkeypatch):
    pings = []
    monkeypatch.setattr(jobs, "daily", lambda db: (_ for _ in ()).throw(RuntimeError("ATS")))
    monkeypatch.setattr(jobs, "ping_healthcheck", pings.append)
    assert jobs.cmd_daily() == 1 and pings == [False]


def test_worker_handlers_chain_without_local_threads(db, monkeypatch, fake_llm, as_user, alice):
    """Production : embed_jobs -> match_user -> assess_user, chacun dans sa tâche."""
    monkeypatch.setattr("app.task_handlers.get_llm", lambda: fake_llm)
    as_user(alice).patch("/api/profile", json=PROFILE)
    db.add(Job(company=Company(name="Acme", slug="acme"), title="Backend Engineer"))
    db.commit()
    tasks.jobs_changed(db)
    stats = tasks.run_worker(session_factory=SessionLocal)
    assert stats["failed"] == 0
    kinds = [t.kind for t in db.scalars(select(Task).order_by(Task.id))]
    # Profil modifié (match_user), offre ajoutée (embed_jobs) ; le 2e match_user ne trouve plus rien à évaluer
    assert kinds == ["match_user", "embed_jobs", "assess_user", "match_user"]
    assert db.scalar(select(func.count(JobAssessment.id)).where(JobAssessment.user_id == alice.id)) == 1
    assert all(t.status == "done" for t in db.scalars(select(Task)))


def test_cli_parses_commands(monkeypatch):
    called = []
    monkeypatch.setattr(jobs, "cmd_worker", lambda s: called.append(("worker", s)) or 0)
    monkeypatch.setattr(jobs, "cmd_migrate", lambda: called.append(("migrate",)) or 0)
    assert jobs.main(["worker", "--max-seconds", "900"]) == 0
    assert jobs.main(["migrate"]) == 0
    assert called == [("worker", 900.0), ("migrate",)]


def test_healthz_and_readyz(alice, as_user):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ok"}


def test_dev_bypass_refused_with_https():
    from app.main import check_production_settings

    with pytest.raises(RuntimeError):
        check_production_settings("https://mire.example", True)
    check_production_settings("https://mire.example", False)
    check_production_settings("http://localhost:8080", True)

