"""Rôle admin : seuls les admins modifient le radar partagé (entreprises, postes, logos, sources, ATS, collecte)."""

from datetime import date, timedelta

import pytest

from app.chat_tools import ToolRunner
from app.db import SessionLocal
from app.models import Company, Job, Source, User


@pytest.fixture
def billing_on(monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "true")
    monkeypatch.setenv("ADMIN_EMAILS", " Admin@Example.com ")


@pytest.fixture
def admin(db):
    user = User(linkedin_sub="admin", name="Admin", email="admin@example.com")
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def source(db):
    s = Source(name="Ma source", kind="autre", jobs_found=0)
    db.add(s)
    db.commit()
    return s


def write_requests(catalog, source) -> list[tuple[str, str, dict | None]]:
    acme = catalog["acme"].id
    job = catalog["jobs"][0].id
    return [
        ("post", "/api/companies", {"json": {"name": "Nouvelle"}}),
        ("patch", f"/api/companies/{acme}", {"json": {"investors": "Faux fonds"}}),
        ("delete", f"/api/companies/{acme}", None),
        ("put", f"/api/companies/{acme}/logo", {"files": {"file": ("l.png", b"\x89PNG", "image/png")}}),
        ("delete", f"/api/companies/{acme}/logo", None),
        ("post", "/api/jobs", {"json": {"company_id": acme, "title": "Nouveau poste"}}),
        ("patch", f"/api/jobs/{job}", {"json": {"title": "Renommé"}}),
        ("delete", f"/api/jobs/{job}", None),
        ("post", f"/api/companies/{acme}/check", None),
        ("post", f"/api/companies/{acme}/postings/import", {"json": {"ats_job_ids": ["x"]}}),
        ("post", "/api/sources", {"json": {"name": "Autre", "kind": "autre"}}),
        ("patch", f"/api/sources/{source.id}", {"json": {"active": False}}),
        ("post", "/api/collect/run", None),
        ("get", "/api/sources", None),  # page Sources : réservée aux admins
        ("get", "/api/collect/status", None),
    ]


def test_non_admin_cannot_write_shared_radar(db, alice, catalog, source, as_user, billing_on):
    client = as_user(alice)
    for method, url, kwargs in write_requests(catalog, source):
        r = getattr(client, method)(url, **(kwargs or {}))
        assert r.status_code == 403, (method, url, r.status_code)
        assert r.json()["detail"]["code"] == "admin_required"
    with SessionLocal() as s:  # rien n'a changé
        assert s.get(Company, catalog["acme"].id).investors is None
        assert s.get(Job, catalog["jobs"][0].id).title == "Backend Engineer"
        assert s.get(Source, source.id).active is True
    assert client.get("/api/me").json()["user"]["is_admin"] is False


def test_admin_can_write_shared_radar(db, admin, catalog, as_user, billing_on):
    client = as_user(admin)
    assert client.get("/api/me").json()["user"]["is_admin"] is True
    r = client.post("/api/companies", json={"name": "Nouvelle"})
    assert r.status_code == 201
    r = client.patch(f"/api/jobs/{catalog['jobs'][0].id}", json={"title": "Renommé"})
    assert r.status_code == 200
    assert client.post("/api/sources", json={"name": "Autre", "kind": "autre"}).status_code == 201


def test_everyone_is_admin_when_billing_disabled(db, alice, catalog, as_user, monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "false")
    client = as_user(alice)
    assert client.get("/api/me").json()["user"]["is_admin"] is True
    assert client.post("/api/companies", json={"name": "Nouvelle"}).status_code == 201


def test_personal_writes_stay_open_to_non_admin(db, alice, catalog, as_user, billing_on):
    """Process, statuts et retours restent propres à chaque utilisateur : pas besoin d'être admin."""
    old = catalog["jobs"][0]
    with SessionLocal() as s:  # offre ancienne : visible en Free
        s.get(Job, old.id).published_on = date.today() - timedelta(days=30)
        s.commit()
    r = as_user(alice).put(f"/api/jobs/{old.id}/status", json={"status": "a_postuler"})
    assert r.status_code == 200


def test_chat_cannot_add_job_for_non_admin(db, alice, catalog, billing_on):
    with SessionLocal() as s:
        user = s.get(User, alice.id)
        out = ToolRunner(s, user)("create_job", {"company_name": "Acme", "title": "Poste inventé"})
    assert "administrateurs" in out["error"]
    with SessionLocal() as s:
        assert s.query(Job).filter(Job.title == "Poste inventé").count() == 0
