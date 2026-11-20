"""Présélection par similarité (embeddings.py, matching.py), sur SQLite avec un faux modèle d'embeddings.

L'opérateur <=> de pgvector et l'index HNSW sont testés sous PostgreSQL : voir test_postgres.py.
"""

import hashlib
import math
import re

import pytest
from sqlalchemy import select

from app import embeddings, jobs, matching
from app.models import EMBEDDING_DIM, Company, Job, JobAssessment, UserProfile
from tests.test_collector import boards

PROFILE = {"years_experience": 5, "skills": [{"name": "Python"}, {"name": "PostgreSQL"}],
           "positioning": "Ingénieur backend Python PostgreSQL"}


class FakeEmbedder:
    """Sac de mots haché sur EMBEDDING_DIM dimensions, normalisé : deux textes qui partagent des mots sont proches."""

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * EMBEDDING_DIM
            for word in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % EMBEDDING_DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


@pytest.fixture
def embedder(monkeypatch):
    fake = FakeEmbedder()
    monkeypatch.setattr(embeddings, "_embedder", fake)
    return fake


def add(db, title, **fields):
    company = db.scalar(select(Company).where(Company.slug == "acme")) or Company(name="Acme", slug="acme")
    job = Job(company=company, title=title, **fields)
    db.add(job)
    db.commit()
    return job


def test_job_embedding_reset_only_when_text_fields_change(db, embedder):
    job = add(db, "Backend Engineer", stack="Python")
    assert embeddings.embed_pending_jobs(db) == 1
    assert job.embedded_at is not None
    assert embeddings.embed_pending_jobs(db) == 0  # rien de nouveau : aucun recalcul
    job.verified_open_at = job.embedded_at  # champ hors du texte de l'offre : embedding conservé
    job.stack = "Python"  # même valeur : conservé
    db.commit()
    assert job.embedded_at is not None
    job.stack = "Python, Kubernetes"
    db.commit()
    assert job.embedded_at is None and embeddings.embed_pending_jobs(db) == 1


def test_match_user_keeps_top_k_after_hard_exclusions(db, alice, as_user, embedder, fake_llm):
    as_user(alice).patch("/api/profile", json=PROFILE)
    backend = add(db, "Backend Engineer Python", stack="Python, PostgreSQL")
    data = add(db, "Data Engineer", stack="Python, PostgreSQL, dbt")
    front = add(db, "Frontend Engineer", stack="TypeScript, React")
    intern = add(db, "Stage backend Python", stack="Python, PostgreSQL")
    embeddings.embed_pending_jobs(db)

    candidates = matching.match_user(db, alice, top_k=2)
    assert candidates["job_ids"] == [backend.id, data.id]
    sims = candidates["similarity"]
    assert sims[str(backend.id)] > sims[str(data.id)] > 0
    # Exclusion dure : écartée sans LLM, avant la similarité
    stage = db.scalar(select(JobAssessment).where(JobAssessment.job_id == intern.id))
    assert (stage.model, stage.verdict) == ("regles", "ecartee")

    stats = matching.assess_candidates(db, alice, fake_llm, candidates)
    assert stats["evaluated"] == 2
    assert [j["job_id"] for j in fake_llm.eval_calls[0]["jobs"]] == [backend.id, data.id]
    rows = {a.job_id: a for a in db.scalars(select(JobAssessment).where(JobAssessment.user_id == alice.id))}
    assert rows[backend.id].similarity == pytest.approx(sims[str(backend.id)])
    assert front.id not in rows  # non présélectionnée : visible dans la liste, mais pas évaluée
    assert as_user(alice).get(f"/api/jobs/{front.id}").status_code == 200


def test_profile_embedding_follows_version(db, alice, as_user, embedder):
    as_user(alice).patch("/api/profile", json=PROFILE)
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == alice.id))
    first = embeddings.ensure_profile_embedding(db, profile, matching.profile_data(profile))
    calls = embedder.calls
    assert embeddings.ensure_profile_embedding(db, profile, matching.profile_data(profile)) == pytest.approx(first)
    assert embedder.calls == calls  # même version : pas de recalcul
    as_user(alice).patch("/api/profile", json={"positioning": "Ingénieur plateforme Kubernetes"})
    db.expire_all()
    profile = db.get(UserProfile, profile.id)
    assert profile.embedding_version != profile.version
    second = embeddings.ensure_profile_embedding(db, profile, matching.profile_data(profile))
    assert embedder.calls == calls + 1 and second != pytest.approx(first)


def test_profile_text_includes_liked_jobs(db, alice, as_user, embedder):
    as_user(alice).patch("/api/profile", json=PROFILE)
    liked = add(db, "Platform Engineer", stack="Terraform, Azure")
    as_user(alice).post(f"/api/jobs/{liked.id}/feedback", json={"sentiment": "like"})
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == alice.id))
    text = embeddings.profile_text(db, profile, matching.profile_data(profile))
    assert "Platform Engineer" in text and "Terraform" in text and "Python" in text


def test_similar_jobs_endpoint(db, alice, as_user, embedder):
    a = add(db, "Backend Engineer Python", stack="Python, PostgreSQL")
    b = add(db, "Senior Backend Engineer Python", stack="Python, PostgreSQL, Kafka")
    c = add(db, "Frontend Engineer", stack="TypeScript, React")
    closed = add(db, "Backend Engineer Python (fermée)", stack="Python, PostgreSQL")
    client = as_user(alice)
    assert client.get(f"/api/jobs/{a.id}/similar").json() == []  # pas encore d'embedding
    embeddings.embed_pending_jobs(db)
    closed.closed_at = closed.embedded_at
    db.commit()
    ids = [j["id"] for j in client.get(f"/api/jobs/{a.id}/similar").json()]
    assert ids[0] == b.id and set(ids) == {b.id, c.id}
    assert client.get("/api/jobs/999999/similar").status_code == 404


def test_without_model_preselection_falls_back_to_recent_jobs(db, alice, as_user, monkeypatch):
    monkeypatch.setattr(embeddings, "get_embedder", lambda: None)
    as_user(alice).patch("/api/profile", json=PROFILE)
    old, new = add(db, "Backend Engineer"), add(db, "Platform Engineer")
    assert matching.match_user(db, alice, top_k=1) == {"job_ids": [new.id], "similarity": {}}
    assert embeddings.embed_pending_jobs(db) == 0 and old.embedded_at is None


def test_daily_embeds_and_preselects(db, alice, as_user, embedder, fake_llm, monkeypatch):
    monkeypatch.setattr(matching, "MATCH_TOP_K", 1)
    as_user(alice).patch("/api/profile", json=PROFILE)
    backend = add(db, "Backend Engineer Python", stack="Python, PostgreSQL")
    add(db, "Office Manager")  # écartée par une exclusion dure (métier hors cible)
    add(db, "Product Engineer", stack="Go")
    summary = jobs.daily(db, llm=fake_llm, fetch=boards({}))
    assert summary["embedded"] == 3 and summary["evaluated"] == 1
    [call] = fake_llm.eval_calls
    assert [j["job_id"] for j in call["jobs"]] == [backend.id]
