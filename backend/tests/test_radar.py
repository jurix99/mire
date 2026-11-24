"""Phase 2 : dédoublonnage, offres fermées, vérification via les ATS (HTTP simulé, aucun appel réseau)."""

import httpx
import pytest
from sqlalchemy import select

from app import ats, radar_api
from app.assessments import assess_user, jobs_to_assess
from app.dedup import normalize_url, title_key
from app.models import Company, Job
from app.profile import get_profile

# --- Dédoublonnage ---------------------------------------------------------------------


@pytest.mark.parametrize("url, key", [
    ("https://jobs.ashbyhq.com/remanence/123?utm_source=linkedin&ref=wttj", "https://jobs.ashbyhq.com/remanence/123"),
    ("http://www.Example.com/careers/job-1/", "https://example.com/careers/job-1"),
    ("example.com/jobs/42?gh_src=abc&lang=fr", "https://example.com/jobs/42?lang=fr"),
    ("https://acme.io/", None),  # page d'accueil : pas une offre
    ("", None),
])
def test_normalize_url(url, key):
    assert normalize_url(url) == key


def test_title_key():
    assert title_key("Sr. Platform Engineer (H/F)") == title_key("Senior platform engineer")
    assert title_key("Développeur Backend – Python") == "developpeur backend python"


def test_api_refuses_duplicates(alice, catalog, as_user):
    client = as_user(alice)
    acme = catalog["acme"].id
    r = client.post("/api/jobs", json={"company_id": acme, "title": "Platform Engineer",
                                       "url": "https://jobs.lever.co/acme/1?utm_source=x"})
    assert r.status_code == 201
    # Même URL (paramètres de suivi ignorés)
    dup_url = client.post("/api/jobs", json={"company_id": catalog["globex"].id, "title": "Autre titre",
                                             "url": "https://jobs.lever.co/acme/1/"})
    assert dup_url.status_code == 409 and "existe déjà" in dup_url.json()["detail"]
    # Même entreprise + même titre, URL différente (agrégateur)
    dup_title = client.post("/api/jobs", json={"company_id": acme, "title": "platform engineer",
                                               "url": "https://wttj.com/acme/platform"})
    assert dup_title.status_code == 409
    # Une modification ne peut pas créer de doublon
    other = client.post("/api/jobs", json={"company_id": acme, "title": "SRE"}).json()
    assert client.patch(f"/api/jobs/{other['id']}", json={"title": "Platform Engineer"}).status_code == 409


def test_seed_like_homepage_urls_are_not_unique_keys(db, catalog):
    acme = catalog["acme"]
    db.add_all([Job(company=acme, title="A", url="https://acme.io"), Job(company=acme, title="B", url="https://acme.io/")])
    db.commit()  # pas de violation d'unicité : une page d'accueil n'a pas de clé


# --- Lecture des ATS -----------------------------------------------------------------------

PAYLOADS = {
    "ashby": {"jobs": [
        {"id": "a1", "title": "Platform Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/a1", "location": "Paris",
         "publishedAt": "2026-11-02T10:00:00Z", "isListed": True},
        {"id": "a2", "title": "Caché", "isListed": False},
    ]},
    "lever": [{"id": "l1", "text": "MLOps Engineer", "hostedUrl": "https://jobs.lever.co/acme/l1",
               "categories": {"location": "Paris"}, "createdAt": 1762000000000}],
    "greenhouse": {"jobs": [{"id": 77, "title": "Backend Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/77",
                             "location": {"name": "Remote"}, "updated_at": "2026-10-01T00:00:00-04:00"}]},
    "recruitee": {"offers": [{"id": 5, "title": "SRE", "careers_url": "https://acme.recruitee.com/o/sre",
                              "location": "Lyon", "published_at": "2026-09-30 10:00:00 UTC"}]},
}


@pytest.mark.parametrize("name", list(PAYLOADS))
def test_fetch_and_parse_each_ats(name):
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=PAYLOADS[name])

    postings = ats.fetch_postings(name, "acme", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert "acme" in seen["url"]
    assert postings and all(p.title for p in postings)
    if name == "ashby":
        assert [p.ats_job_id for p in postings] == ["a1"]  # offre non listée ignorée
        assert str(postings[0].published_on) == "2026-11-02"


def test_fetch_errors_and_slug_validation():
    with pytest.raises(ats.ATSError):
        ats.board_url("ashby", "../../etc/passwd")
    with pytest.raises(ats.ATSError):
        ats.board_url("yc", "acme")  # pas de lecture automatique
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(ats.ATSError, match="introuvable"):
        ats.fetch_postings("lever", "inconnue", client=client)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")))
    with pytest.raises(ats.ATSError, match="illisible"):
        ats.fetch_postings("lever", "acme", client=client)


# --- Vérification : ouvertes, fermées, nouvelles ------------------------------------------------


def acme_with_ats(db, catalog) -> Company:
    acme = catalog["acme"]
    acme.ats, acme.ats_slug = "ashby", "acme"
    db.commit()
    return acme


def listing(*postings):
    return lambda _ats, _slug: [ats.Posting(*p) for p in postings]


def test_check_marks_open_closed_and_new(db, alice, catalog, as_user, fake_llm):
    acme = acme_with_ats(db, catalog)
    backend, frontend, _ = catalog["jobs"]
    out = radar_api.check_and_commit(db, acme, listing(
        ("a1", "Backend Engineer", "https://jobs.ashbyhq.com/acme/a1"),  # = backend (même titre)
        ("a9", "Founding Engineer", "https://jobs.ashbyhq.com/acme/a9"),
    ))
    assert out.verified == [backend.id] and out.closed == [frontend.id]
    assert [p.title for p in out.new_postings] == ["Founding Engineer"]
    db.expire_all()
    assert db.get(Job, backend.id).ats_job_id == "a1" and db.get(Job, backend.id).verified_open_at
    assert db.get(Company, acme.id).last_checked_at is not None

    # « Offre fermée » chez qui ne l'a pas traitée ; statut de l'utilisateur conservé sinon
    client = as_user(alice)
    assert client.get(f"/api/jobs/{frontend.id}").json()["display_status"] == "fermee"
    client.put(f"/api/jobs/{frontend.id}/status", json={"status": "postule"})
    assert client.get(f"/api/jobs/{frontend.id}").json()["display_status"] == "postule"
    # Une offre fermée n'est plus évaluée
    client.patch("/api/profile", json={"skills": [{"name": "Python"}]})
    assert frontend.id not in [j.id for j in jobs_to_assess(db, alice, get_profile(db, alice))]

    # Republiée -> rouverte
    radar_api.check_and_commit(db, acme, listing(("a1", "Backend Engineer", None), ("a2", "Frontend Engineer", None)))
    db.expire_all()
    assert db.get(Job, frontend.id).closed_at is None


def test_empty_listing_or_error_never_closes(db, catalog):
    acme = acme_with_ats(db, catalog)
    out = radar_api.check_and_commit(db, acme, listing())
    assert out.closed == [] and out.listed == 0

    def broken(_a, _s):
        raise ats.ATSError("ATS injoignable")

    out = radar_api.check_and_commit(db, acme, broken)
    assert out.error == "ATS injoignable" and out.closed == []
    assert all(j.closed_at is None for j in db.scalars(select(Job)))


def test_check_requires_ats(alice, catalog, as_user):
    r = as_user(alice).post(f"/api/companies/{catalog['globex'].id}/check").json()
    assert "Renseignez l'ATS" in r["error"]


def test_import_new_postings_without_duplicates(db, catalog, fake_llm):
    acme = acme_with_ats(db, catalog)
    fetch = listing(("a9", "Founding Engineer", "https://jobs.ashbyhq.com/acme/a9", "Paris"),
                    ("a1", "Backend Engineer", "https://jobs.ashbyhq.com/acme/a1"))
    out = radar_api.import_postings(db, acme, ["a9", "a1", "zz"], fetch)
    assert len(out.created) == 1 and out.duplicates == [catalog["jobs"][0].id] and out.missing == ["zz"]
    job = db.get(Job, out.created[0])
    assert (job.title, job.location, job.ats_job_id, job.source) == ("Founding Engineer", "Paris", "a9", "ATS ashby")
    assert job.verified_open_at is not None
    # Relancer l'import ne recrée rien
    again = radar_api.import_postings(db, acme, ["a9"], fetch)
    assert again.created == [] and again.duplicates == out.created


def test_company_ats_fields_validated(alice, catalog, as_user):
    client = as_user(alice)
    ok = client.patch(f"/api/companies/{catalog['acme'].id}", json={"ats": "lever", "ats_slug": "acme-io",
                                                                     "headcount": "11-50"})
    assert ok.status_code == 200 and ok.json()["ats_slug"] == "acme-io"
    assert client.patch(f"/api/companies/{catalog['acme'].id}", json={"ats_slug": "a/../b"}).status_code == 422
    assert client.patch(f"/api/companies/{catalog['acme'].id}", json={"ats": "workday"}).status_code == 422


def test_closed_jobs_hidden_from_dashboard(db, alice, catalog, as_user, fake_llm):
    client = as_user(alice)
    client.patch("/api/profile", json={"skills": [{"name": "Python"}]})
    assess_user(db, alice, fake_llm)
    acme = acme_with_ats(db, catalog)
    radar_api.check_and_commit(db, acme, listing(("a1", "Backend Engineer", None)))
    todo = [j["title"] for j in client.get("/api/dashboard").json()["priority_jobs"]]
    assert "Frontend Engineer" not in todo


# --- Sources et passages (côté utilisateur) ----------------------------------------------------


def test_seed_sources_once(db):
    from app.models import Source
    from app.seed import seed_sources

    seed_sources(db)
    seed_sources(db)
    sources = db.scalars(select(Source)).all()
    assert len(sources) >= 20
    breega = next(s for s in sources if s.name.startswith("Breega"))
    assert breega.active is False
    assert next(s for s in sources if s.name.startswith("Flaire")).auto_readable is False


def test_user_manages_sources(alice, as_user):
    client = as_user(alice)
    s = client.post("/api/sources", json={"name": "Otta", "kind": "job_board", "query": "https://otta.com"}).json()
    assert s["added_by"] == "user" and s["active"] is True
    assert client.post("/api/sources", json={"name": "otta", "kind": "job_board"}).status_code == 409
    off = client.patch(f"/api/sources/{s['id']}", json={"active": False, "notes": "Peu de postes à Paris"}).json()
    assert off["active"] is False and off["notes"] == "Peu de postes à Paris"
    assert client.post("/api/sources", json={"name": "X", "kind": "inconnu"}).status_code == 422


def test_runs_listing_shows_my_summary(db, alice, bob, as_user):
    from app.models import SearchRun

    db.add(SearchRun(kind="planifie", status="termine", jobs_seen=30, jobs_added=4, jobs_duplicates=2, jobs_closed=1,
                     companies_checked=5, sources_added=1, summary="Bon passage", details={"per_user": [
                         {"user_id": alice.id, "nouvelles": 2, "top": [{"titre": "Platform Engineer"}]},
                         {"user_id": bob.id, "nouvelles": 0, "top": []}]}))
    db.commit()
    runs = as_user(alice).get("/api/runs").json()
    assert runs["runs"][0]["mine"]["nouvelles"] == 2
    assert as_user(bob).get("/api/runs").json()["runs"][0]["mine"]["nouvelles"] == 0


def test_import_accepts_old_postings_still_listed(db, catalog):
    from datetime import date, timedelta

    acme = acme_with_ats(db, catalog)
    old = date.today() - timedelta(days=90)
    fetch = lambda _a, _s: [ats.Posting("o1", "Vieille offre", None, None, old),  # noqa: E731
                            ats.Posting("n1", "Offre récente", None, None, date.today())]
    out = radar_api.import_postings(db, acme, ["o1", "n1"], fetch)
    assert len(out.created) == 2  # encore listée par l'ATS : ouverte, quelle que soit sa date


def test_api_text_limits_fit_database_columns():
    """SQLite ne vérifie pas la longueur des VARCHAR : on compare les limites de l'API aux colonnes (PostgreSQL)."""
    from sqlalchemy import String

    from app.models import Company, Job
    from app.schemas import CompanyPatch, JobPatch

    for schema, model in ((JobPatch, Job), (CompanyPatch, Company)):
        for name, field in schema.model_fields.items():
            column = model.__table__.columns.get(name)
            max_len = next((m.max_length for m in field.metadata if getattr(m, "max_length", None)), None)
            if column is not None and isinstance(column.type, String) and column.type.length and max_len:
                assert max_len <= column.type.length, f"{model.__name__}.{name} : API {max_len} > colonne {column.type.length}"
