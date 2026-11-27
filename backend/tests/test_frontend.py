"""Front servi par FastAPI (production, sans nginx) et limites de taille du corps des requêtes."""

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.frontend import BodySizeLimit, mount_frontend

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


@pytest.fixture
def site():
    """Mini-application : routes /auth et /api factices + front du dépôt (frontend/)."""
    app = FastAPI()

    @app.get("/auth/test-login")
    def test_login(request: Request):
        request.session["user"] = {"sub": "x"}
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request):
        if not request.session.get("user"):
            raise HTTPException(401, "Non connecté")
        return {"ok": True}

    app.add_middleware(SessionMiddleware, secret_key="test")
    mount_frontend(app, FRONTEND, asset_version="abc123")
    return TestClient(app, follow_redirects=False)


def test_landing_is_public_with_versioned_assets(site):
    r = site.get("/")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert 'href="/assets/landing.css?v=abc123"' in r.text and "/assets/landing.css\"" not in r.text
    assert site.get("/index.html").status_code == 200


def test_app_pages_require_session(site):
    r = site.get("/entreprises")
    assert r.status_code == 302 and r.headers["location"] == "/?next=/entreprises"
    assert site.get("/tableau-de-bord.html").headers["location"] == "/?next=/tableau-de-bord.html"
    # Lien du récapitulatif par e-mail : les paramètres survivent à la connexion (encodés dans next)
    r = site.get("/postes?offre=12&retour=like")
    assert r.headers["location"] == "/?next=/postes%3Foffre%3D12%26retour%3Dlike"
    # Fichier inexistant : même règle que nginx (session d'abord), puis 404
    assert site.get("/nexiste-pas").status_code == 302
    site.get("/auth/test-login")
    r = site.get("/entreprises")
    assert r.status_code == 200 and "<html" in r.text.lower() and r.headers["cache-control"] == "no-cache"
    assert site.get("/entreprises.html").status_code == 200
    assert site.get("/nexiste-pas").status_code == 404
    assert site.get("/../backend/app/main.py").status_code in (302, 404)


def test_api_and_auth_are_not_redirected(site):
    r = site.get("/api/me")
    assert r.status_code == 401 and r.json()["detail"] == "Non connecté"
    r = site.get("/api/inconnue")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    assert site.get("/auth/inconnue").status_code == 404


def test_old_landing_url(site):
    r = site.get("/bienvenue?next=/postes")
    assert r.status_code == 301 and r.headers["location"] == "/?next=/postes"


def test_removed_pages_lead_to_the_jobs(site):
    site.get("/auth/test-login")
    for path in ("/tableau-de-bord", "/tableau-de-bord.html", "/analytics"):
        r = site.get(path)
        assert (r.status_code, r.headers["location"]) == (302, "/postes")
    assert site.get("/tableau-de-bord?pro=1").headers["location"] == "/postes?pro=1"


def test_assets_cache(site):
    versioned = site.get("/assets/app.css?v=abc123")
    assert versioned.status_code == 200 and versioned.headers["cache-control"] == "public, max-age=31536000, immutable"
    plain = site.get("/assets/js/app.js")  # importé par les modules des pages, sans version : revalidé
    assert plain.headers["cache-control"] == "no-cache" and "etag" in plain.headers
    assert site.get("/assets/js/app.js", headers={"if-none-match": plain.headers["etag"]}).status_code == 304
    assert site.get("/assets/nope.css").status_code == 404


def limited_app() -> TestClient:
    app = FastAPI()

    @app.post("/api/chat")
    async def chat(request: Request):
        return {"size": len(await request.body())}

    @app.post("/api/profile/import/pdf")
    async def upload(request: Request):
        return {"size": len(await request.body())}

    @app.post("/api/companies")
    async def other(request: Request):
        return {"size": len(await request.body())}

    app.add_middleware(BodySizeLimit)
    return TestClient(app)


def test_body_size_limits():
    client = limited_app()
    assert client.post("/api/chat", content=b"x" * 60_000).status_code == 200
    r = client.post("/api/chat", content=b"x" * 70_000)
    assert r.status_code == 413 and "Ko" in r.json()["detail"]
    assert client.post("/api/companies", content=b"x" * (3 * 1024 * 1024)).status_code == 413
    assert client.post("/api/profile/import/pdf", content=b"x" * (3 * 1024 * 1024)).json()["size"] == 3 * 1024 * 1024
    assert client.post("/api/profile/import/pdf", content=b"x" * (22 * 1024 * 1024)).status_code == 413


def test_body_size_limit_without_content_length():
    client = limited_app()

    def chunks():
        for _ in range(10):
            yield b"x" * 10_000

    r = client.post("/api/chat", content=chunks())  # transfert par morceaux : pas de Content-Length
    assert r.status_code == 413


def test_main_app_applies_limits(alice, as_user):
    r = as_user(alice).post("/api/chat", content=b"{" + b" " * 70_000 + b"}",
                            headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_legal_pages_are_public(site):
    for path in ("/mentions-legales", "/cgv", "/confidentialite", "/cgv.html"):
        r = site.get(path)
        assert r.status_code == 200, path
    assert "Droit de rétractation" in site.get("/cgv").text
    assert "Politique de confidentialité" in site.get("/confidentialite").text
