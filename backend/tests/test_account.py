"""Droits RGPD : export des données, suppression du compte, purge des comptes inactifs."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app import account
from app.db import SessionLocal
from app.models import (
    Application,
    ChatConversation,
    ChatMessage,
    Company,
    Job,
    JobFeedback,
    Subscription,
    User,
    UserProfile,
)

PROFILE = {"years_experience": 5, "skills": [{"name": "Python"}], "positioning": "Ingénieur plateforme"}


def fill(db, as_user, user):
    client = as_user(user)
    client.patch("/api/profile", json=PROFILE)
    job = Job(company=Company(name=f"Acme {user.id}", slug=f"acme-{user.id}"), title="Backend Engineer")
    db.add(job)
    db.commit()
    client.put(f"/api/jobs/{job.id}/status", json={"status": "postule"})
    client.post(f"/api/jobs/{job.id}/feedback", json={"sentiment": "like"})
    conv = ChatConversation(user_id=user.id)
    db.add(conv)
    db.flush()
    db.add(ChatMessage(conversation_id=conv.id, role="user", content="Mon CV : confidentiel"))
    db.commit()
    return job


def count(model, user_id):
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))


def test_export_contains_all_personal_data(db, alice, as_user):
    fill(db, as_user, alice)
    r = as_user(alice).get("/api/me/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["user"]["id"] == alice.id
    assert data["profile"][0]["positioning"] == "Ingénieur plateforme"
    assert data["applications"][0]["status"] == "postule" and data["application_events"]
    assert data["job_feedback"][0]["sentiment"] == "like"
    assert data["chat_messages"][0]["content"] == "Mon CV : confidentiel"


def test_delete_account_removes_everything_but_shared_radar(db, alice, bob, as_user):
    job = fill(db, as_user, alice)
    fill(db, as_user, bob)
    assert as_user(alice).delete("/api/me").status_code == 204
    with SessionLocal() as s:
        assert s.get(User, alice.id) is None
        assert s.get(Job, job.id) is not None  # radar partagé : conservé
    for model in (Application, UserProfile, JobFeedback, ChatConversation):
        assert count(model, alice.id) == 0
        assert count(model, bob.id) > 0  # les autres utilisateurs ne sont pas touchés


def test_delete_refused_while_subscription_renews(db, alice, as_user):
    db.add(Subscription(user_id=alice.id, kind="subscription", status="active",
                        current_period_end=datetime.now(UTC) + timedelta(days=20)))
    db.commit()
    r = as_user(alice).delete("/api/me")
    assert r.status_code == 409 and "Résiliez" in r.json()["detail"]
    sub = db.scalar(select(Subscription))
    sub.cancel_at_period_end = True  # résilié à la fin de la période : suppression possible
    db.commit()
    assert as_user(alice).delete("/api/me").status_code == 204


def test_purge_inactive_accounts(db, alice, bob):
    alice.last_login_at = datetime.now(UTC) - timedelta(days=365 * 3 + 5)
    bob.last_login_at = datetime.now(UTC) - timedelta(days=30)
    db.commit()
    assert account.purge_inactive(db) == 1
    db.expire_all()
    assert db.get(User, alice.id) is None and db.get(User, bob.id) is not None
