"""Bêta privée : inscription sur invitation ou liste d'e-mails ; pertinence mesurée par profil."""

from datetime import UTC, datetime

from app.engine_health import beta_report
from app.models import Application, Company, Job, JobAssessment, JobFeedback, ProcessStatus, User
from app.profile import ProfileData, get_profile, save_fields
from app.users import invite_matches, signup_allowed


def test_signup_open_without_beta_config(db, monkeypatch):
    monkeypatch.delenv("BETA_INVITE_CODE", raising=False)
    monkeypatch.delenv("BETA_EMAILS", raising=False)
    assert signup_allowed(db, {"sub": "nouveau", "email": "x@example.com"})


def test_private_beta_gate(db, alice, monkeypatch):
    monkeypatch.setenv("BETA_INVITE_CODE", "code-secret")
    monkeypatch.setenv("BETA_EMAILS", "Testeur@Example.com")
    assert not signup_allowed(db, {"sub": "inconnu", "email": "autre@example.com"})
    assert signup_allowed(db, {"sub": "inconnu", "email": "testeur@example.com"})  # sur la liste
    assert signup_allowed(db, {"sub": "inconnu"}, invited=True)  # lien d'invitation
    assert signup_allowed(db, {"sub": alice.linkedin_sub})  # compte existant : toujours accepté
    assert invite_matches("code-secret") and not invite_matches("faux") and not invite_matches(None)
    monkeypatch.setenv("BETA_INVITE_CODE", "")
    assert not invite_matches("")  # aucun code configuré : rien ne correspond


def _profile(db, user, roles):
    save_fields(get_profile(db, user), ProfileData.model_validate(
        {"skills": [{"name": "Python"}], "preferences": {"roles": roles},
         "scoring": {"definitions": {"stade": "Seed à Série A"}}}))


def test_beta_report_precision_per_profile(db, alice, bob):
    _profile(db, alice, ["Platform Engineer"])
    _profile(db, bob, ["Data Engineer"])
    db.add(User(linkedin_sub="vide", name="Vide"))  # profil vide : absent du rapport
    acme = Company(name="Acme", slug="acme")
    jobs = [Job(company=acme, title=f"Offre {i}") for i in range(6)]
    db.add_all(jobs)
    db.flush()
    now = datetime.now(UTC)
    for i, job in enumerate(jobs):  # notes décroissantes : 9, 8, 7, 6, 5, 4
        db.add(JobAssessment(user_id=alice.id, job_id=job.id, score=9 - i, verdict="nouveau" if i < 4 else "ecartee",
                             criteria={}, skills_have=[], skills_missing=[], profile_version=1, model="m",
                             assessed_at=now))
    # Alice a jugé : 1re aimée, 2e postulée, 3e pas intéressée, 5e aimée
    db.add(JobFeedback(user_id=alice.id, job_id=jobs[0].id, sentiment="like"))
    db.add(Application(user_id=alice.id, job_id=jobs[1].id, status=ProcessStatus.postule))
    db.add(JobFeedback(user_id=alice.id, job_id=jobs[2].id, sentiment="dislike"))
    db.add(JobFeedback(user_id=alice.id, job_id=jobs[4].id, sentiment="like"))
    db.commit()

    report = beta_report(db)
    rows = {r["user_id"]: r for r in report["utilisateurs"]}
    assert set(rows) == {alice.id, bob.id}
    a = rows[alice.id]
    assert a["profil"] == "Platform Engineer" and a["notees"] == 6 and a["retenues"] == 4
    assert (a["jugees"], a["positives"], a["jugees_top"]) == (4, 3, 4)
    assert a["precision_top"] == 0.75 and a["criteres_definis"] == 1
    assert "Alice" not in str(report)  # ni nom ni e-mail
    assert rows[bob.id]["precision_top"] is None  # rien de jugé : pas de mesure
    assert report["precision_mediane"] == 0.75 and report["mesures"] == 1 and report["sous_50"] == 0


def test_beta_endpoint_admin_only(alice, as_user, monkeypatch):
    assert as_user(alice).get("/api/admin/beta").status_code == 200
    monkeypatch.setenv("BILLING_ENABLED", "true")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    assert as_user(alice).get("/api/admin/beta").status_code == 403
