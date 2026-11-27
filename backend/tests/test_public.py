"""API publique de la landing : sans session, et sans rien de réservé au Pro."""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.entitlements import PLAN_LIMITS, price_labels
from app.main import app
from app.models import Company, Job


def seed(db):
    old = Company(name="Ancienne Corp", slug="ancienne", sector="SaaS", investors="Fonds Secret",
                  funding_amount_meur=12.5, logo_data=b"\x89PNG", logo_content_type="image/png")
    fresh = Company(name="Toute Neuve", slug="neuve", sector="IA")
    db.add_all([
        old, fresh,
        Job(company=old, title="Backend Engineer", published_on=date.today() - timedelta(days=30)),
        Job(company=fresh, title="Founding Engineer Confidentiel", published_on=date.today()),
    ])
    db.commit()
    return old, fresh


def test_landing_is_public_and_hides_pro_data(db, monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "true")
    old, fresh = seed(db)
    client = TestClient(app)  # aucune session, aucun utilisateur
    r = client.get("/api/public/landing")
    assert r.status_code == 200
    body = r.text
    data = r.json()
    assert [c["name"] for c in data["companies"]] == ["Ancienne Corp"]
    for secret in ("Toute Neuve", "Founding Engineer Confidentiel", "Fonds Secret", "12.5"):
        assert secret not in body
    assert data["prices"] == price_labels() and data["prices"]["pass_3m"] == "25 € TTC pour 3 mois"
    assert data["limits"]["free"]["chat_per_month"] == PLAN_LIMITS["free"]["chat_per_month"]
    assert data["stats"] == {"companies": 2, "jobs_open": 2, "jobs_week": 2}

    assert client.get(f"/api/public/logos/{old.id}").status_code == 200
    assert client.get(f"/api/public/logos/{fresh.id}").status_code == 404  # entreprise réservée au Pro
    assert client.get("/api/public/logos/9999").status_code == 404
