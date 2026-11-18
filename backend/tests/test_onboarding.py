"""Entretien d'onboarding dans le chat et outil rate_job (Gemini simulé)."""

from app.assessments import assess_user


def onboarding(client, text="Je veux compléter mon profil"):
    r = client.post("/api/chat", json={"message": text, "mode": "onboarding"})
    assert r.status_code == 200, r.text
    return r.json()["message"]


def test_onboarding_only_asks_missing_topics(alice, as_user, fake_llm):
    client = as_user(alice)
    client.patch("/api/profile", json={
        "skills": [{"name": "Python"}],
        "preferences": {"locations": ["Paris"], "min_salary_keur": 70, "clouds_preferred": ["Azure"]},
    })
    onboarding(client)
    system = fake_llm.calls[-1]["system"]
    assert "MODE ENTRETIEN" in system
    topics = system.split("MODE ENTRETIEN", 1)[1]
    # Sujets renseignés : jamais redemandés
    assert "lieux de travail" not in topics
    assert "salaire minimum" not in topics
    assert "clouds préférés ou à éviter" not in topics
    # Sujets manquants : listés
    assert "tolérance aux déplacements" in topics
    assert "stades d'entreprise" in topics
    assert "ask_question" in fake_llm.calls[-1]["tools"]


def test_normal_mode_has_no_interview(alice, as_user, fake_llm):
    as_user(alice).post("/api/chat", json={"message": "Bonjour"})
    assert "MODE ENTRETIEN" not in fake_llm.calls[-1]["system"]


def test_complete_profile_ends_interview(alice, as_user, fake_llm):
    client = as_user(alice)
    client.patch("/api/profile", json={
        "preferences": {"locations": ["Paris"], "work_modes": ["hybride"], "company_stages": ["Seed"],
                        "min_salary_keur": 70, "clouds_preferred": ["Azure"], "travel": "occasionnel",
                        "sectors": ["Santé"], "roles": ["Platform"]},
        "rules": [{"kind": "eviter", "text": "SQL au cœur"}],
        "reference_jobs": [{"company": "Remanence", "title": "MTS"}],
    })
    onboarding(client)
    assert "contient déjà l'essentiel" in fake_llm.calls[-1]["system"]


def test_quick_replies_and_answer_becomes_a_proposal(alice, as_user, fake_llm):
    client = as_user(alice)
    fake_llm.script = [
        ([("ask_question", {"question": "Quelle tolérance aux déplacements ?",
                            "quick_replies": ["Aucun", "Peu de déplacements", "Fréquents OK"]})],
         "Quelle est votre tolérance aux déplacements ?"),
        ([("update_profile", {"travel": "occasionnel", "preferences_add": {"company_stages": ["Seed", "Série A"]}})],
         "Je vous propose de noter : déplacements occasionnels."),
    ]
    first = onboarding(client)
    assert first["quick_replies"] == ["Aucun", "Peu de déplacements", "Fréquents OK"]
    assert first["actions"] == []

    second = onboarding(client, "Peu de déplacements")
    [action] = second["actions"]
    assert action["status"] == "pending"
    assert {c["label"] for c in action["changes"]} >= {"Déplacements acceptés"}
    assert client.get("/api/profile").json()["preferences"]["travel"] is None  # rien sans confirmation
    client.post(f"/api/chat/actions/{action['id']}/confirm")
    prefs = client.get("/api/profile").json()["preferences"]
    assert prefs["travel"] == "occasionnel" and prefs["company_stages"] == ["Seed", "Série A"]
    # Historique : les réponses rapides restent associées au message
    history = client.get("/api/chat/history").json()["messages"]
    assert history[1]["quick_replies"] == ["Aucun", "Peu de déplacements", "Fréquents OK"]


def test_rate_job_requires_confirmation_and_is_kept(db, alice, catalog, as_user, fake_llm):
    client = as_user(alice)
    client.patch("/api/profile", json={"skills": [{"name": "Python"}]})
    assess_user(db, alice, fake_llm)
    job = catalog["jobs"][1]
    # get_job explique la note (détail par critère)
    from app.chat_tools import ToolRunner
    detail = ToolRunner(db, alice)("get_job", {"job_id": job.id})["data"]["evaluation"]
    assert detail["note_calculee"] == 7.0 and len(detail["criteres"]) == 6

    fake_llm.script = [([("rate_job", {"job_id": job.id, "score": 8.6, "reason": "Stack idéale"})], "Proposé.")]
    action = client.post("/api/chat", json={"message": "Mets 8,5 à ce poste"}).json()["message"]["actions"][0]
    assert action["changes"][0]["after"] == "8.5"  # arrondie au demi-point
    assert client.get(f"/api/jobs/{job.id}").json()["score"] == 7.0
    client.post(f"/api/chat/actions/{action['id']}/confirm")
    assert client.get(f"/api/jobs/{job.id}").json()["score"] == 8.5
    # Une réévaluation ne l'écrase pas
    client.patch("/api/profile", json={"skills": [{"name": "Python"}, {"name": "Go"}]})
    assess_user(db, alice, fake_llm)
    assert client.get(f"/api/jobs/{job.id}").json()["score"] == 8.5


def test_update_profile_excludes_company(alice, as_user, fake_llm):
    client = as_user(alice)
    fake_llm.script = [([("update_profile", {"add_excluded_companies": [{"name": "Comand AI",
                                                                          "reason": "entretiens faits"}]})], "ok")]
    action = client.post("/api/chat", json={"message": "Ne me propose plus Comand AI"}).json()["message"]["actions"][0]
    client.post(f"/api/chat/actions/{action['id']}/confirm")
    assert client.get("/api/profile").json()["excluded_companies"][0]["name"] == "Comand AI"


def test_fallback_quick_replies_when_model_forgets(alice, as_user, fake_llm):
    from app.chat import fallback_quick_replies

    assert fallback_quick_replies("Noté.\nQuel mode de travail préférez-vous ?") == ["Sur site", "Hybride", "Remote"]
    assert fallback_quick_replies("Merci, c'est noté.") == []
    fake_llm.script = [([], "Je vous propose Paris.\n\nQuelle est votre tolérance aux déplacements ?")]
    message = onboarding(as_user(alice))
    assert message["quick_replies"] == ["Aucun", "Occasionnels", "Fréquents OK"]
    # En mode normal, pas de réponses rapides ajoutées
    fake_llm.script = [([], "Quel mode de travail préférez-vous ?")]
    assert as_user(alice).post("/api/chat", json={"message": "x"}).json()["message"]["quick_replies"] == []
