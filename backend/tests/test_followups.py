"""Relances guidées (followups.py) : quand relancer, texte proposé (Gemini ou modèle écrit), deux relances au plus,
puis « sans réponse ». Mire n'envoie rien."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import followups
from app.models import Application, ApplicationEvent, ApplicationGeneration, Company, Job, ProcessStatus

TODAY = date(2026, 11, 27)


def at(days_ago: int) -> datetime:
    return datetime(2026, 11, 27, 10, tzinfo=UTC) - timedelta(days=days_ago)


def event(to, days_ago, frm=None):
    return ApplicationEvent(from_status=frm, to_status=to, created_at=at(days_ago))


def process(status, applied_days_ago=None, events=(), **fields):
    applied = TODAY - timedelta(days=applied_days_ago) if applied_days_ago is not None else None
    return Application(status=status, applied_on=applied, events=list(events), created_at=at(40), **fields)


def test_when_to_follow_up():
    state = followups.follow_up_state
    assert state(process(ProcessStatus.a_postuler), TODAY) is None
    assert state(process(ProcessStatus.entretien, 30), TODAY) is None
    assert state(process(ProcessStatus.postule, 4), TODAY) == {
        "kind": "waiting", "due_on": "2026-12-03", "number": 1, "sent": 0}
    assert state(process(ProcessStatus.postule, 12), TODAY) == {"kind": "due", "number": 1, "days": 12, "sent": 0}
    # Sans date saisie : le passage à « Postulé » fait foi
    assert state(process(ProcessStatus.postule, None, [event(ProcessStatus.postule, 11)]), TODAY)["kind"] == "due"
    # Une étape prévue (l'entreprise a répondu) : rien à relancer
    assert state(process(ProcessStatus.postule, 20, next_step_date=TODAY + timedelta(days=2)), TODAY) is None
    # Après une relance, le compteur repart de sa date
    one = process(ProcessStatus.relance, 30, [event(ProcessStatus.relance, 3, ProcessStatus.postule)])
    assert state(one, TODAY) == {"kind": "waiting", "due_on": "2026-12-04", "number": 2, "sent": 1}
    two = process(ProcessStatus.relance, 40, [event(ProcessStatus.relance, 25, ProcessStatus.postule),
                                              event(ProcessStatus.relance, 12, ProcessStatus.relance)])
    assert state(two, TODAY) == {"kind": "no_answer", "days": 12, "sent": 2}


def test_generated_text_is_tidied():
    # Vu avec Gemini : salutation collée au texte, formule de politesse ajoutée malgré la consigne
    assert followups.tidy("Bonjour, Je reviens vers vous. Où en est le recrutement ? Bien cordialement.") == (
        "Bonjour,\n\nJe reviens vers vous. Où en est le recrutement ?")
    assert followups.tidy("Hello,\n\nAny news?\n\nBest regards,\nJulien\n", "Julien Terrier") == "Hello,\n\nAny news?"
    assert followups.tidy("Hello,\n\nI did my best.") == "Hello,\n\nI did my best."


@pytest.fixture
def sent_process(db, alice):
    acme = Company(name="Acme", slug="acme")
    job = Job(company=acme, title="Backend Engineer", location="Paris",
              summary="Nous construisons la plateforme de paie des PME et recrutons pour l'équipe API.")
    db.add_all([acme, job])
    db.flush()
    app = Application(user_id=alice.id, job_id=job.id, status=ProcessStatus.postule,
                      applied_on=date.today() - timedelta(days=12))
    app.events.append(ApplicationEvent(from_status=ProcessStatus.prete, to_status=ProcessStatus.postule,
                                       snapshot={"cover_letter": "J'ai mené la refonte de l'API de facturation.",
                                                 "answers": [], "cv_text": None}))
    db.add(app)
    db.commit()
    return app


def backdate_follow_ups(db, app_id, days):
    for e in db.scalars(select(ApplicationEvent).where(ApplicationEvent.application_id == app_id,
                                                       ApplicationEvent.to_status == ProcessStatus.relance)):
        e.created_at = datetime.now(UTC) - timedelta(days=days)
    db.commit()


def test_two_follow_ups_then_no_answer(db, alice, sent_process, as_user, fake_llm):
    client = as_user(alice)
    listed = {a["id"]: a for a in client.get("/api/applications").json()}
    assert listed[sent_process.id]["follow_up"] == {"kind": "due", "number": 1, "days": 12, "sent": 0}

    fake_llm.follow_up = {"subject": "Candidature Backend Engineer",
                          "body": "Bonjour,\n\nJ'ai envoyé ma candidature il y a 12 jours. J'ai réduit les coûts "
                                  "de 40 %. Où en est le recrutement ?"}
    r = client.post(f"/api/applications/{sent_process.id}/follow-up/draft")
    assert r.status_code == 200, r.text
    text = r.json()
    assert text["generated"] is True and text["number"] == 1 and text["language"] == "fr"
    # Ce qui a été envoyé est transmis à Gemini ; un nombre inventé (40 %) fait sauter sa phrase ; signature ajoutée
    assert "refonte de l'API de facturation" in fake_llm.follow_up_calls[0]
    assert "40" not in text["body"] and "il y a 12 jours" in text["body"]
    assert text["body"].endswith("Bien cordialement,\nAlice")
    assert db.scalar(select(func.count(ApplicationGeneration.id)).where(
        ApplicationGeneration.user_id == alice.id, ApplicationGeneration.kind == "regenerate")) == 1

    r = client.post(f"/api/applications/{sent_process.id}/follow-up/sent", json={"body": text["body"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "relance" and out["follow_up"]["kind"] == "waiting" and out["follow_up"]["sent"] == 1
    assert out["events"][-1]["note"] == "Relance 1 envoyée" and out["events"][-1]["has_snapshot"] is True

    backdate_follow_ups(db, sent_process.id, 11)
    assert client.post(f"/api/applications/{sent_process.id}/follow-up/sent").json()["follow_up"]["sent"] == 2
    r = client.post(f"/api/applications/{sent_process.id}/follow-up/sent")
    assert r.status_code == 409  # deux relances au plus

    backdate_follow_ups(db, sent_process.id, 10)
    listed = {a["id"]: a for a in client.get("/api/applications").json()}
    assert listed[sent_process.id]["follow_up"]["kind"] == "no_answer"
    out = client.post(f"/api/applications/{sent_process.id}/follow-up/no-answer").json()
    assert out["status"] == "abandonne" and out["follow_up"] is None
    assert out["events"][-1]["note"] == "Sans réponse après 2 relances"


def test_without_gemini_a_written_template(db, alice, sent_process, as_user, monkeypatch):
    from app.llm import get_llm
    from app.main import app

    app.dependency_overrides[get_llm] = lambda: None
    try:
        text = as_user(alice).post(f"/api/applications/{sent_process.id}/follow-up/draft").json()
    finally:
        app.dependency_overrides.pop(get_llm, None)
    assert text["generated"] is False
    assert text["subject"] == "Ma candidature au poste de Backend Engineer"
    sent_on = date.today() - timedelta(days=12)
    assert f"le {sent_on.day} {followups.MONTHS_FR[sent_on.month - 1]} ma candidature" in text["body"]
    assert "je me permets" not in text["body"].lower() and text["body"].endswith("Alice")


def test_free_plan_gets_the_template_without_gemini(db, alice, sent_process, as_user, fake_llm, monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "true")
    monkeypatch.delenv("PRO_USER_EMAILS", raising=False)
    text = as_user(alice).post(f"/api/applications/{sent_process.id}/follow-up/draft").json()
    assert text["generated"] is False and not getattr(fake_llm, "follow_up_calls", [])


def test_follow_up_is_private_and_only_for_sent_processes(db, alice, bob, sent_process, as_user):
    assert as_user(bob).post(f"/api/applications/{sent_process.id}/follow-up/draft").status_code == 404
    sent_process.status = ProcessStatus.a_postuler
    db.commit()
    assert as_user(alice).post(f"/api/applications/{sent_process.id}/follow-up/sent").status_code == 409
