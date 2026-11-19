"""Backend Mire : connexion LinkedIn (OpenID Connect) + session par cookie + API (/api, voir api.py).

nginx appelle /auth/check (auth_request) avant de servir chaque fichier du site :
200 = connecté, 401 = nginx redirige vers /auth/login.
"""

import logging
import os
import secrets
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from . import assessments
from .api import router as api_router
from .assessments_api import router as assessments_router
from .feedback import router as feedback_router
from .radar_api import router as radar_router
from .llm import get_llm
from .chat import router as chat_router
from .profile_api import router as profile_router
from .db import SessionLocal
from .net import TLS_CONTEXT
from .seed import seed_catalog
from .users import normalize_locale, upsert_user

LINKEDIN_AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
SCOPES = "openid profile email"

CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8080").rstrip("/")
REDIRECT_URI = f"{PUBLIC_URL}/auth/callback"
SESSION_SECRET = os.environ["SESSION_SECRET"]

# Journal applicatif (actions exécutées par l'assistant, erreurs Gemini…) : logger "mire", niveau INFO
_mire_log = logging.getLogger("mire")
if not _mire_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    _mire_log.addHandler(_handler)
    _mire_log.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Le schéma est créé par les migrations Alembic (lancées avant uvicorn, voir docker-compose.yml)
    with SessionLocal() as db:
        seed_catalog(db)
    # Évaluation automatique des offres en tâche de fond (seulement si Gemini est configuré)
    if get_llm() is not None and os.environ.get("ASSESSMENT_WORKER", "true").lower() not in ("0", "false", "no"):
        assessments.assessor = assessments.Assessor(
            SessionLocal, get_llm, interval=float(os.environ.get("ASSESSMENT_INTERVAL_S", "120")),
        )
        assessments.assessor.start()
    yield
    if assessments.assessor is not None:
        assessments.assessor.stop()


app = FastAPI(title="Mire", lifespan=lifespan)
app.include_router(api_router)
app.include_router(profile_router)
app.include_router(chat_router)
app.include_router(assessments_router)
app.include_router(feedback_router)
app.include_router(radar_router)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="jobradar_session",
    max_age=7 * 24 * 3600,
    same_site="lax",
    https_only=PUBLIC_URL.startswith("https://"),
)


def _save_user(info: dict) -> int:
    with SessionLocal() as db:
        return upsert_user(db, info).id


def _safe_next(value: str | None) -> str:
    """N'accepte que des chemins internes (évite les redirections ouvertes)."""
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


@app.get("/auth/check")
def check(request: Request) -> Response:
    if request.session.get("user"):
        return Response(status_code=200)
    return Response(status_code=401)


@app.get("/auth/login")
def login(request: Request, next: str | None = None) -> RedirectResponse:
    if not CLIENT_ID or not CLIENT_SECRET:
        raise HTTPException(500, "LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET manquants dans .env")
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    request.session["next"] = _safe_next(next)
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "scope": SCOPES,
    }
    return RedirectResponse(f"{LINKEDIN_AUTHORIZE_URL}?{urlencode(params)}")


@app.get("/auth/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    if error:
        raise HTTPException(401, f"LinkedIn a refusé la connexion : {error} {error_description or ''}".strip())
    expected_state = request.session.pop("oauth_state", None)
    if not code or not state or not expected_state or not secrets.compare_digest(state, expected_state):
        raise HTTPException(400, "Paramètre state invalide, recommencez la connexion.")

    async with httpx.AsyncClient(verify=TLS_CONTEXT, timeout=10) as client:
        token_resp = await client.post(
            LINKEDIN_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(502, f"Échange du code LinkedIn échoué : {token_resp.text}")
        access_token = token_resp.json()["access_token"]

        # Le token vient d'un échange direct en TLS avec LinkedIn : on peut lire le profil
        # via l'endpoint userinfo OIDC plutôt que de vérifier la signature de l'id_token.
        userinfo_resp = await client.get(
            LINKEDIN_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(502, f"Lecture du profil LinkedIn échouée : {userinfo_resp.text}")
        info = userinfo_resp.json()

    request.session["user"] = {
        "sub": info.get("sub"),
        "name": info.get("name"),
        "given_name": info.get("given_name"),
        "family_name": info.get("family_name"),
        "email": info.get("email"),
        "picture": info.get("picture"),
        "locale": normalize_locale(info.get("locale")),
    }
    if not info.get("sub"):
        raise HTTPException(502, "Profil LinkedIn sans identifiant (sub)")
    # Crée / met à jour la ligne de la table users (sync : exécuté hors de la boucle d'événements)
    user_id = await run_in_threadpool(_save_user, request.session["user"])
    request.session["user_id"] = user_id
    return RedirectResponse(request.session.pop("next", "/"), status_code=303)


@app.get("/auth/me")
def me(request: Request) -> JSONResponse:
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "Non connecté")
    return JSONResponse(user)


@app.get("/auth/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/auth/logged-out", status_code=303)


@app.get("/auth/logged-out")
def logged_out() -> Response:
    html = (
        "<!doctype html><meta charset=utf-8><title>Déconnecté</title>"
        "<body style='font-family:Inter,sans-serif;padding:40px'>"
        "<p>Vous êtes déconnecté.</p><p><a href='/auth/login'>Se reconnecter avec LinkedIn</a></p>"
    )
    return Response(html, media_type="text/html")
