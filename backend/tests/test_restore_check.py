"""Test de restauration : vérification d'une base restaurée (ici une base SQLite jetable)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

from app import jobs
from app.db import Base
from app.models import Company, Job, User
from app.restore_check import alembic_head, check, restored_url

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def restored(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'restored.db').as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": alembic_head()})
    yield engine
    engine.dispose()


def _fill(engine, job_created: datetime):
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        acme = Company(name="Acme", slug="acme")
        db.add_all([User(linkedin_sub="u", name="U"), acme, Job(company=acme, title="Platform", created_at=job_created)])
        db.commit()


def test_restored_url_never_targets_the_source():
    src = "postgresql+psycopg://mireadmin:secret@psql-mire-prod-x.postgres.database.azure.com:5432/mire?sslmode=require"
    out = restored_url(src, "psql-mire-restore-x.postgres.database.azure.com")
    assert "psql-mire-restore-x" in out and "secret" in out and "sslmode=require" in out
    with pytest.raises(ValueError):
        restored_url(src, "psql-mire-prod-x.postgres.database.azure.com")
    with pytest.raises(ValueError):
        restored_url(src, "")


def test_fresh_restore_passes(restored):
    _fill(restored, NOW - timedelta(hours=10))
    result = check(restored, alembic_head(), now=NOW)
    assert result["ok"], result["problemes"]
    assert result["lignes"]["jobs"] == 1 and result["revision"] == alembic_head()


def test_empty_or_stale_restore_fails(restored):
    result = check(restored, alembic_head(), now=NOW)
    assert not result["ok"]
    assert "Table users vide" in result["problemes"] and any("trop ancienne" in p for p in result["problemes"])
    _fill(restored, NOW - timedelta(days=10))
    stale = check(restored, alembic_head(), now=NOW)
    assert stale["problemes"] == [p for p in stale["problemes"] if "trop ancienne" in p] and not stale["ok"]


def test_wrong_schema_revision_fails(restored):
    _fill(restored, NOW)
    assert "Révision du schéma" in check(restored, "autre_revision", now=NOW)["problemes"][0]


def test_command_fails_cleanly_without_restored_host(monkeypatch):
    monkeypatch.delenv("RESTORE_HOST", raising=False)
    assert jobs.main(["restore-check"]) == 1
