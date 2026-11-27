"""Récapitulatif des nouvelles offres par e-mail (digest.py)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import digest, entitlements, mailer
from app.models import Application, Company, Job, JobSkill, ProcessStatus, User

PROFILE = {"years_experience": 4, "skills": [{"name": "Python"}, {"name": "Kubernetes"}],
           "preferences": {"roles": ["Backend"], "locations": ["Paris"], "work_modes": ["hybride", "remote"]}}
NOW = datetime.now(UTC)


def add_job(db, company, title, family="backend", skills=("python", "kubernetes"), age_days=0, **fields):
    fields = {"location": "Paris", "work_mode": "hybride", "published_on": date.today(), **fields}
    job = Job(company=company, title=title, role_family=family, created_at=NOW - timedelta(days=age_days), **fields)
    db.add(job)
    db.flush()
    db.add_all([JobSkill(job_id=job.id, skill_id=s, required=True) for s in skills])
    db.commit()
    return job


@pytest.fixture
def radar(db):
    acme, globex, initech = (Company(name=n, slug=n.lower()) for n in ("Acme", "Globex", "Initech"))
    db.add_all([acme, globex, initech])
    db.commit()
    return {
        "backend": add_job(db, acme, "Backend Engineer", salary_min_keur=55, salary_max_keur=70,
                           verified_open_at=NOW - timedelta(hours=5)),
        "platform": add_job(db, globex, "Platform Engineer"),
        "old": add_job(db, initech, "Senior Backend Engineer", age_days=12),
        "closed": add_job(db, initech, "Backend Developer", closed_at=NOW),
        "front": add_job(db, globex, "Designer produit", family="design", skills=()),
    }


@pytest.fixture
def outbox():
    sent: list[mailer.Mail] = []
    return sent


def with_profile(db, as_user, user, email="alice@example.com"):
    assert as_user(user).patch("/api/profile", json=PROFILE).status_code == 200
    db.expire_all()
    user = db.get(User, user.id)
    user.email = email
    db.commit()
    return user


def run(db, outbox, at=None):
    db.expire_all()
    return digest.send_digests(db, at=at or NOW, sender=outbox.append)


def test_weekly_digest_lists_new_relevant_offers_only(db, alice, radar, as_user, outbox):
    user = with_profile(db, as_user, alice)
    assert run(db, outbox) == {"sent": 1, "empty": 0, "failed": 0}
    mail = outbox[0]
    assert mail.to == "alice@example.com"
    # Objet : la meilleure offre d'abord (ici à égalité : la plus récente), puis le nombre des autres
    assert mail.subject == "Platform Engineer chez Globex, et 1 autre offre"
    assert "2 nouvelles offres correspondent à votre recherche cette semaine. Voici les 2 qui collent le mieux :" in mail.text
    for text in (mail.text, mail.html):
        assert "Backend Engineer" in text and "Platform Engineer" in text
        assert "Senior Backend Engineer" not in text  # ajoutée il y a 12 jours : pas nouvelle
        assert "Backend Developer" not in text  # fermée
        assert "Designer produit" not in text  # hors du métier visé : pas assez pertinente
    assert "55-70 k€" in mail.text and "Vérifiée il y a 5 h sur le site de l'entreprise" in mail.text
    assert f"/postes?offre={radar['backend'].id}&retour=like" in mail.text
    assert "Pourquoi : Métier visé : Backend" in mail.text  # Pro : raisons du classement
    db.refresh(user)
    assert user.email_token and len(user.email_token) >= 40
    assert mail.headers["List-Unsubscribe"] == f"<http://localhost:8080/api/public/email/desabonnement?t={user.email_token}>"
    assert mail.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert entitlements.aware(user.digest_sent_at) == NOW

    # Le lendemain : pas encore le tour (hebdomadaire) ; une semaine plus tard, seulement les offres ajoutées depuis
    assert run(db, outbox, NOW + timedelta(days=1))["sent"] == 0
    later = add_job(db, db.get(Company, radar["backend"].company_id), "Backend Engineer II")
    later.created_at = NOW + timedelta(days=3)
    db.commit()
    assert run(db, outbox, NOW + timedelta(days=7))["sent"] == 1
    assert outbox[-1].subject == "Backend Engineer II chez Acme"
    assert "Platform Engineer" not in outbox[-1].text


def test_treated_and_disliked_offers_are_left_out(db, alice, radar, as_user, outbox):
    client = as_user(alice)
    with_profile(db, as_user, alice)
    assert client.post(f"/api/jobs/{radar['platform'].id}/feedback", json={"sentiment": "dislike"}).status_code == 200
    db.add(Application(user_id=alice.id, job_id=radar["backend"].id, status=ProcessStatus.postule))
    db.commit()
    assert run(db, outbox) == {"sent": 0, "empty": 1, "failed": 0}
    assert outbox == []
    assert db.get(User, alice.id).digest_sent_at is None  # rien envoyé : le prochain passage réessaie


def test_frequency_and_missing_email(db, alice, bob, radar, as_user, outbox):
    with_profile(db, as_user, alice)
    with_profile(db, as_user, bob, email=None)
    db.get(User, alice.id).digest_frequency = "off"
    db.commit()
    assert run(db, outbox)["sent"] == 0

    db.get(User, alice.id).digest_frequency = "daily"
    db.commit()
    assert run(db, outbox)["sent"] == 1 and [m.to for m in outbox] == ["alice@example.com"]
    # 22 h plus tard : c'est le tour (marge : le job ne part pas à heure fixe), mais rien de nouveau depuis
    assert run(db, outbox, NOW + timedelta(hours=22)) == {"sent": 0, "empty": 1, "failed": 0}


def test_daily_window_is_one_day(db, alice, radar, as_user, outbox):
    with_profile(db, as_user, alice)
    db.get(User, alice.id).digest_frequency = "daily"
    radar["platform"].created_at = NOW - timedelta(days=2)
    db.commit()
    run(db, outbox)
    assert "Backend Engineer" in outbox[0].text and "Platform Engineer" not in outbox[0].text


def test_free_digest_never_describes_a_blurred_offer(db, monkeypatch, alice, radar, as_user, outbox):
    monkeypatch.setenv("BILLING_ENABLED", "true")
    with_profile(db, as_user, alice)
    access = entitlements.access_for(db, db.get(User, alice.id))
    run(db, outbox)
    mail = outbox[0]
    relevant = [radar["backend"], radar["platform"]]
    locked = [j for j in relevant if access.job_is_locked(j)]
    assert locked, "le jeu de test doit contenir au moins une offre floutée"
    for job in locked:
        assert job.title not in mail.text and job.title not in mail.html and job.title not in mail.subject
        assert job.company.name not in mail.text and job.company.name not in mail.html
    assert "réservée" in mail.text and "/postes?pro=1" in mail.text
    assert "Pourquoi :" not in mail.text  # raisons du classement : Pro seulement


def test_one_failure_does_not_stop_the_others(db, alice, bob, radar, as_user):
    with_profile(db, as_user, alice)
    with_profile(db, as_user, bob, email="bob@example.com")
    sent = []

    def flaky(mail):
        if mail.to == "alice@example.com":
            raise OSError("SMTP indisponible")
        sent.append(mail)

    db.expire_all()
    assert digest.send_digests(db, at=NOW, sender=flaky) == {"sent": 1, "empty": 0, "failed": 1}
    assert [m.to for m in sent] == ["bob@example.com"]
    assert db.get(User, alice.id).digest_sent_at is None


def test_without_smtp_nothing_is_sent(db, monkeypatch, alice, radar, as_user):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with_profile(db, as_user, alice)
    assert digest.send_digests(db, at=NOW)["disabled"] is True


def test_settings_api(alice, as_user):
    client = as_user(alice)
    r = client.get("/api/me/digest")
    assert r.status_code == 200 and r.json() == {"frequency": "weekly", "email": None, "available": False}
    r = client.put("/api/me/digest", json={"frequency": "daily"})
    assert r.status_code == 200 and r.json()["frequency"] == "daily"
    assert client.put("/api/me/digest", json={"frequency": "hourly"}).status_code == 422
    assert client.get("/api/me/digest").json()["frequency"] == "daily"


def test_unsubscribe_link(db, alice, as_user):
    from fastapi.testclient import TestClient

    from app.main import app

    user = db.get(User, alice.id)
    token = digest.ensure_token(user)
    db.commit()
    anonymous = TestClient(app)  # sans session
    url = f"/api/public/email/desabonnement?t={token}"
    page = anonymous.get(url)
    assert page.status_code == 200 and "Ne plus le recevoir" in page.text
    db.expire_all()
    assert db.get(User, alice.id).digest_frequency == "weekly"  # un GET ne change rien
    # Désabonnement « en un clic » des messageries (RFC 8058)
    r = anonymous.post(url, content="List-Unsubscribe=One-Click",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and "Vous ne recevrez plus le récapitulatif" in r.text
    db.expire_all()
    assert db.get(User, alice.id).digest_frequency == "off"
    assert "Déjà désabonné" in anonymous.get(url).text
    assert anonymous.get("/api/public/email/desabonnement?t=inconnu-mais-assez-long-0000").status_code == 404
    assert anonymous.post("/api/public/email/desabonnement?t=").status_code == 404


def test_export_contains_digest_settings(db, alice, as_user):
    data = as_user(alice).get("/api/me/export").json()
    assert data["user"]["digest_frequency"] == "weekly"


def test_daily_sends_digests(db, monkeypatch, alice, radar, as_user):
    from app import ats, jobs

    def no_ats(kind, slug):
        raise ats.ATSError("introuvable")

    with_profile(db, as_user, alice)
    outbox = []
    monkeypatch.setattr(digest.mailer, "configured", lambda: True)
    monkeypatch.setattr(digest.mailer, "send", outbox.append)
    db.expire_all()
    summary = jobs.daily(db, llm=None, fetch=no_ats)
    assert summary["digest"]["sent"] == 1 and outbox[0].to == "alice@example.com"


def test_daily_survives_a_digest_crash(db, monkeypatch, alice):
    from app import ats, jobs

    def boom(*a, **k):
        raise RuntimeError("panne")

    def no_ats(kind, slug):
        raise ats.ATSError("introuvable")

    monkeypatch.setattr(digest, "send_digests", boom)
    assert "digest" not in jobs.daily(db, llm=None, fetch=no_ats)


def test_mail_message_is_multipart_with_headers():
    mail = mailer.Mail(to="a@example.com", subject="Sujet é", text="texte", html="<p>html</p>",
                       headers={"List-Unsubscribe": "<https://x/u>"})
    msg = mailer.build_message(mail)
    assert msg["To"] == "a@example.com" and msg["List-Unsubscribe"] == "<https://x/u>"
    assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "text/html"]
    assert msg["Reply-To"] is None  # sans SMTP_REPLY_TO


def test_written_style_signature_and_reply_to(db, alice, radar, as_user, outbox, monkeypatch):
    monkeypatch.setenv("SMTP_FROM", "Julien de Mire <recapitulatif@notifications.example.org>")
    monkeypatch.setenv("SMTP_REPLY_TO", "julien@example.org")
    with_profile(db, as_user, alice)
    run(db, outbox)
    mail = outbox[0]
    # Un lien par offre (le titre) et les deux retours ; ni cartes ni boutons
    assert mail.html.count(f"/postes?offre={radar['backend'].id}") == 3
    assert "Voir l'offre" not in mail.html and "border-radius" not in mail.html
    for text in (mail.text, mail.html):
        assert "Bonne recherche," in text and "Julien de Mire" in text
        assert "Répondez simplement à ce message" in text
    assert mailer.build_message(mail)["Reply-To"] == "julien@example.org"


def test_new_accounts_receive_the_weekly_digest_by_default(db, alice):
    assert db.scalar(select(User.digest_frequency).where(User.id == alice.id)) == "weekly"
