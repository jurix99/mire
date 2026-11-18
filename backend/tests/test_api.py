"""API existante : statuts des postes, process, isolation entre utilisateurs."""

from sqlalchemy import select

from app.models import ApplicationEvent


def test_set_job_status_creates_application_and_event(db, alice, catalog, as_user):
    client = as_user(alice)
    job = catalog["jobs"][0]

    r = client.put(f"/api/jobs/{job.id}/status", json={"status": "postule"})
    assert r.status_code == 200
    assert r.json()["status"] == "postule"

    apps = client.get("/api/applications").json()
    assert [a["job"]["id"] for a in apps] == [job.id]
    assert apps[0]["applied_on"] is not None
    events = db.scalars(select(ApplicationEvent)).all()
    assert [(e.from_status, e.to_status) for e in events] == [("nouveau", "postule")]


def test_jobs_filtered_by_company(alice, catalog, as_user):
    client = as_user(alice)
    r = client.get(f"/api/jobs?company_id={catalog['globex'].id}")
    assert [j["title"] for j in r.json()] == ["Data Engineer"]


def test_applications_are_private(alice, bob, catalog, as_user):
    job = catalog["jobs"][0]
    app_id = as_user(alice).put(f"/api/jobs/{job.id}/status", json={"status": "entretien"}).json()["application_id"]

    bob_client = as_user(bob)
    assert bob_client.get("/api/applications").json() == []
    assert bob_client.patch(f"/api/applications/{app_id}", json={"notes": "x"}).status_code == 404
    # Pour Bob, le poste reste « nouveau »
    jobs = {j["id"]: j for j in bob_client.get("/api/jobs").json()}
    assert jobs[job.id]["status"] == "nouveau"


def test_dashboard(alice, catalog, as_user):
    data = as_user(alice).get("/api/dashboard").json()
    assert data["stats"]["jobs"] == 3
    assert data["priority_jobs"][0]["title"] == "Backend Engineer"


def test_get_job_detail_with_own_application_only(alice, bob, catalog, as_user):
    job = catalog["jobs"][0]
    alice_client = as_user(alice)
    app_id = alice_client.put(f"/api/jobs/{job.id}/status", json={"status": "entretien"}).json()["application_id"]
    alice_client.patch(f"/api/applications/{app_id}", json={"notes": "Note privée"})

    data = alice_client.get(f"/api/jobs/{job.id}").json()
    assert data["title"] == "Backend Engineer" and data["stack"] == "Python, PostgreSQL"
    assert data["application"]["notes"] == "Note privée"

    bob_data = as_user(bob).get(f"/api/jobs/{job.id}").json()
    assert bob_data["status"] == "nouveau" and bob_data["application"] is None
    assert as_user(bob).get("/api/jobs/9999").status_code == 404
