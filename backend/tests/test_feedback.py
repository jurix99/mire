"""Retours sur les offres : J'aime / Pas intéressé, raisons, exclusion d'entreprise proposée."""

from sqlalchemy import select

from app.models import ApplicationEvent, JobFeedback


def test_dislike_sets_status_and_records_reasons(db, alice, catalog, as_user):
    client = as_user(alice)
    job = catalog["jobs"][1]
    before = client.get("/api/profile").json()
    r = client.post(f"/api/jobs/{job.id}/feedback",
                    json={"sentiment": "dislike", "reasons": ["trop_front"], "text": "TypeScript partout"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["job"]["display_status"] == "pas_interesse"
    assert data["feedback"]["reason_labels"] == ["Trop front / TypeScript"]
    assert data["new_feedback"] == 1 and data["suggest_synthesis"] is False and data["proposal"] is None
    # Changement de statut historisé, avec la raison
    [event] = db.scalars(select(ApplicationEvent)).all()
    assert (event.to_status, event.note) == ("pas_interesse", "Trop front / TypeScript ; TypeScript partout")
    # Le profil n'est pas modifié (seules les règles déduites, validées plus tard, le feront)
    assert client.get("/api/profile").json() == before


def test_like_adds_reference_job_directly(alice, catalog, as_user):
    client = as_user(alice)
    job = catalog["jobs"][0]
    data = client.post(f"/api/jobs/{job.id}/feedback", json={"sentiment": "like", "text": "Stack GitOps exacte"}).json()
    assert data["job"]["display_status"] == "nouveau"  # aimer ne pose pas de statut
    refs = client.get("/api/profile").json()["reference_jobs"]
    assert refs == [{"job_id": job.id, "title": "Backend Engineer", "company": "Acme", "why": "Stack GitOps exacte"}]
    client.post(f"/api/jobs/{job.id}/feedback", json={"sentiment": "like"})
    assert len(client.get("/api/profile").json()["reference_jobs"]) == 1  # pas de doublon


def test_already_interviewed_proposes_company_exclusion(alice, catalog, as_user):
    client = as_user(alice)
    job = catalog["jobs"][2]  # Globex
    data = client.post(f"/api/jobs/{job.id}/feedback",
                       json={"sentiment": "dislike", "reasons": ["deja_entretien"]}).json()
    proposal = data["proposal"]
    [it] = proposal["items"]
    assert it["field"] == "excluded_companies" and it["value"]["name"] == "Globex"
    assert client.get("/api/profile").json()["excluded_companies"] == []  # rien sans confirmation
    profile = client.post(f"/api/profile/proposals/{proposal['import_id']}/apply").json()
    assert profile["excluded_companies"][0]["name"] == "Globex"
    # Déjà exclue : plus de proposition
    again = client.post(f"/api/jobs/{job.id}/feedback", json={"sentiment": "dislike", "reasons": ["deja_entretien"]})
    assert again.json()["proposal"] is None


def test_synthesis_suggested_after_three_new_feedbacks(alice, catalog, as_user):
    client = as_user(alice)
    for job, sentiment in zip(catalog["jobs"], ["dislike", "like", "dislike"], strict=True):
        last = client.post(f"/api/jobs/{job.id}/feedback", json={"sentiment": sentiment}).json()
    assert last["new_feedback"] == 3 and last["suggest_synthesis"] is True
    summary = client.get("/api/feedback").json()
    assert summary["new"] == 3 and summary["total"] == 3
    assert summary["recent"][0]["job_label"] == "Globex – Data Engineer"


def test_feedback_validation_and_privacy(db, alice, bob, catalog, as_user):
    job = catalog["jobs"][0]
    assert as_user(alice).post(f"/api/jobs/{job.id}/feedback",
                               json={"sentiment": "dislike", "reasons": ["inconnue"]}).status_code == 422
    as_user(alice).post(f"/api/jobs/{job.id}/feedback", json={"sentiment": "dislike"})
    assert as_user(bob).get("/api/feedback").json()["total"] == 0
    assert db.scalar(select(JobFeedback)).user_id == alice.id


def test_feedback_state_is_returned_on_each_job(alice, bob, catalog, as_user):
    """Le dernier retour de l'utilisateur (cœur / pouce remplis) est renvoyé avec chaque offre, pour lui seul."""
    job = catalog["jobs"][0]
    client = as_user(alice)
    r = client.post(f"/api/jobs/{job.id}/feedback", json={"sentiment": "like"})
    assert r.json()["job"]["feedback"] == "like"
    jobs = {j["id"]: j for j in client.get("/api/jobs").json()}
    assert jobs[job.id]["feedback"] == "like" and jobs[catalog["jobs"][1].id]["feedback"] is None
    assert client.get(f"/api/jobs/{job.id}").json()["feedback"] == "like"
    client.post(f"/api/jobs/{job.id}/feedback", json={"sentiment": "dislike", "reasons": []})
    assert client.get(f"/api/jobs/{job.id}").json()["feedback"] == "dislike"  # le plus récent l'emporte
    assert as_user(bob).get(f"/api/jobs/{job.id}").json()["feedback"] is None
