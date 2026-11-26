"""Santé du moteur : offres par jour, bascule de modèle, file, retours, passages, alertes."""

from datetime import UTC, datetime, timedelta

from app import jobs
from app.engine_health import engine_health
from app.models import Company, Job, JobAssessment, JobFeedback, SearchRun, Task

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def _job(db, company, title, created, **fields):
    job = Job(company=company, title=title, created_at=created, **fields)
    db.add(job)
    db.flush()
    return job


def _assess(db, user, job, model, at):
    db.add(JobAssessment(user_id=user.id, job_id=job.id, criteria={}, skills_have=[], skills_missing=[],
                         verdict="nouveau", profile_version=1, model=model, assessed_at=at))


def test_engine_health_counts_and_alerts(db, alice):
    acme = Company(name="Acme", slug="acme")
    db.add(acme)
    old = _job(db, acme, "Ancienne", NOW - timedelta(days=30), embedded_at=NOW)
    j1 = _job(db, acme, "Platform", NOW - timedelta(days=5, hours=2), embedded_at=NOW)
    j2 = _job(db, acme, "Backend", NOW - timedelta(days=5), closed_at=NOW - timedelta(days=1), embedded_at=NOW)
    _job(db, acme, "Sans embedding", NOW - timedelta(days=4))  # embedding en attente depuis 4 jours
    _assess(db, alice, j1, "gemini-3.5-flash-lite", NOW - timedelta(days=5))  # 2 h après l'arrivée
    _assess(db, alice, j2, "gemini-3.1-flash-lite", NOW - timedelta(days=4))  # secours, 24 h après
    _assess(db, alice, old, "regles", NOW - timedelta(days=2))
    db.add_all([JobFeedback(user_id=alice.id, job_id=j1.id, sentiment="like", created_at=NOW - timedelta(days=1)),
                JobFeedback(user_id=alice.id, job_id=j2.id, sentiment="dislike", reasons=["trop_front"],
                            created_at=NOW - timedelta(days=1))])
    db.add(Task(kind="assess_user", payload={}, status="queued", run_after=NOW - timedelta(hours=3)))
    db.add(Task(kind="collect", payload={}, status="failed", run_after=NOW, updated_at=NOW - timedelta(hours=2)))
    db.add(SearchRun(kind="collecte", status="termine", started_at=NOW - timedelta(days=3), details={}))
    db.add(SearchRun(kind="recherche", status="termine", started_at=NOW - timedelta(days=1), jobs_added=4,
                     companies_checked=7, details={}))
    db.commit()

    h = engine_health(db, days=14, now=NOW, primary_model="gemini-3.5-flash-lite")
    assert len(h["jours"]) == 14 and h["jours"][-1] == "2026-11-25"
    assert sum(h["offres"]["ajoutees"]) == 3 and sum(h["offres"]["fermees"]) == 1
    ev = h["evaluations"]
    assert ev["total"] == 3 and ev["par_modele"]["regles"] == 1
    assert ev["bascule"] == 1 and ev["part_bascule"] == 0.5  # les exclusions par règles ne comptent pas
    assert ev["delai_median_h"] == 13.0  # médiane de 2 h et 24 h
    assert h["file"]["en_attente"] == 1 and h["file"]["plus_ancienne_h"] == 3.0 and h["file"]["echecs_24h"] == 1
    assert h["file"]["embeddings_en_attente"] == 1
    assert h["retours"] == {"jaime": 1, "pas_interesse": 1, "part_jaime": 0.5, "raisons": {"trop_front": 1}}
    assert h["passages"]["recherche"]["offres_ajoutees"] == 4 and h["passages"]["offres"] is None

    alerts = " | ".join(h["alertes"])
    assert "Aucune collecte terminée depuis plus de 36 h" in alerts
    assert "Aucune découverte d'entreprises" in alerts  # agent arrêté (PC éteint, job en échec)
    assert "File bloquée" in alerts and "1 tâche(s) en échec" in alerts
    assert "sans embedding depuis plus de 24 h" in alerts
    assert "Aucune offre ajoutée depuis 3 jours" in alerts
    assert "modèle de secours" not in alerts  # échantillon trop petit (2 évaluations) pour alerter


def test_frequent_fallback_alert(db, alice):
    acme = Company(name="Acme", slug="acme")
    db.add(acme)
    for i in range(20):
        job = _job(db, acme, f"Offre {i}", NOW - timedelta(hours=5), embedded_at=NOW)
        _assess(db, alice, job, "gemini-3.1-flash-lite" if i < 8 else "gemini-3.5-flash-lite", NOW)
    db.add(SearchRun(kind="collecte", status="termine", started_at=NOW - timedelta(hours=5), details={}))
    db.add(SearchRun(kind="decouverte", status="termine", started_at=NOW - timedelta(days=2), details={}))
    db.commit()
    h = engine_health(db, now=NOW, primary_model="gemini-3.5-flash-lite")
    assert h["evaluations"]["part_bascule"] == 0.4
    assert h["alertes"] == ["40% des évaluations sur un modèle de secours (quota ou surcharge de gemini-3.5-flash-lite)"]


def test_endpoint_is_admin_only(db, alice, as_user, monkeypatch):
    assert as_user(alice).get("/api/admin/engine-health").status_code == 200  # sans facturation : tout le monde admin
    monkeypatch.setenv("BILLING_ENABLED", "true")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    assert as_user(alice).get("/api/admin/engine-health").status_code == 403


def test_daily_reports_alerts(db, fake_llm):
    from tests.test_collector import boards

    summary = jobs.daily(db, llm=fake_llm, fetch=boards({}))
    assert any("découverte" in a for a in summary["alerts"])  # aucune découverte en base


def test_model_that_answered_is_recorded():
    """Le client Gemini retient le modèle qui a VRAIMENT répondu (secours compris)."""
    from app.llm import GeminiClient

    client = GeminiClient.__new__(GeminiClient)
    client.models, client.quota_until, client.last_model = ["nominal", "secours"], {}, None

    class Overloaded(Exception):
        code = 503

    class FakeModels:
        def generate_content(self, model, contents, config):
            if model == "nominal":
                raise Overloaded()
            return "réponse"

    client.errors = type("E", (), {"APIError": Overloaded})
    client.client = type("C", (), {"models": FakeModels()})()
    assert client._generate({}, "texte", None) == "réponse"
    assert client.last_model == "secours"
