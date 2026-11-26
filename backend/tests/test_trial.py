"""Essai gratuit de la landing (trial.py) : lien LinkedIn -> 3 startups, sans compte ni appel réseau."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import trial
from app.llm import get_llm
from app.main import app
from app.models import Company, Job, TrialLead, User
from app.trial import TrialPick, TrialSelection, name_from_slug, parse_linkedin

OLD = date.today() - timedelta(days=30)


class FakeLLM:
    model = "fake"

    def __init__(self, profile="Ingénieure backend senior chez Acme, Python, Paris"):
        self.profile = profile
        self.searches: list[str] = []
        self.prompts: list[str] = []

    def find_public_profile(self, url):
        self.searches.append(url)
        return self.profile

    def select_trial_companies(self, instruction, contents, model_cls):
        self.prompts.append(contents)
        ids = [int(part.split("|")[0].strip().removeprefix("- id=")) for part in contents.splitlines()
               if part.startswith("- id=")]
        # id hors liste, puis une offre recopiée (casse près) sur deux, et une offre inventée sur deux
        picks = [TrialPick(company_id=9999, job_title="x", reason="inventée")]
        picks += [TrialPick(company_id=i, job_title=f"backend engineer {n}" if n % 2 == 0 else "Titre inventé",
                            reason=f"Raison {i}") for n, i in enumerate(ids)]
        return TrialSelection(profile_summary="Backend senior, Python, Paris", picks=picks)


@pytest.fixture
def fake_llm():
    fake = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_llm, None)


def seed(db, n=7):
    companies = []
    for i in range(n):
        c = Company(name=f"Startup {i}", slug=f"s{i}", website=f"s{i}.io", sector="SaaS", funding_stage="Série A",
                    funding_amount_meur=10 + i, funding_announced_on=OLD, investors="Fonds Secret")
        db.add_all([c, Job(company=c, title=f"Backend Engineer {i}", published_on=OLD)])
        companies.append(c)
    fresh = Company(name="Toute Neuve", slug="neuve")  # offre trop récente : réservée au Pro
    closed = Company(name="Fermée", slug="fermee")
    intern = Company(name="Que Des Stages", slug="stages")
    db.add_all([fresh, Job(company=fresh, title="Founding Engineer", published_on=date.today()),
                closed, Job(company=closed, title="Ancien poste", published_on=OLD, closed_at=datetime.now(UTC)),
                intern, Job(company=intern, title="Engineering Internships 2027", published_on=OLD),
                Job(company=intern, title="Spontaneous Application", published_on=OLD)])
    db.commit()
    return companies


@pytest.mark.parametrize("url, slug, normalized", [
    ("https://www.linkedin.com/in/jane-doe-42/", "jane-doe-42", "jane-doe-42"),
    ("linkedin.com/in/Jane-Doe", "jane-doe", "jane-doe"),
    ("https://fr.linkedin.com/in/jane?utm=x", "jane", "jane"),
    ("linkedin.com/in/jérémy-lê", "jérémy-lê", "j%C3%A9r%C3%A9my-l%C3%AA"),
    ("https://www.linkedin.com/in/j%C3%A9r%C3%A9my-l%C3%AA", "jérémy-lê", "j%C3%A9r%C3%A9my-l%C3%AA"),
])
def test_parse_linkedin(url, slug, normalized):
    assert parse_linkedin(url) == (f"https://www.linkedin.com/in/{normalized}", slug)


@pytest.mark.parametrize("url", ["https://evil.com/in/jane", "https://linkedin.com/company/acme",
                                 "linkedin.com/in/", "jane"])
def test_parse_linkedin_rejects(url):
    with pytest.raises(Exception):
        parse_linkedin(url)


def test_name_hint_from_slug():
    assert name_from_slug("jérémy-lê-3a91b2c4") == "Jérémy Lê"
    assert name_from_slug("12345") is None


def test_trial_returns_three_relevant_public_companies_and_stores_lead(db, fake_llm):
    seed(db)
    client = TestClient(app)
    r = client.post("/api/public/trial", json={"linkedin_url": "linkedin.com/in/jane-doe", "wish": "Backend, Paris"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["profile_found"] is True
    assert data["profile_summary"] == "Backend senior, Python, Paris"
    # 3 au plus, et seulement des offres qui existent (les titres inventés sont jetés, jamais remplacés)
    assert [c["name"] for c in data["companies"]] == ["Startup 0", "Startup 2", "Startup 4"]
    assert [c["job_title"] for c in data["companies"]] == ["Backend Engineer 0", "Backend Engineer 2", "Backend Engineer 4"]
    first = data["companies"][0]
    assert first["website"].startswith("https://") and first["funding"]["stage"] == "Série A"
    assert "Fonds Secret" not in r.text  # investisseurs : réservés au Pro
    prompt = fake_llm.prompts[0]
    for absent in ("Toute Neuve", "Fermée", "Que Des Stages", "Internships", "Spontaneous"):
        assert absent not in prompt  # trop récente, fermée, stages et candidatures spontanées écartés
    assert "Jane Doe" in fake_llm.searches[0]  # nom deviné depuis l'adresse, pour la recherche

    lead = db.query(TrialLead).one()
    assert lead.linkedin_url == "https://www.linkedin.com/in/jane-doe" and lead.wish == "Backend, Paris"
    assert len(lead.ip_hash) == 64

    # Même lien : résultat gardé, pas de nouvel appel Gemini
    again = client.post("/api/public/trial", json={"linkedin_url": "https://www.linkedin.com/in/jane-doe/",
                                                   "wish": "Backend, Paris"})
    assert again.json() == data
    assert len(fake_llm.prompts) == 1


def test_internships_kept_when_the_person_asks_for_one(db, fake_llm):
    seed(db)
    TestClient(app).post("/api/public/trial", json={"linkedin_url": "linkedin.com/in/jane", "wish": "Stage en data"})
    assert "Engineering Internships 2027" in fake_llm.prompts[0]


def test_profile_not_found_asks_for_a_wish_instead_of_guessing(db, fake_llm):
    seed(db)
    fake_llm.profile = None
    client = TestClient(app)
    r = client.post("/api/public/trial", json={"linkedin_url": "linkedin.com/in/jane"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "need_wish"
    assert fake_llm.prompts == []  # aucune sélection au hasard
    assert db.query(TrialLead).one().results is None  # le lien est gardé quand même

    r = client.post("/api/public/trial", json={"linkedin_url": "linkedin.com/in/jane", "wish": "Backend, Paris"})
    assert r.status_code == 200 and r.json()["profile_found"] is False and r.json()["companies"]
    assert "introuvable" in fake_llm.prompts[-1]


def test_trial_errors(db, fake_llm):
    client = TestClient(app)
    assert client.post("/api/public/trial", json={"linkedin_url": "linkedin.com/in/jane"}).status_code == 503  # vide
    assert client.post("/api/public/trial", json={"linkedin_url": "https://x.com/jane"}).status_code == 422


def test_trial_limits_per_ip(db, fake_llm, monkeypatch):
    seed(db)
    monkeypatch.setattr(trial, "TRIAL_PER_IP", 2)
    client = TestClient(app)
    for i in range(2):
        assert client.post("/api/public/trial", json={"linkedin_url": f"linkedin.com/in/user-{i}"}).status_code == 200
    assert client.post("/api/public/trial", json={"linkedin_url": "linkedin.com/in/user-9"}).status_code == 429


def test_trial_without_gemini(db):
    seed(db)
    app.dependency_overrides[get_llm] = lambda: None
    try:
        r = TestClient(app).post("/api/public/trial", json={"linkedin_url": "linkedin.com/in/jane"})
    finally:
        app.dependency_overrides.pop(get_llm, None)
    assert r.status_code == 503


def test_purge_old_leads(db):
    now = datetime.now(UTC)
    db.add_all([TrialLead(linkedin_url="u1", linkedin_slug="a", ip_hash="h", created_at=now - timedelta(days=400)),
                TrialLead(linkedin_url="u2", linkedin_slug="b", ip_hash="h", created_at=now)])
    db.commit()
    assert trial.purge_old_leads(db) == 1
    assert [lead.linkedin_slug for lead in db.query(TrialLead)] == ["b"]


def test_admin_export(db, fake_llm, monkeypatch, as_user):
    client_for = as_user
    seed(db)
    TestClient(app).post("/api/public/trial", json={"linkedin_url": "linkedin.com/in/jane"})
    monkeypatch.setenv("BILLING_ENABLED", "true")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@mire.test")
    admin = User(linkedin_sub="adm", name="Admin", email="admin@mire.test")
    someone = User(linkedin_sub="u", name="U", email="u@mire.test")
    db.add_all([admin, someone])
    db.commit()
    assert client_for(someone).get("/api/admin/trial-leads").status_code == 403
    csv = client_for(admin).get("/api/admin/trial-leads?format=csv")
    assert csv.status_code == 200 and "https://www.linkedin.com/in/jane" in csv.text
