"""Offre Free : masquage côté serveur (aucune vraie valeur verrouillée dans le JSON) et quotas."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.assessments import Assessor
from app.chat_tools import ToolRunner
from app.db import SessionLocal
from app.models import Company, Job, JobAssessment, User

TODAY = date.today()
OLD = TODAY - timedelta(days=30)
PROFILE = {"years_experience": 5, "skills": [{"name": "Python"}], "positioning": "Platform engineer"}

# Valeurs réservées au Pro : elles ne doivent apparaître nulle part dans les réponses faites à un utilisateur Free
SECRETS = [
    "Ultra Secret Staff Engineer",  # titre d'une offre récente
    "123-456 kEUR",  # son salaire
    "Confidential summary of the role",  # sa description
    "jobs.example/secret-777",  # son lien
    "Hushhushcorp",  # entreprise dont toutes les offres sont récentes
    "hushhush.example",
    "Secretville",
    "Sequoia Capital Secret",  # investisseurs
    "Série Z confidentielle",  # tour
    "987.6",  # montant levé
    "stealthyslug",  # identifiant ATS
    "stealthy.example/careers",
    "Raison ultra confidentielle",  # « pourquoi » de l'évaluation
    "Rustacean-skill",  # compétences de l'évaluation
]


@pytest.fixture
def billing_on(monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "true")
    monkeypatch.setenv("PRO_USER_EMAILS", "alice@example.com")


@pytest.fixture
def pro_and_free(db, alice, bob, billing_on):
    """alice : Pro (PRO_USER_EMAILS) ; bob : Free."""
    alice.email = "alice@example.com"
    db.commit()
    return alice, bob


@pytest.fixture
def radar(db):
    """Une entreprise visible avec une offre ancienne, une entreprise dont l'unique offre est récente."""
    stealthy = Company(name="Stealthy", slug="stealthy", sector="DevTools", funding_stage="Série Z confidentielle",
                       funding_amount_meur=987.6, funding_announced_on=TODAY - timedelta(days=10),
                       investors="Sequoia Capital Secret", headcount="51-200", ats="ashby", ats_slug="stealthyslug",
                       careers_url="https://stealthy.example/careers")
    hush = Company(name="Hushhushcorp", slug="hush", sector="IA", website="https://hushhush.example",
                   city="Secretville", funding_announced_on=TODAY - timedelta(days=3), funding_amount_meur=987.6)
    old = Job(company=stealthy, title="Platform Engineer", published_on=OLD, stack="Python")
    fresh = Job(company=hush, title="Ultra Secret Staff Engineer", published_on=TODAY - timedelta(days=1),
                salary="123-456 kEUR", summary="Confidential summary of the role",
                url="https://jobs.example/secret-777")
    db.add_all([stealthy, hush, old, fresh])
    db.commit()
    return {"stealthy": stealthy, "hush": hush, "old": old, "fresh": fresh}


def assess(as_user, user, fake_llm):
    """Évaluation (Gemini simulé) avec un « pourquoi » et des compétences réservés au Pro."""
    as_user(user).patch("/api/profile", json=PROFILE)
    fake_llm.grade = lambda j: {"why": "Raison ultra confidentielle", "skills_have": ["Rustacean-skill"]}
    Assessor(SessionLocal, lambda: fake_llm).run_once()
    with SessionLocal() as s:
        return s.scalar(select(func.count(JobAssessment.id)).where(JobAssessment.user_id == user.id))


def leaks(text: str) -> list[str]:
    return [s for s in SECRETS if s in text]


# --- Masquage -----------------------------------------------------------------------------------


def test_free_user_never_receives_locked_values(pro_and_free, radar, as_user, fake_llm):
    _, bob = pro_and_free
    assert assess(as_user, bob, fake_llm) == 1  # Free : seule l'offre ancienne (visible) est évaluée
    client = as_user(bob)
    urls = ["/api/jobs", f"/api/jobs/{radar['fresh'].id}", f"/api/jobs/{radar['old'].id}", "/api/companies",
            f"/api/companies/{radar['hush'].id}", f"/api/companies/{radar['stealthy'].id}", "/api/dashboard",
            "/api/analytics", "/api/me", "/api/applications"]
    for url in urls:
        r = client.get(url)
        assert r.status_code == 200, (url, r.text)
        assert leaks(r.text) == [], url

    fresh = client.get(f"/api/jobs/{radar['fresh'].id}").json()
    assert fresh["locked"] is True and fresh["title"]["locked"] is True
    assert fresh["published_on"] == str(TODAY - timedelta(days=1))  # la date reste visible
    old = client.get(f"/api/jobs/{radar['old'].id}").json()
    assert old["title"] == "Platform Engineer" and old["company"]["name"] == "Stealthy"
    assert old["assessment"]["why"]["locked"] is True
    assert isinstance(old["assessment"]["score"], float)  # dans le top de la semaine : note visible
    hush = client.get(f"/api/companies/{radar['hush'].id}").json()
    assert hush["locked"] is True and hush["sector"] == "IA"  # seul le secteur reste en clair
    stealthy = client.get(f"/api/companies/{radar['stealthy'].id}").json()
    assert stealthy["name"] == "Stealthy" and stealthy["investors"]["locked"] is True
    assert client.get(f"/api/companies/{radar['hush'].id}/logo").status_code == 404

    me = client.get("/api/me").json()["billing"]
    assert me["plan"] == "free" and me["locked_jobs"] == 1 and me["locked_fundings"] == 2


def test_free_user_status_on_fresh_job_does_not_leak(pro_and_free, radar, as_user):
    _, bob = pro_and_free
    client = as_user(bob)
    r = client.put(f"/api/jobs/{radar['fresh'].id}/status", json={"status": "a_postuler"})
    assert r.status_code == 200 and leaks(r.text) == []
    assert leaks(client.get("/api/applications").text) == []
    assert leaks(client.get("/api/dashboard").text) == []
    # Pas de retour (J'aime / Pas intéressé) sur une offre verrouillée
    r = client.post(f"/api/jobs/{radar['fresh'].id}/feedback", json={"sentiment": "like"})
    assert r.status_code == 402 and r.json()["detail"]["code"] == "pro_required"


def test_chat_tools_mask_for_free_user(pro_and_free, radar, as_user, fake_llm):
    _, bob = pro_and_free
    assess(as_user, bob, fake_llm)
    with SessionLocal() as s:
        user = s.get(User, bob.id)
        run = ToolRunner(s, user)
        for name, args in [("list_jobs", {}), ("get_job", {"job_id": radar["fresh"].id}),
                           ("get_job", {"job_id": radar["old"].id}), ("get_dashboard", {}),
                           ("list_applications", {})]:
            out = json.dumps(run(name, args), ensure_ascii=False, default=str)
            assert leaks(out) == [], (name, out)
        fresh = run("get_job", {"job_id": radar["fresh"].id})["data"]
        assert fresh["title"] == "[réservé au Pro]"


def test_chat_system_prompt_forbids_inventing_locked_data(pro_and_free, radar, as_user, fake_llm):
    _, bob = pro_and_free
    as_user(bob).post("/api/chat", json={"message": "Bonjour"})
    assert "[réservé au Pro]" in fake_llm.calls[0]["system"]


def test_pro_user_sees_everything(pro_and_free, radar, as_user, fake_llm):
    alice, _ = pro_and_free
    assess(as_user, alice, fake_llm)
    client = as_user(alice)
    text = "".join(client.get(u).text for u in ["/api/jobs", "/api/companies", f"/api/jobs/{radar['fresh'].id}"])
    assert leaks(text) == SECRETS
    assert client.get("/api/me").json()["billing"]["plan"] == "pro"


def test_billing_disabled_masks_nothing(bob, radar, as_user):
    assert "Ultra Secret Staff Engineer" in as_user(bob).get("/api/jobs").text


# --- Quotas -------------------------------------------------------------------------------------


def test_chat_quota_blocks_eleventh_message_without_llm_call(pro_and_free, as_user, fake_llm):
    _, bob = pro_and_free
    client = as_user(bob)
    for i in range(10):
        assert client.post("/api/chat", json={"message": f"Message {i}"}).status_code == 200
    r = client.post("/api/chat", json={"message": "Encore un"})
    assert r.status_code == 402
    detail = r.json()["detail"]
    assert detail["code"] == "quota" and detail["kind"] == "chat" and (detail["used"], detail["limit"]) == (10, 10)
    assert detail["message"].startswith("Limite Free atteinte (10/10")
    assert len(fake_llm.calls) == 10  # aucun appel à Gemini pour le 11e


def test_import_quota_and_pro_only_profile_tools(pro_and_free, as_user, fake_llm):
    _, bob = pro_and_free
    client = as_user(bob)
    text = {"text": "Je suis ingénieur plateforme, 5 ans de Python et de Kubernetes à Paris."}
    first = client.post("/api/profile/import/text", json=text)
    assert first.status_code == 200, first.text
    client.delete(f"/api/profile/import/{first.json()['import_id']}")  # annuler ne rend pas l'import
    r = client.post("/api/profile/import/text", json=text)
    assert r.status_code == 402 and r.json()["detail"]["kind"] == "imports"
    r = client.post("/api/profile/import/pdf", files={"file": ("cv.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 402
    assert client.post("/api/profile/analyze").json()["detail"]["code"] == "pro_required"
    assert client.post("/api/profile/rules/synthesize").json()["detail"]["code"] == "pro_required"


def test_active_processes_quota_terminal_status_always_allowed(db, pro_and_free, as_user):
    _, bob = pro_and_free
    company = Company(name="Many", slug="many")
    jobs = [Job(company=company, title=f"Poste {i}", published_on=OLD) for i in range(12)]
    db.add_all(jobs)
    db.commit()
    client = as_user(bob)
    for job in jobs[:10]:
        assert client.put(f"/api/jobs/{job.id}/status", json={"status": "postule"}).status_code == 200
    r = client.put(f"/api/jobs/{jobs[10].id}/status", json={"status": "a_postuler"})
    assert r.status_code == 402 and r.json()["detail"]["kind"] == "processes"
    r = client.post("/api/applications", json={"job_id": jobs[11].id, "status": "postule"})
    assert r.status_code == 402
    # Changer d'étape ou passer à un statut terminal reste possible à la limite
    app_id = client.get(f"/api/jobs/{jobs[0].id}").json()["application_id"]
    assert client.patch(f"/api/applications/{app_id}", json={"status": "entretien"}).status_code == 200
    assert client.put(f"/api/jobs/{jobs[1].id}/status", json={"status": "refuse"}).status_code == 200
    assert client.patch(f"/api/applications/{app_id}", json={"status": "abandonne"}).status_code == 200
    # Deux places libérées
    assert client.put(f"/api/jobs/{jobs[10].id}/status", json={"status": "a_postuler"}).status_code == 200


def test_background_assessment_serves_pro_first_and_caps_free(db, pro_and_free, as_user, fake_llm):
    alice, bob = pro_and_free
    company = Company(name="Big", slug="big")
    db.add_all([Job(company=company, title=f"Poste {i}", published_on=OLD) for i in range(30)])
    db.add(Job(company=company, title="Toute récente", published_on=TODAY))
    db.commit()
    for user in (alice, bob):
        as_user(user).patch("/api/profile", json=PROFILE)
    assessor = Assessor(SessionLocal, lambda: fake_llm, per_cycle=10)

    def evaluated(user):
        with SessionLocal() as s:
            return s.scalar(select(func.count(JobAssessment.id)).where(JobAssessment.user_id == user.id))

    # Tant que le Pro a des offres à évaluer, le Free attend
    for _ in range(4):
        assessor.run_once()
    assert evaluated(alice) == 31 and evaluated(bob) == 0
    # Puis le Free est servi, dans la limite de 20 par semaine et sans les offres récentes
    while assessor.run_once():
        pass
    assert evaluated(alice) == 31 and evaluated(bob) == 20
    assert all("Nom : Bob" not in c["context"] for c in fake_llm.eval_calls[:7])

    r = as_user(bob).post("/api/assessments/run")
    assert r.status_code == 402 and r.json()["detail"]["kind"] == "assessments"
    status = as_user(bob).get("/api/assessments/status").json()
    assert status["weekly_left"] == 0


def test_pro_quotas_are_higher(pro_and_free, as_user, fake_llm):
    alice, _ = pro_and_free
    client = as_user(alice)
    for i in range(11):
        assert client.post("/api/chat", json={"message": f"Message {i}"}).status_code == 200
    me = client.get("/api/me").json()["billing"]
    assert me["usage"]["chat"] == 11 and me["limits"]["chat_per_month"] == 300


def test_quota_counts_only_this_month(db, pro_and_free, as_user, fake_llm):
    _, bob = pro_and_free
    client = as_user(bob)
    for i in range(10):
        client.post("/api/chat", json={"message": f"Message {i}"})
    from app.models import ChatMessage
    last_month = datetime.now(UTC).replace(day=1) - timedelta(days=2)
    with SessionLocal() as s:
        for m in s.scalars(select(ChatMessage)):
            m.created_at = last_month
        s.commit()
    assert client.post("/api/chat", json={"message": "Nouveau mois"}).status_code == 200
