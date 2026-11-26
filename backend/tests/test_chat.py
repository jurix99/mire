"""Chat : outils (Gemini simulé), confirmation obligatoire avant toute écriture, isolation entre utilisateurs."""

from datetime import date

from sqlalchemy import select

from app import chat as chat_module
from app.chat_tools import TOOLS
from app.models import Application, ApplicationEvent, ChatAction, Company, Job


def say(client, text):
    r = client.post("/api/chat", json={"message": text})
    assert r.status_code == 200, r.text
    return r.json()["message"]


def test_status_and_chat_without_gemini_key(alice, as_user):
    client = as_user(alice)
    assert client.get("/api/chat/status").json()["enabled"] is False
    r = client.post("/api/chat", json={"message": "Bonjour"})
    assert r.status_code == 503


def test_status_enabled(alice, as_user, fake_llm):
    assert as_user(alice).get("/api/chat/status").json() == {"enabled": True, "model": "fake-gemini", "max_message_chars": 4000}


def test_no_delete_tool():
    names = {t.name for t in TOOLS}
    assert not any("delete" in n or "remove_job" in n for n in names)
    assert names == {"get_profile", "list_jobs", "get_job", "list_applications", "get_dashboard", "get_application_draft",
                     "update_profile", "set_job_status", "update_application", "create_job", "rate_job",
                     "ask_question"}


def test_read_tools_run_directly(alice, catalog, as_user, fake_llm):
    fake_llm.script = [([("list_jobs", {"min_score": 7}), ("get_dashboard", {})], "Voici vos postes.")]
    client = as_user(alice)
    reply = say(client, "Quels postes ont une bonne note ?")
    assert reply["content"] == "Voici vos postes." and reply["actions"] == []

    history = client.get("/api/chat/history").json()["messages"]
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "Quels postes ont une bonne note ?"), ("assistant", "Voici vos postes."),
    ]


def test_tool_results(db, alice, catalog, as_user, fake_llm):
    from app.chat_tools import ToolRunner

    runner = ToolRunner(db, alice)
    jobs = runner("list_jobs", {"min_score": 7})["data"]["jobs"]
    assert [j["title"] for j in jobs] == ["Backend Engineer", "Data Engineer"]
    assert runner("list_jobs", {"company": "glob"})["data"]["total"] == 1
    assert runner("get_job", {"job_id": 9999}) == {"error": "Poste introuvable"}
    assert "error" in runner("get_job", {})
    assert "error" in runner("drop_database", {})


def test_system_instruction_contains_profile_and_date(alice, as_user, fake_llm):
    client = as_user(alice)
    client.patch("/api/profile", json={"skills": [{"name": "Python"}],
                                       "rules": [{"kind": "eviter", "text": "TypeScript au cœur du poste"}]})
    say(client, "Salut")
    system = fake_llm.calls[0]["system"]
    assert date.today().isoformat() in system
    assert "Compétences fortes : Python" in system
    assert "Éviter : TypeScript au cœur du poste" in system
    assert "DONNÉES" in system


def test_set_job_status_requires_confirmation(db, alice, catalog, as_user, fake_llm):
    job = catalog["jobs"][0]
    fake_llm.script = [([("set_job_status", {"job_id": job.id, "status": "postule"})], "Je vous propose de passer ce poste en Postulé.")]
    client = as_user(alice)
    reply = say(client, "J'ai postulé chez Acme pour le poste backend")

    [action] = reply["actions"]
    assert action["status"] == "pending"
    assert action["changes"][0] == {"label": "Statut", "before": "Nouveau", "after": "Postulé"}
    # Rien n'est modifié avant confirmation
    assert db.scalar(select(Application)) is None

    r = client.post(f"/api/chat/actions/{action['id']}/confirm")
    assert r.status_code == 200, r.text
    assert r.json()["action"]["status"] == "confirmed"
    assert "applications" in r.json()["refresh"]

    db.expire_all()
    app = db.scalar(select(Application))
    assert app.status == "postule" and app.applied_on == date.today()
    events = db.scalars(select(ApplicationEvent)).all()
    assert [(e.from_status, e.to_status) for e in events] == [("nouveau", "postule")]

    # Une action ne s'exécute qu'une fois
    assert client.post(f"/api/chat/actions/{action['id']}/confirm").status_code == 409
    # L'historique montre l'action confirmée, et le modèle le saura au tour suivant
    history = client.get("/api/chat/history").json()["messages"]
    assert history[-1]["actions"][0]["status"] == "confirmed"
    say(client, "Merci")
    assert "confirmée et exécutée" in fake_llm.calls[-1]["history"][-2]["text"]


def test_update_profile_add_skill_then_confirm(alice, as_user, fake_llm):
    fake_llm.script = [([("update_profile", {"add_skills": [{"name": "Kubernetes", "kind": "a_developper"}],
                                              "add_rules": [{"kind": "preferer", "text": "Équipes produit"}]})],
                         "Je vous propose d'ajouter Kubernetes.")]
    client = as_user(alice)
    action = say(client, "Ajoute Kubernetes à mes compétences à développer")["actions"][0]
    assert [c["label"] for c in action["changes"]] == ["Compétence ajoutée", "Règle ajoutée (préférer)"]
    assert client.get("/api/profile").json()["skills"] == []

    client.post(f"/api/chat/actions/{action['id']}/confirm")
    profile = client.get("/api/profile").json()
    assert profile["skills"] == [{"name": "Kubernetes", "level": None, "kind": "a_developper"}]
    [rule] = profile["rules"]
    assert (rule["kind"], rule["text"], rule["source"], rule["active"]) == ("preferer", "Équipes produit", "chat", True)
    assert rule["created_at"]


def test_update_profile_without_change_is_refused(alice, as_user, fake_llm):
    client = as_user(alice)
    client.patch("/api/profile", json={"skills": [{"name": "Python"}]})
    fake_llm.script = [([("update_profile", {"add_skills": [{"name": "python"}]})], "Déjà présent.")]
    reply = say(client, "Ajoute Python")
    assert reply["actions"] == []


def test_cancel_action(db, alice, catalog, as_user, fake_llm):
    fake_llm.script = [([("set_job_status", {"job_id": catalog["jobs"][1].id, "status": "abandonne"})], "Proposé.")]
    client = as_user(alice)
    action = say(client, "Abandonne le poste frontend")["actions"][0]
    r = client.post(f"/api/chat/actions/{action['id']}/cancel")
    assert r.json()["action"]["status"] == "cancelled"
    assert client.post(f"/api/chat/actions/{action['id']}/confirm").status_code == 409
    assert db.scalar(select(Application)) is None


def test_update_application(db, alice, catalog, as_user, fake_llm):
    client = as_user(alice)
    job = catalog["jobs"][0]
    app_id = client.put(f"/api/jobs/{job.id}/status", json={"status": "entretien"}).json()["application_id"]
    client.patch(f"/api/applications/{app_id}", json={"notes": "Premier échange."})
    fake_llm.script = [([("update_application", {"job_id": job.id, "next_step": "Entretien technique",
                                                  "next_step_date": "2026-12-02", "append_note": "Bon feeling"})], "Ok.")]
    action = say(client, "Prochaine étape : entretien technique le 2 décembre")["actions"][0]
    assert {c["label"] for c in action["changes"]} == {"Prochaine étape", "Date de la prochaine étape", "Note ajoutée"}
    client.post(f"/api/chat/actions/{action['id']}/confirm")
    app = client.get("/api/applications").json()[0]
    assert app["next_step"] == "Entretien technique" and app["next_step_date"] == "2026-12-02"
    assert app["notes"].startswith("Premier échange.\n") and app["notes"].endswith("Bon feeling")
    assert app["status"] == "entretien"


def test_create_job_with_new_company(db, alice, catalog, as_user, fake_llm):
    fake_llm.script = [([("create_job", {"company_name": "Initech", "title": "Platform Engineer",
                                          "url": "initech.io/jobs/1", "score": 8})], "Proposé.")]
    client = as_user(alice)
    action = say(client, "Ajoute le poste Platform Engineer chez Initech")["actions"][0]
    assert action["changes"][0]["after"] == "Initech (nouvelle entreprise)"
    assert db.scalar(select(Company).where(Company.name == "Initech")) is None

    client.post(f"/api/chat/actions/{action['id']}/confirm")
    db.expire_all()
    job = db.scalar(select(Job).where(Job.title == "Platform Engineer"))
    assert job.company.name == "Initech" and job.url == "https://initech.io/jobs/1"
    assert job.source == "Assistant Mire"


def test_create_job_rejects_dangerous_url(alice, catalog, as_user, fake_llm):
    fake_llm.script = [([("create_job", {"company_name": "Acme", "title": "X", "url": "javascript:alert(1)"})], "?")]
    assert say(as_user(alice), "Ajoute ce poste")["actions"] == []


# --- Isolation entre utilisateurs ----------------------------------------------


def test_users_cannot_touch_each_other(db, alice, bob, catalog, as_user, fake_llm):
    job = catalog["jobs"][0]
    alice_client = as_user(alice)
    app_id = alice_client.put(f"/api/jobs/{job.id}/status", json={"status": "entretien"}).json()["application_id"]
    fake_llm.script = [([("set_job_status", {"job_id": job.id, "status": "offre"})], "Proposé.")]
    alice_action = say(alice_client, "J'ai une offre !")["actions"][0]

    bob_client = as_user(bob)
    # Bob ne peut ni confirmer ni annuler l'action d'Alice
    assert bob_client.post(f"/api/chat/actions/{alice_action['id']}/confirm").status_code == 404
    assert bob_client.post(f"/api/chat/actions/{alice_action['id']}/cancel").status_code == 404
    # Ni voir son historique
    assert bob_client.get("/api/chat/history").json()["messages"] == []
    # Les outils de Bob ne voient pas les process d'Alice et ne peuvent pas les modifier
    fake_llm.script = [([("list_applications", {}), ("update_application", {"application_id": app_id, "notes": "pirate"})], "…")]
    reply = say(bob_client, "Montre mes process")
    assert reply["actions"] == []
    assert fake_llm.calls[-1]["history"] == [{"role": "user", "text": "Montre mes process"}]

    db.expire_all()
    app = db.get(Application, app_id)
    assert app.status == "entretien" and app.notes is None
    assert db.get(ChatAction, alice_action["id"]).status == "pending"


def test_bob_tools_see_his_own_statuses(db, alice, bob, catalog, as_user):
    from app.chat_tools import ToolRunner

    as_user(alice).put(f"/api/jobs/{catalog['jobs'][0].id}/status", json={"status": "entretien"})
    assert ToolRunner(db, bob)("list_applications", {})["data"]["applications"] == []
    jobs = ToolRunner(db, bob)("list_jobs", {"status": "entretien"})["data"]
    assert jobs["total"] == 0


# --- Limites -----------------------------------------------------------------


def test_message_size_limit(alice, as_user, fake_llm):
    assert as_user(alice).post("/api/chat", json={"message": "x" * 4001}).status_code == 422
    assert as_user(alice).post("/api/chat", json={"message": ""}).status_code == 422


def test_rate_limit(alice, as_user, fake_llm, monkeypatch):
    monkeypatch.setattr(chat_module, "RATE_LIMIT", 2)
    client = as_user(alice)
    say(client, "1")
    say(client, "2")
    r = client.post("/api/chat", json={"message": "3"})
    assert r.status_code == 429


def test_history_window(alice, as_user, fake_llm, monkeypatch):
    monkeypatch.setattr(chat_module, "HISTORY_WINDOW", 4)
    client = as_user(alice)
    for i in range(5):
        say(client, f"message {i}")
    sent = fake_llm.calls[-1]["history"]
    assert len(sent) <= 4
    assert sent[0]["role"] == "user"
    assert sent[-1] == {"role": "user", "text": "message 4"}


def test_reset_starts_new_conversation(alice, as_user, fake_llm):
    client = as_user(alice)
    say(client, "Bonjour")
    client.post("/api/chat/reset")
    assert client.get("/api/chat/history").json()["messages"] == []


def test_llm_failure_rolls_back_proposals(db, alice, catalog, as_user, fake_llm):
    from app.llm import LLMError

    def broken_chat(system, history, tools, execute):
        execute("set_job_status", {"job_id": catalog["jobs"][0].id, "status": "postule"})
        raise LLMError()

    fake_llm.chat = broken_chat
    r = as_user(alice).post("/api/chat", json={"message": "Postule"})
    assert r.status_code == 502
    assert db.scalar(select(ChatAction)) is None
