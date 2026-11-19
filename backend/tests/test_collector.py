"""Trois processus : découverte (ATS proposé par l'agent, testé par Mire), collecte quotidienne (sans LLM),
pertinence par utilisateur (pré-filtre gratuit avant Gemini). Aucun appel réseau."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import ats, collector
from app.assessments import assess_user, hard_exclusion
from app.models import Company, Job, SearchRun
from app.profile import ProfileData

# --- Lecture des ATS : descriptions, mode, salaire ---------------------------------------------


def test_parse_rich_fields():
    [a] = ats.parse_postings("ashby", {"jobs": [{
        "id": "1", "title": "Platform Engineer", "jobUrl": "u", "location": "Paris", "workplaceType": "Hybrid",
        "descriptionPlain": "Kubernetes, ArgoCD", "department": "Engineering",
        "compensation": {"compensationTierSummary": "€70K – €90K"}, "publishedAt": "2026-11-02T10:00:00Z"}]})
    assert (a.work_mode, a.salary, a.department, a.description) == ("hybride", "€70K – €90K", "Engineering",
                                                                    "Kubernetes, ArgoCD")
    [g] = ats.parse_postings("greenhouse", {"jobs": [{
        "id": 7, "title": "SRE", "location": {"name": "Remote - Europe"}, "departments": [{"name": "Infra"}],
        "content": "&lt;p&gt;Nous &lt;strong&gt;recrutons&lt;/strong&gt;&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Terraform&lt;/li&gt;&lt;/ul&gt;"}]})
    assert g.description == "Nous recrutons\n\n- Terraform" and g.work_mode == "remote" and g.department == "Infra"
    [l] = ats.parse_postings("lever", [{
        "id": "x", "text": "Backend", "workplaceType": "onsite", "descriptionPlain": "Intro",
        "lists": [{"text": "Stack", "content": "<li>Python</li>"}], "salaryRange": {"min": 60000, "max": 80000, "currency": "EUR"},
        "categories": {"location": "Paris", "team": "Tech"}}])
    assert l.work_mode == "sur_site" and l.salary == "60000-80000 EUR" and "Python" in l.description


# --- Détection de l'ATS -------------------------------------------------------------------------


def boards(mapping):
    """Faux ATS : {(ats, slug): [Posting…]} ; tout le reste répond 404."""
    calls = []

    def fetch(kind, slug):
        calls.append((kind, slug))
        if (kind, slug) not in mapping:
            raise ats.ATSError("introuvable")
        return mapping[(kind, slug)]

    fetch.calls = calls
    return fetch


def posting(id_, title, desc="Rejoignez Tsuga", **kw):
    return ats.Posting(id_, title, f"https://x/{id_}", kw.pop("location", "Paris"), kw.pop("published_on", None),
                       description=desc, **kw)


def test_detect_from_careers_url_and_guess():
    tsuga = Company(name="Tsuga", slug="tsuga", careers_url="https://jobs.lever.co/tsuga-io")
    fetch = boards({("lever", "tsuga-io"): [posting("1", "Backend engineer")]})
    assert ats.detect_ats(tsuga, fetch) == ("lever", "tsuga-io")
    # Sans URL : identifiants devinés à partir du nom / domaine
    remanence = Company(name="Remanence AI", slug="r", website="https://remanence.ai")
    fetch = boards({("ashby", "remanence"): [posting("1", "MTS", desc="At Remanence AI we build agents")]})
    assert ats.detect_ats(remanence, fetch) == ("ashby", "remanence")


def test_detect_refuses_homonym_board():
    nabla = Company(name="Nabla", slug="nabla")
    fetch = boards({("ashby", "nabla"): [posting("1", "Engineer", desc="Join Acme Corp")]})
    assert ats.detect_ats(nabla, fetch) is None


# --- Collecte quotidienne ----------------------------------------------------------------------------


def test_collect_company_imports_all_and_closes(db, catalog):
    acme = catalog["acme"]
    acme.ats, acme.ats_slug = "ashby", "acme"
    db.commit()
    today = date.today()
    fetch = boards({("ashby", "acme"): [
        posting("a1", "Backend Engineer", desc="Acme backend"),  # déjà connue (titre)
        posting("a2", "Account Executive", desc="Acme sales", department="Sales"),  # ajoutée (le tri est par utilisateur)
        posting("a3", "Platform Engineer", desc="Acme platform", work_mode="hybride", salary="70-90 k€",
                published_on=today - timedelta(days=3)),
        posting("a4", "Vieille offre", published_on=today - timedelta(days=120)),
    ]})
    stats = collector.collect_company(db, acme, fetch)
    db.commit()
    assert stats["verified"] == 1 and stats["closed"] == 1 and stats["too_old"] == 1 and len(stats["added"]) == 2
    platform = db.scalar(select(Job).where(Job.title == "Platform Engineer"))
    assert (platform.summary, platform.work_mode, platform.salary, platform.source) == (
        "Acme platform", "hybride", "70-90 k€", "ATS ashby")
    assert platform.verified_open_at is not None
    backend = catalog["jobs"][0]
    db.refresh(backend)
    assert backend.summary == "Acme backend"  # champ vide complété
    assert db.get(Job, catalog["jobs"][1].id).closed_at is not None  # Frontend absente de la liste
    # Deuxième collecte : aucun doublon
    again = collector.collect_company(db, acme, fetch)
    assert again["added"] == [] and again["verified"] == 3


def test_detection_is_throttled_weekly(db):
    company = Company(name="Inconnue", slug="inconnue")
    db.add(company)
    db.commit()
    fetch = boards({})
    first = collector.collect_company(db, company, fetch)
    assert first["error"] == "ATS introuvable automatiquement" and fetch.calls
    calls = len(fetch.calls)
    second = collector.collect_company(db, company, fetch)
    assert "cette semaine" in second["error"] and len(fetch.calls) == calls  # pas de nouvel essai
    later = collector.collect_company(db, company, fetch, now=datetime.now(UTC) + timedelta(days=8))
    assert later["error"] == "ATS introuvable automatiquement"


def test_collect_all_records_a_run(db, catalog, monkeypatch):
    acme = catalog["acme"]
    acme.ats, acme.ats_slug = "lever", "acme"
    db.commit()
    fetch = boards({("lever", "acme"): [posting("l1", "Data Platform Engineer", desc="Acme data")]})
    run = collector.collect_all(db, fetch=fetch, detect=lambda c, f: None)
    assert run.kind == "collecte" and run.status == "termine" and run.jobs_added == 1 and run.companies_checked == 2
    assert run.details["jobs_added"] and any("Globex" in e for e in run.details["errors"])
    assert db.scalar(select(SearchRun)).summary.startswith("Collecte : 2 entreprises, 1 offres ajoutées")


def test_next_run_at_is_tomorrow_after_the_hour():
    now = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    nxt = collector.next_run_at(now, "06:30")
    assert nxt > now and nxt - now < timedelta(days=1)


# --- Pertinence : pré-filtre gratuit ---------------------------------------------------------------


@pytest.mark.parametrize("title, category, excluded", [
    ("Account Executive", None, True),
    ("Senior Marketing Manager", None, True),
    ("Talent Acquisition Partner", None, True),
    ("Product Designer", None, True),
    ("Sales Engineer", None, False),  # technique : laissé à l'évaluation
    ("Platform Engineer", "Sales", False),
    ("Chief of Staff", None, True),
    ("Office Manager", "People", True),
    ("Business Analyst", "Sales", True),
])
def test_role_prefilter(title, category, excluded):
    data = ProfileData.model_validate({"preferences": {"roles": ["Platform engineer"]}})
    job = Job(company=Company(name="X", slug="x"), title=title, category=category)
    assert (hard_exclusion(job, data) is not None) == excluded


def test_role_prefilter_respects_wanted_roles():
    data = ProfileData.model_validate({"preferences": {"roles": ["Product manager"]}})
    assert hard_exclusion(Job(company=Company(name="X", slug="x"), title="Senior Product Manager"), data) is None


@pytest.mark.parametrize("location, mode, excluded", [
    ("San Francisco, CA", None, True),
    ("London", None, True),
    ("New York", "remote", False),
    ("Paris, France", None, False),
    ("Remote - Europe", None, False),
    (None, None, False),
])
def test_location_prefilter(location, mode, excluded):
    data = ProfileData.model_validate({"preferences": {"locations": ["Paris"]}})
    job = Job(company=Company(name="X", slug="x"), title="Platform Engineer", location=location, work_mode=mode)
    assert (hard_exclusion(job, data) is not None) == excluded


def test_prefilter_saves_gemini_calls(db, alice, catalog, as_user, fake_llm):
    as_user(alice).patch("/api/profile", json={"skills": [{"name": "Python"}], "preferences": {"locations": ["Paris"]}})
    acme = catalog["acme"]
    db.add_all([Job(company=acme, title="Account Executive"), Job(company=acme, title="SRE", location="Austin, TX")])
    db.commit()
    assess_user(db, alice, fake_llm)
    sent = {j["titre"] for call in fake_llm.eval_calls for j in call["jobs"]}
    assert sent == {"Backend Engineer", "Frontend Engineer", "Data Engineer"}  # pré-filtrées : jamais envoyées


# --- Découverte : ATS proposé par l'agent, testé par Mire ------------------------------------------


def test_agent_ats_probe(db, catalog, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("AGENT_TOKEN", "t")
    client = TestClient(app, headers={"Authorization": "Bearer t"})
    acme = catalog["acme"]
    acme.ats, acme.ats_slug = "lever", "acme-old"
    db.commit()
    fake = boards({("ashby", "acme"): [posting("1", "Engineer", desc="Join Acme")],
                   ("ashby", "other"): [posting("2", "Engineer", desc="Join Globex")]})
    monkeypatch.setattr(ats, "fetch_postings", fake)

    assert client.post(f"/api/agent/companies/{acme.id}/ats", json={"ats": "ashby", "slug": "nope"}).json()["ok"] is False
    homonym = client.post(f"/api/agent/companies/{acme.id}/ats", json={"ats": "ashby", "slug": "other"}).json()
    assert homonym["ok"] is False and "pas être celui" in homonym["error"]
    # L'ancien identifiant (lever/acme-old) ne fonctionne pas : remplacé
    ok = client.post(f"/api/agent/companies/{acme.id}/ats", json={"ats": "ashby", "slug": "acme"}).json()
    assert ok["ok"] is True and ok["postings"] == 1
    db.refresh(acme)
    assert (acme.ats, acme.ats_slug) == ("ashby", "acme")
    # Un ATS fonctionnel n'est pas remplacé
    fake2 = boards({("ashby", "acme"): [posting("1", "Engineer", desc="Join Acme")],
                    ("lever", "acme"): [posting("3", "Engineer", desc="Join Acme")]})
    monkeypatch.setattr(ats, "fetch_postings", fake2)
    keep = client.post(f"/api/agent/companies/{acme.id}/ats", json={"ats": "lever", "slug": "acme"}).json()
    assert keep["ok"] is False and "déjà enregistré" in keep["error"]


def test_interrupted_collection_is_marked_and_caught_up(db):
    from app.db import SessionLocal

    db.add(SearchRun(kind="collecte", status="en_cours", details={}, jobs_seen=0, jobs_added=0, jobs_duplicates=0,
                     jobs_closed=0, companies_checked=0, sources_added=0))
    db.commit()
    c = collector.DailyCollector(SessionLocal)
    assert c._due_at_startup() is True  # aucune collecte terminée : rattrapage
    db.expire_all()
    assert db.scalar(select(SearchRun)).status == "echec"


def test_lenient_evaluation_parsing():
    from app.assessments import EvaluationBatch

    batch = EvaluationBatch.model_validate({"evaluations": [
        {"job_id": 1, "competences": 3, "stade": 2.0, "ownership": 1.5, "ia_concrete": 1, "xp": 1, "references": 0.5,
         "adjustment_reason": "x" * 400, "why": "y" * 500},
        {"job_id": "pas un nombre"},  # invalide : ignoré seul
        {"job_id": 3, "competences": 0.5, "stade": 0.5, "ownership": 0.5, "ia_concrete": 0.5, "xp": 0.5, "references": 0.5},
    ]})
    first = batch.evaluations[0]
    assert (first.competences, first.stade, first.ownership) == (1.0, 1.0, 0.75)  # points -> 0-1
    assert len(first.adjustment_reason) == 200 and len(first.why) == 300
    assert [e.job_id for e in batch.evaluations] == [1, 3]


def test_villejuif_is_not_abroad():
    data = ProfileData.model_validate({"preferences": {"locations": ["Paris"]}})
    job = Job(company=Company(name="X", slug="x"), title="Platform Engineer", location="Villejuif")
    assert hard_exclusion(job, data) is None


def test_agent_ats_refuses_empty_board(db, catalog, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("AGENT_TOKEN", "t")
    client = TestClient(app, headers={"Authorization": "Bearer t"})
    monkeypatch.setattr(ats, "fetch_postings", boards({("lever", "acme"): []}))
    out = client.post(f"/api/agent/companies/{catalog['acme'].id}/ats", json={"ats": "lever", "slug": "acme"}).json()
    assert out["ok"] is False and "vide" in out["error"]
    db.refresh(catalog["acme"])
    assert catalog["acme"].ats is None


TEAMTAILOR_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel><title>Spiko</title>
<item><title>Platform Engineer</title>
<description>&lt;h4&gt;Mission&lt;/h4&gt;&lt;ul&gt;&lt;li&gt;Kubernetes&lt;/li&gt;&lt;/ul&gt;</description>
<pubDate>Tue, 03 Nov 2026 18:16:56 +0100</pubDate><link>https://spiko.teamtailor.com/jobs/1-platform-engineer</link>
<remoteStatus>hybrid</remoteStatus><guid>g-1</guid>
<tt:locations><tt:location><tt:name>Paris</tt:name><tt:city>Paris</tt:city><tt:country>France</tt:country></tt:location></tt:locations>
<tt:department>Engineering</tt:department></item>
<item><title>Sales Lead</title><link>https://spiko.teamtailor.com/jobs/2</link><remoteStatus>none</remoteStatus><guid>g-2</guid></item>
</channel></rss>"""


def test_teamtailor_rss_parsing_and_fetch():
    import httpx
    import pytest

    posts = ats.parse_postings("teamtailor", TEAMTAILOR_RSS)
    first = posts[0]
    assert (first.ats_job_id, first.title, first.location, first.work_mode, first.department) == (
        "g-1", "Platform Engineer", "Paris, France", "hybride", "Engineering")
    assert str(first.published_on) == "2026-11-03" and "Kubernetes" in first.description
    assert posts[1].work_mode == "sur_site" and posts[1].location is None
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=TEAMTAILOR_RSS)))
    assert len(ats.fetch_postings("teamtailor", "spiko", client=client)) == 2
    with pytest.raises(ats.ATSError):
        ats.parse_postings("teamtailor", "<html>pas du xml")


def test_detect_teamtailor_from_careers_url():
    company = Company(name="Spiko", slug="spiko", careers_url="https://spiko.teamtailor.com/jobs")
    fetch = boards({("teamtailor", "spiko"): [posting("g-1", "Platform Engineer", desc="Spiko builds money market funds")]})
    assert ats.detect_ats(company, fetch) == ("teamtailor", "spiko")


def test_bulk_companies(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("AGENT_TOKEN", "t")
    client = TestClient(app, headers={"Authorization": "Bearer t"})
    run_id = client.post("/api/agent/runs", json={"kind": "decouverte"}).json()["run_id"]
    payload = {"run_id": run_id, "companies": [
        {"name": "Alpha", "website": "alpha.io", "funding_stage": "Seed", "funding_amount_meur": 4},
        {"name": "Beta", "website": "beta.ai", "funding_stage": "Série A"},
    ]}
    first = client.post("/api/agent/companies/bulk", json=payload).json()
    assert (first["created"], first["existing"]) == (2, 0)
    again = client.post("/api/agent/companies/bulk", json={"companies": [
        {"name": "alpha", "website": "alpha.io", "city": "Paris"}, {"name": "Gamma", "website": "gamma.fr"}]}).json()
    assert (again["created"], again["existing"]) == (1, 1)
    assert again["companies"][0]["completed"] == ["city"]
    assert db.scalar(select(Company).where(Company.name == "Alpha")).funding_stage == "Seed"
    assert client.post("/api/agent/companies/bulk", json={"companies": []}).status_code == 422
