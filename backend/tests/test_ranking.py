"""Classement (ranking.py) et retours réversibles sur les offres et les entreprises."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app import embeddings, ranking
from app.models import Application, Company, CompanyFeedback, Job, JobSkill, UserProfile

PROFILE = {"years_experience": 4, "skills": [{"name": "Python"}, {"name": "Kubernetes"}],
           "preferences": {"roles": ["Backend"], "locations": ["Paris"], "work_modes": ["hybride", "remote"]}}
RECENT = date.today() - timedelta(days=10)


def add_job(db, company, title, family, skills=(), **fields):
    fields = {"location": "Paris", "work_mode": "hybride", "published_on": RECENT, **fields}
    job = Job(company=company, title=title, role_family=family, **fields)
    db.add(job)
    db.flush()
    db.add_all([JobSkill(job_id=job.id, skill_id=s, required=True) for s in skills])
    db.commit()
    return job


@pytest.fixture
def market(db):
    acme = Company(name="Acme", slug="acme", sector="SaaS")
    globex = Company(name="Globex", slug="globex", sector="Santé")
    initech = Company(name="Initech", slug="initech", sector="Fintech")
    db.add_all([acme, globex, initech])
    db.commit()
    return {
        "acme": acme, "globex": globex, "initech": initech,
        "backend": add_job(db, acme, "Backend Engineer", "backend", ["python", "kubernetes"]),
        "front": add_job(db, globex, "Frontend Engineer", "front_mobile", ["react", "typescript_javascript"]),
        "front2": add_job(db, initech, "Mobile Engineer", "front_mobile", ["react"]),
        "data": add_job(db, globex, "Data Engineer", "data", ["python", "sql"]),
    }


def setup(as_user, user):
    client = as_user(user)
    assert client.patch("/api/profile", json=PROFILE).status_code == 200
    return client


def listed(client):
    return client.get("/api/jobs?brief=true").json()


def by_id(client):
    return {j["id"]: j for j in listed(client)}


def test_jobs_are_ranked_without_any_llm_note(alice, market, as_user):
    client = setup(as_user, alice)
    jobs = listed(client)
    assert [j["id"] for j in jobs][:2] == [market["backend"].id, market["data"].id]  # visé, puis famille proche
    top = jobs[0]["match"]
    assert top["score"] > jobs[1]["match"]["score"] > by_id(client)[market["front"].id]["match"]["score"]
    assert "Métier visé : Backend" in top["reasons"] and any("Python" in r for r in top["reasons"])
    front = by_id(client)[market["front"].id]["match"]
    assert any("Hors de vos métiers visés" in w for w in front["warnings"])
    assert jobs[0]["city"] == "Paris" and jobs[0]["summary"] is None


def test_like_reranks_immediately_and_unlike_restores(db, alice, market, as_user):
    client = setup(as_user, alice)
    before = by_id(client)
    version = db.scalar(select(UserProfile.version).where(UserProfile.user_id == alice.id))

    r = client.post(f"/api/jobs/{market['front'].id}/feedback", json={"sentiment": "like"})
    assert r.status_code == 200, r.text
    after = by_id(client)
    similar = market["front2"].id
    assert after[similar]["match"]["score"] > before[similar]["match"]["score"]
    assert any(x.startswith("Proche de vos J'aime") for x in after[similar]["match"]["reasons"])
    db.expire_all()
    # Un J'aime ne change pas la version du profil : aucune réévaluation Gemini déclenchée
    assert db.scalar(select(UserProfile.version).where(UserProfile.user_id == alice.id)) == version

    r = client.delete(f"/api/jobs/{market['front'].id}/feedback")
    assert r.status_code == 200 and r.json()["feedback"] is None
    assert by_id(client)[similar]["match"]["score"] == before[similar]["match"]["score"]
    assert client.get("/api/profile").json()["reference_jobs"] == []


def test_dislike_hides_and_lowers_similar_then_can_be_undone(db, alice, market, as_user):
    client = setup(as_user, alice)
    before = by_id(client)
    client.post(f"/api/jobs/{market['front'].id}/feedback", json={"sentiment": "dislike", "reasons": ["trop_front"]})
    after = by_id(client)
    assert after[market["front"].id]["hidden_reason"] == ranking.DISLIKED_JOB
    assert after[market["front2"].id]["match"]["score"] < before[market["front2"].id]["match"]["score"]
    assert [j["id"] for j in listed(client)][-1] == market["front"].id  # les offres écartées en dernier

    restored = client.delete(f"/api/jobs/{market['front'].id}/feedback").json()
    assert restored["hidden_reason"] is None and restored["display_status"] == "nouveau"
    assert db.scalars(select(Application)).all() == []  # process créé par le seul retour : supprimé
    assert by_id(client)[market["front2"].id]["match"]["score"] == before[market["front2"].id]["match"]["score"]


def test_undoing_a_dislike_restores_the_previous_status(alice, market, as_user):
    client = setup(as_user, alice)
    job = market["backend"].id
    client.put(f"/api/jobs/{job}/status", json={"status": "a_postuler"})
    client.post(f"/api/jobs/{job}/feedback", json={"sentiment": "dislike"})
    assert client.delete(f"/api/jobs/{job}/feedback").json()["status"] == "a_postuler"


def test_switching_from_dislike_to_like(alice, market, as_user):
    client = setup(as_user, alice)
    job = market["backend"].id
    client.post(f"/api/jobs/{job}/feedback", json={"sentiment": "dislike"})
    data = client.post(f"/api/jobs/{job}/feedback", json={"sentiment": "like"}).json()["job"]
    assert (data["feedback"], data["status"], data["hidden_reason"]) == ("like", "nouveau", None)


def test_company_reactions_rank_and_hide(db, alice, bob, market, as_user):
    client = setup(as_user, alice)
    companies = {c["name"]: c for c in client.get("/api/companies").json()}
    acme = companies["Acme"]
    assert acme["open_jobs"] == 1 and acme["top_jobs"][0]["title"] == "Backend Engineer"
    assert acme["match"]["score"] > companies["Initech"]["match"]["score"] and acme["reaction"] is None

    globex = market["globex"].id
    before = by_id(client)[market["data"].id]["match"]["score"]
    liked = client.put(f"/api/companies/{globex}/reaction", json={"value": "like"}).json()
    assert liked["reaction"] == "like"
    assert by_id(client)[market["data"].id]["match"]["score"] > before

    hidden = client.put(f"/api/companies/{globex}/reaction", json={"value": "dislike"}).json()
    assert hidden["hidden_reason"] == ranking.DISLIKED_COMPANY
    assert by_id(client)[market["data"].id]["hidden_reason"] == ranking.DISLIKED_COMPANY
    assert as_user(bob).get(f"/api/companies/{globex}").json()["reaction"] is None  # propre à chacun

    client = as_user(alice)
    cleared = client.put(f"/api/companies/{globex}/reaction", json={"value": None}).json()
    assert cleared["reaction"] is None and cleared["hidden_reason"] is None
    assert db.scalars(select(CompanyFeedback)).all() == []
    assert client.put(f"/api/companies/{globex}/reaction", json={"value": "autre"}).status_code == 422
    assert client.put("/api/companies/999999/reaction", json={"value": "like"}).status_code == 404


def test_profile_changes_apply_to_hidden_jobs_at_once(db, alice, market, as_user):
    client = setup(as_user, alice)
    sales = add_job(db, market["acme"], "Account Executive", "sales")
    assert "hors de votre cible" in by_id(client)[sales.id]["hidden_reason"]
    client.patch("/api/profile", json={"preferences": {**PROFILE["preferences"], "roles": ["Sales (SDR / BDR / AE)"]}})
    assert by_id(client)[sales.id]["hidden_reason"] is None
    client.patch("/api/profile", json={"excluded_companies": [{"name": "Acme"}]})
    assert by_id(client)[sales.id]["hidden_reason"].startswith("Entreprise exclue")


def test_target_families_from_labels_and_free_text():
    from app.profile import ProfileData

    data = ProfileData.model_validate({"preferences": {"roles": ["Platform / DevOps / SRE", "développeur backend"]}})
    assert ranking.target_families(data) == {"platform_devops_sre", "backend"}
    assert ranking.target_families(ProfileData(headline="Data engineer")) == {"data"}
    assert ranking.target_families(ProfileData()) == set()


@pytest.mark.parametrize(("location", "city"), [
    ("Paris, France", "Paris"), ("Hybrid Paris", "Paris"), ("Paris office", "Paris"),
    ("Anywhere in France, Belgium", "France"), ("Madrid, Spain; Barcelona, Spain", "Madrid"), (None, None),
])
def test_city_of(location, city):
    assert ranking.city_of(location) == city


def test_free_plan_gets_no_explanation_and_dislikes_reveal_nothing(db, bob, market, as_user, monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "true")
    client = setup(as_user, bob)
    jobs = listed(client)
    locked = [j for j in jobs if j["locked"]]
    visible = [j for j in jobs if not j["locked"]]
    assert locked and visible
    assert all(j["match"] is None and j["hidden_reason"] is None and j["city"] is None for j in locked)
    assert all(j["match"]["reasons"] == [] and j["match"]["warnings"] == [] for j in visible)
    for job in visible:  # écarter les offres en clair n'en dévoile pas d'autres
        client.post(f"/api/jobs/{job['id']}/feedback", json={"sentiment": "dislike"})
    assert {j["id"] for j in listed(client) if j["locked"]} == {j["id"] for j in locked}
    assert client.delete(f"/api/jobs/{locked[0]['id']}/feedback").status_code == 402
    hidden_company = next(c for c in client.get("/api/companies").json() if c["locked"])
    assert hidden_company["match"] is None and hidden_company["top_jobs"] == []
    assert client.put(f"/api/companies/{hidden_company['id']}/reaction",
                      json={"value": "like"}).status_code == 402


def test_options(alice, market, as_user):
    data = setup(as_user, alice).get("/api/options").json()
    assert data["locations"] == ["Paris"]
    assert {"key": "backend", "label": "Backend", "group": "Tech"} in data["families"]
    assert data["sectors"] and data["seniority"]


def test_long_texts_are_embedded_in_pieces():
    text = " ".join(f"mot{i}" for i in range(2000))
    pieces = embeddings.chunks(text)
    assert len(pieces) == embeddings.MAX_CHUNKS and all(len(p) <= embeddings.CHUNK_CHARS for p in pieces)
    assert pieces[0].startswith("mot0 ") and pieces[-1].endswith("mot1999")  # début et fin gardés
    job = Job(title="Backend Engineer", summary=text)
    assert embeddings.job_texts(job)[0] == "Backend Engineer" and len(embeddings.job_texts(job)) == embeddings.MAX_CHUNKS + 1


def test_account_export_and_deletion_include_company_reactions(db, alice, market, as_user):
    client = setup(as_user, alice)
    client.put(f"/api/companies/{market['acme'].id}/reaction", json={"value": "like"})
    export = client.get("/api/me/export").json()
    assert export["company_feedback"][0]["sentiment"] == "like"
    assert client.delete("/api/me").status_code == 204
    assert db.scalars(select(CompanyFeedback)).all() == []
