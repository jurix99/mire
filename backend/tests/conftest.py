"""Tests sur SQLite (fichier temporaire) : pas besoin de PostgreSQL ni de Docker.

Lancer : cd backend && uv run pytest
"""

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="mire-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ["SEED_DEMO"] = "false"
os.environ["GEMINI_API_KEY"] = ""  # aucun appel réseau : les tests injectent un faux client Gemini

from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402, F401
from app.db import Base, SessionLocal, engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Company, Job, User  # noqa: E402
from app.users import current_user  # noqa: E402


@pytest.fixture(autouse=True)
def _schema():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def make_user(db, sub: str, name: str) -> User:
    user = User(linkedin_sub=sub, name=name)
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def alice(db):
    return make_user(db, "alice", "Alice")


@pytest.fixture
def bob(db):
    return make_user(db, "bob", "Bob")


@pytest.fixture
def catalog(db):
    """Deux entreprises, trois postes."""
    acme = Company(name="Acme", slug="acme", website="acme.io")
    globex = Company(name="Globex", slug="globex")
    jobs = [
        Job(company=acme, title="Backend Engineer", score=8.5, stack="Python, PostgreSQL"),
        Job(company=acme, title="Frontend Engineer", score=6.0, stack="TypeScript, React"),
        Job(company=globex, title="Data Engineer", score=7.8, stack="Python, GCP"),
    ]
    db.add_all([acme, globex, *jobs])
    db.commit()
    return {"acme": acme, "globex": globex, "jobs": jobs}


def client_for(user: User) -> TestClient:
    """Client HTTP connecté en tant que `user` (la session LinkedIn est court-circuitée)."""

    def _get_db():
        with SessionLocal() as session:
            yield session

    def _current_user():
        with SessionLocal() as session:
            return session.get(User, user.id)

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[current_user] = _current_user
    return TestClient(app)


@pytest.fixture
def as_user():
    yield client_for
    app.dependency_overrides.clear()
