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
from app.llm import ChatResult, ToolCallRecord, get_llm  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Company, Job, User  # noqa: E402
from app.profile import ProfileData  # noqa: E402
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


class FakeLLM:
    """Faux client Gemini : réponses scriptées, aucun appel réseau.

    - extract_profile renvoie `self.profile` ;
    - chat joue `self.script` : liste de tours, chaque tour = liste d'appels d'outils (name, args)
      suivie du texte final. Les appels sont exécutés via le vrai callback `execute`.
    """

    model = "fake-gemini"

    def __init__(self):
        self.profile = ProfileData()
        self.script: list[tuple[list[tuple[str, dict]], str]] = []
        self.calls: list[dict] = []  # ce qui a été envoyé au modèle (system, history, tools)

    def extract_profile(self, text: str) -> ProfileData:
        self.last_text = text
        return self.profile

    def analyze_profile(self, profile_json: str):
        from app.analysis import ProfileAnalysis

        self.last_analysis_input = profile_json
        return getattr(self, "analysis", None) or ProfileAnalysis()

    def chat(self, system, history, tools, execute):
        self.calls.append({"system": system, "history": history, "tools": [t.name for t in tools]})
        tool_calls, text = self.script.pop(0) if self.script else ([], "D'accord.")
        records = [ToolCallRecord(name, args, execute(name, args)) for name, args in tool_calls]
        return ChatResult(text=text, tool_calls=records)


@pytest.fixture
def fake_llm():
    llm = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: llm
    yield llm
    app.dependency_overrides.pop(get_llm, None)
