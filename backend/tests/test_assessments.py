"""Évaluation automatique par utilisateur (Gemini simulé) et règle d'or."""

from sqlalchemy import select

from app.assessments import assess_user, hard_exclusion, jobs_to_assess, required_years
from app.models import Company, Job, JobAssessment
from app.profile import ProfileData, get_profile

PROFILE = {
    "years_experience": 5,
    "skills": [{"name": "Python"}, {"name": "Kubernetes"}],
    "excluded_companies": [{"name": "Comand AI", "reason": "entretiens déjà faits"}],
}


def setup_profile(as_user, user, **extra):
    as_user(user).patch("/api/profile", json={**PROFILE, **extra})


def add_job(db, company: str, title: str, **fields) -> Job:
    c = db.scalar(select(Company).where(Company.name == company)) or Company(name=company, slug=company.lower())
    job = Job(company=c, title=title, **fields)
    db.add(job)
    db.commit()
    return job


# --- Exclusions dures ------------------------------------------------------------


def test_required_years_parsing():
    assert required_years("Vous avez 7+ ans d'expérience en backend") == 7
    assert required_years("At least 8 years of experience with Go") == 8
    assert required_years("Une équipe de 40 ans de moyenne d'âge") is None  # pas une exigence d'expérience
    assert required_years("6 ans", field_is_experience=True) == 6


def test_hard_exclusions(db):
    data = ProfileData.model_validate(PROFILE)
    comand = Job(company=Company(name="comand ai", slug="c"), title="FDE")
    stage = Job(company=Company(name="X", slug="x"), title="Stage DevOps")
    senior = Job(company=Company(name="Y", slug="y"), title="Platform", summary="7+ years of experience required")
    ok = Job(company=Company(name="Z", slug="z"), title="Platform", seniority="5+ ans",
             summary="6+ years of experience appreciated")
    assert "Entreprise exclue" in hard_exclusion(comand, data)
    assert hard_exclusion(stage, data) == "Stage ou alternance"
    assert "7+ ans" in hard_exclusion(senior, data)
    assert hard_exclusion(ok, data) is None


# --- Note, verdict et passage par Gemini ---------------------------------------------


def test_assessment_scores_and_verdicts(db, alice, catalog, as_user, fake_llm):
    setup_profile(as_user, alice)
    comand = add_job(db, "Comand AI", "Forward Deployed Engineer")
    backend, frontend, data_job = catalog["jobs"]
    fake_llm.grade = lambda j: {
        backend.id: {"competences": 1, "stade": 1, "ownership": 1, "ia_concrete": 1, "xp": 1, "references": 0.5},
        frontend.id: {"competences": 0.9, "adjustment": -3, "adjustment_reason": "front TypeScript au cœur du poste"},
        data_job.id: {"exclusion": "Cloud imposé non maîtrisé (deep AWS)"},
    }.get(j["job_id"], {})

    stats = assess_user(db, alice, fake_llm)
    assert stats == {"evaluated": 4, "excluded": 3, "kept": 1, "remaining": 0}
    # L'entreprise exclue n'est jamais envoyée au modèle
    sent = [j["job_id"] for call in fake_llm.eval_calls for j in call["jobs"]]
    assert comand.id not in sent
    assert "Entreprises exclues : Comand AI" in fake_llm.eval_calls[0]["context"]

    jobs = {j["id"]: j for j in as_user(alice).get("/api/jobs").json()}
    b = jobs[backend.id]
    assert b["score"] == 9.5 and b["display_status"] == "nouveau"
    assert b["assessment"]["why"] == "Intéressant : Backend Engineer"
    assert {c["key"]: c["points"] for c in b["assessment"]["criteria"]}["competences"] == 3
    f = jobs[frontend.id]
    assert f["display_status"] == "ecartee" and "sous le seuil" in f["assessment"]["exclusion"]
    assert "TypeScript" in f["assessment"]["exclusion"]
    assert jobs[data_job.id]["assessment"]["exclusion"] == "Cloud imposé non maîtrisé (deep AWS)"
    assert jobs[comand.id]["assessment"]["exclusion"].startswith("Entreprise exclue : Comand AI")
    assert jobs[comand.id]["score"] is None
    # Les postes sont triés par la note de l'utilisateur
    assert next(iter(jobs.values()))["id"] == backend.id


def test_weights_and_threshold_are_the_users(db, alice, catalog, as_user, fake_llm):
    setup_profile(as_user, alice, scoring={"weights": {"stade": 0, "ownership": 0, "ia_concrete": 0, "xp": 0,
                                                       "references": 0}, "threshold": 8})
    fake_llm.grade = lambda j: {"competences": 0.75}
    assess_user(db, alice, fake_llm)
    job = as_user(alice).get(f"/api/jobs/{catalog['jobs'][0].id}").json()
    assert job["score"] == 7.5  # seul le critère compétences compte, ramené sur 10
    assert job["display_status"] == "ecartee"  # seuil personnel de 8


def test_dashboard_hides_excluded_jobs(db, alice, catalog, as_user, fake_llm):
    setup_profile(as_user, alice)
    backend = catalog["jobs"][0]
    fake_llm.grade = lambda j: {} if j["job_id"] == backend.id else {"exclusion": "hors critères"}
    assess_user(db, alice, fake_llm)
    data = as_user(alice).get("/api/dashboard").json()
    assert [j["id"] for j in data["priority_jobs"]] == [backend.id]
    assert data["stats"]["todo"] == 1


def test_job_forgotten_by_model_stays_pending(db, alice, catalog, as_user, fake_llm):
    setup_profile(as_user, alice)
    forgotten = catalog["jobs"][1].id
    fake_llm.grade = lambda j: None if j["job_id"] == forgotten else {}
    stats = assess_user(db, alice, fake_llm)
    assert stats["remaining"] == 1


def test_empty_profile_is_not_assessed(db, alice, catalog, fake_llm):
    assert assess_user(db, alice, fake_llm)["evaluated"] == 0
    assert not hasattr(fake_llm, "eval_calls")


# --- Règle d'or et réévaluation -------------------------------------------------------


def test_reassessment_respects_user_decisions(db, alice, catalog, as_user, fake_llm):
    client = as_user(alice)
    setup_profile(as_user, alice)
    backend, frontend, data_job = catalog["jobs"]
    assess_user(db, alice, fake_llm)

    # L'utilisateur traite un poste, corrige la note d'un autre, ne touche pas au troisième
    client.put(f"/api/jobs/{backend.id}/status", json={"status": "pas_interesse"})
    r = client.put(f"/api/jobs/{frontend.id}/assessment/manual", json={"score": 9})
    assert r.json()["score"] == 9 and r.json()["assessment"]["auto_score"] == 7.0

    # Le profil change : seule l'offre non traitée et non corrigée est réévaluée
    client.patch("/api/profile", json={"rules": [{"kind": "eviter", "text": "Data engineering au cœur"}]})
    profile = get_profile(db, alice)
    db.refresh(profile)
    assert [j.id for j in jobs_to_assess(db, alice, profile)] == [data_job.id]
    fake_llm.grade = lambda j: {"exclusion": "Data engineering au cœur du poste"}
    assess_user(db, alice, fake_llm)

    jobs = {j["id"]: j for j in client.get("/api/jobs").json()}
    assert jobs[backend.id]["display_status"] == "pas_interesse"  # statut posé par l'utilisateur
    assert jobs[frontend.id]["score"] == 9  # correction conservée
    assert jobs[data_job.id]["display_status"] == "ecartee"
    assert not any(j["assessment"]["stale"] for j in jobs.values() if j["id"] != backend.id)


def test_user_can_bring_back_an_excluded_job(db, alice, catalog, as_user, fake_llm):
    client = as_user(alice)
    setup_profile(as_user, alice)
    fake_llm.grade = lambda j: {"exclusion": "FDE avec déplacements"}
    assess_user(db, alice, fake_llm)
    job_id = catalog["jobs"][0].id
    assert client.get(f"/api/jobs/{job_id}").json()["display_status"] == "ecartee"
    client.put(f"/api/jobs/{job_id}/status", json={"status": "nouveau"})
    assert client.get(f"/api/jobs/{job_id}").json()["display_status"] == "nouveau"


def test_job_update_invalidates_untreated_assessments(db, alice, bob, catalog, as_user, fake_llm):
    for u in (alice, bob):
        setup_profile(as_user, u)
        assess_user(db, u, fake_llm)
    job_id = catalog["jobs"][0].id
    as_user(bob).put(f"/api/jobs/{job_id}/status", json={"status": "postule"})
    as_user(alice).patch(f"/api/jobs/{job_id}", json={"summary": "Nouvelle description : Kubernetes + GitOps"})
    rows = {a.user_id: a for a in db.scalars(select(JobAssessment).where(JobAssessment.job_id == job_id))}
    db.expire_all()
    rows = {a.user_id: a for a in db.scalars(select(JobAssessment).where(JobAssessment.job_id == job_id))}
    assert rows[alice.id].profile_version == 0  # à réévaluer
    assert rows[bob.id].profile_version > 0  # Bob a traité l'offre : intouchée


def test_assessments_are_private(db, alice, bob, catalog, as_user, fake_llm):
    setup_profile(as_user, alice)
    assess_user(db, alice, fake_llm)
    job = as_user(bob).get(f"/api/jobs/{catalog['jobs'][0].id}").json()
    assert job["assessment"] is None and job["display_status"] == "nouveau"


def test_status_and_run_endpoints(alice, catalog, as_user, fake_llm):
    client = as_user(alice)
    status = client.get("/api/assessments/status").json()
    assert status["enabled"] is True and status["profile_ready"] is False
    setup_profile(as_user, alice)
    assert client.get("/api/assessments/status").json()["pending"] == 3
    r = client.post("/api/assessments/run").json()
    assert r["evaluated"] == 3 and r["remaining"] == 0
    assert client.get("/api/assessments/status").json()["pending"] == 0


def test_run_without_gemini(alice, as_user):
    assert as_user(alice).post("/api/assessments/run").status_code == 503


def test_chat_tool_sees_evaluation(db, alice, catalog, as_user, fake_llm):
    from app.chat_tools import ToolRunner

    setup_profile(as_user, alice)
    fake_llm.grade = lambda j: {"exclusion": "Trop front"} if j["titre"] == "Frontend Engineer" else {}
    assess_user(db, alice, fake_llm)
    out = ToolRunner(db, alice)("list_jobs", {"status": "ecartee"})["data"]
    assert [j["title"] for j in out["jobs"]] == ["Frontend Engineer"]
    assert out["jobs"][0]["raison_ecart"] == "Trop front"
    assert "error" in ToolRunner(db, alice)("list_jobs", {"status": "inconnu"})
