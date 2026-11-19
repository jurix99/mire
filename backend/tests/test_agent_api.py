"""API de l'agent : jeton, ingestion, dédoublonnage, garde-fous, passages (Gemini simulé)."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Company, Job, Source

TOKEN = "jeton-de-test"


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN", TOKEN)
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    return client


def test_token_required(monkeypatch):
    client = TestClient(app)
    monkeypatch.delenv("AGENT_TOKEN", raising=False)
    assert client.get("/api/agent/context").status_code == 503  # API désactivée sans jeton configuré
    monkeypatch.setenv("AGENT_TOKEN", TOKEN)
    assert client.get("/api/agent/context").status_code == 401
    assert client.get("/api/agent/context", headers={"Authorization": "Bearer faux"}).status_code == 401
    # La session LinkedIn ne donne pas accès à l'API de l'agent
    assert client.get("/api/agent/context", headers={"X-Dev-User": "1"}).status_code == 401


def test_context_is_anonymised(db, alice, catalog, as_user, agent):
    as_user(alice).patch("/api/profile", json={"skills": [{"name": "Python"}],
                                               "rules": [{"kind": "eviter", "text": "SQL au cœur"}]})
    ctx = agent.get("/api/agent/context").json()
    assert {j["titre"] for j in ctx["offres_connues"]} == {"Backend Engineer", "Frontend Engineer", "Data Engineer"}
    [profile] = ctx["profils"]
    assert profile["profil"].startswith(f"Nom : Utilisateur {alice.id}")  # ni nom ni e-mail
    assert "Alice" not in str(ctx)
    assert "Éviter : SQL au cœur" in profile["profil"]


def test_full_run(db, alice, catalog, as_user, agent, fake_llm):
    as_user(alice).patch("/api/profile", json={"skills": [{"name": "Python"}]})
    run_id = agent.post("/api/agent/runs", json={"kind": "approfondi"}).json()["run_id"]

    # Entreprise : création puis complément des seuls champs vides
    c = agent.post("/api/agent/companies", json={"name": "Remanence", "website": "remanence.ai",
                                                 "funding_stage": "Seed", "ats": "ashby", "ats_slug": "remanence"}).json()
    assert c["created"] is True
    as_user(alice).patch(f"/api/companies/{c['id']}", json={"funding_stage": "Pré-seed (saisi par Alice)"})
    again = agent.post("/api/agent/companies", json={"name": "remanence", "funding_stage": "Série A",
                                                     "headcount": "11-50"}).json()
    assert again == {"id": c["id"], "name": "Remanence", "created": False, "completed": ["headcount"]}
    assert db.get(Company, c["id"]).funding_stage == "Pré-seed (saisi par Alice)"

    # Source ajoutée par l'agent
    s = agent.post("/api/agent/sources", json={"name": "Les Pépites Tech", "kind": "funding_radar",
                                               "run_id": run_id}).json()
    agent.patch(f"/api/agent/sources/{s['id']}", json={"query": "https://lespepitestech.com", "notes_append": "RAS"})
    source = db.get(Source, s["id"])
    db.refresh(source)
    assert (source.added_by, source.active, source.query) == ("agent", True, "https://lespepitestech.com")

    # Offres : ajout, doublon refusé, trop ancienne refusée
    job = {"company_id": c["id"], "title": "Member of Technical Staff, Platform",
           "url": "https://jobs.ashbyhq.com/remanence/1?utm_source=x", "open_verified": True,
           "source_id": s["id"], "run_id": run_id, "work_mode": "hybride", "salary": "70-90 k€"}
    r = agent.post("/api/agent/jobs", json=job)
    assert r.status_code == 201, r.text
    dup = agent.post("/api/agent/jobs", json={**job, "url": "https://jobs.ashbyhq.com/remanence/1"})
    assert dup.status_code == 409 and dup.json()["detail"]["existing_id"] == r.json()["id"]
    old = agent.post("/api/agent/jobs", json={**job, "title": "Ancienne", "url": None,
                                              "published_on": (date.today() - timedelta(days=90)).isoformat()})
    assert old.status_code == 422
    created = db.get(Job, r.json()["id"])
    assert created.verified_open_at is not None and created.work_mode == "hybride"

    # Fin de passage : évaluation immédiate et résumé par utilisateur
    fake_llm.grade = lambda j: {"competences": 1, "stade": 1, "ownership": 1, "ia_concrete": 1, "xp": 1, "references": 1}
    done = agent.post(f"/api/agent/runs/{run_id}/finish", json={"jobs_seen": 12, "summary": "Passage approfondi"}).json()
    [mine] = [u for u in done["par_utilisateur"] if u["user_id"] == alice.id]
    assert mine["nouvelles"] == 1 and mine["top"][0]["titre"] == "Member of Technical Staff, Platform"
    assert "12 offres vues, 1 ajoutées, 1 déjà connues" in done["summary"]
    assert agent.post(f"/api/agent/runs/{run_id}/finish", json={}).status_code == 409


def test_agent_cannot_deactivate_or_delete(db, agent):
    s = agent.post("/api/agent/sources", json={"name": "Breega", "kind": "vc_board"}).json()
    agent.patch(f"/api/agent/sources/{s['id']}", json={"active": False})  # champ ignoré
    assert db.scalar(select(Source)).active is True
    from app.agent_api import router

    routes = {(r.path, m) for r in router.routes for m in r.methods}
    assert ("/api/agent/jobs", "POST") in routes
    assert not any(m == "DELETE" for _, m in routes)


def test_close_job(db, catalog, agent):
    job = catalog["jobs"][0]
    assert agent.post(f"/api/agent/jobs/{job.id}/close").json()["closed_at"]
    db.refresh(job)
    assert job.closed_at is not None
