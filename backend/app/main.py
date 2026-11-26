"""Backend Mire : connexion LinkedIn (OpenID Connect) + session par cookie + API (/api, voir api.py).

En local (docker compose), nginx appelle /auth/check (auth_request) avant de servir chaque fichier du site :
200 = connecté, 401 = nginx redirige vers la landing.

Traitements de fond (évaluation Gemini, collecte, file de tâches) :
- RUN_BACKGROUND_THREADS=true (défaut, docker compose) : threads dans ce processus, comme avant ;
- RUN_BACKGROUND_THREADS=false (production) : aucun thread ; l'API ne fait que mettre des tâches en file
  (tasks.py), traitées par les jobs Container Apps (`python -m app.jobs daily|worker`).
"""

import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from . import assessments, collector, logs, tasks
from .frontend import BodySizeLimit, mount_frontend
from .account import router as account_router
from .agent_api import router as agent_router
from .analytics_api import router as analytics_router
from .api import router as api_router
from .assessments_api import router as assessments_router
from .billing import router as billing_router
from .chat import router as chat_router
from .db import SessionLocal
from .feedback import router as feedback_router
from .llm import get_llm
from .net import TLS_CONTEXT
from .profile_api import router as profile_router
from .public_api import router as public_router
from .trial import router as trial_router
from .radar_api import router as radar_router
from .seed import seed_catalog, seed_sources
from .users import DEV_LOGIN_BYPASS, SEED_DEMO, normalize_locale, upsert_user

LINKEDIN_AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
SCOPES = "openid profile email"

CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8080").rstrip("/")
REDIRECT_URI = f"{PUBLIC_URL}/auth/callback"
SESSION_SECRET = os.environ["SESSION_SECRET"]


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() not in ("0", "false", "no", "")


# Threads de fond dans le processus de l'API : oui en local (défaut), non en production (jobs Container Apps)
RUN_BACKGROUND_THREADS = _flag("RUN_BACKGROUND_THREADS", "true")


def check_production_settings(public_url: str, dev_login_bypass: bool) -> None:
    """Refuse de démarrer si l'accès sans login (en-tête X-Dev-User) est activé sur un site public en HTTPS :
    en production il n'y a pas de nginx pour retirer cet en-tête, n'importe qui pourrait l'envoyer."""
    if dev_login_bypass and public_url.startswith("https://"):
        raise RuntimeError("DEV_LOGIN_BYPASS doit valoir false quand PUBLIC_URL est en https (production)")


check_production_settings(PUBLIC_URL, DEV_LOGIN_BYPASS)

# Journaux : JSON sur la sortie standard (LOG_FORMAT=text en local), logger "mire", niveau INFO
logs.configure()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Le schéma est créé par les migrations Alembic : avant uvicorn en local (docker-compose.yml), par le job
    # mire-migrate en production (lancé par la CI avant chaque mise à jour de l'API)
    with SessionLocal() as db:
        if SEED_DEMO:  # données de démo de la maquette (fausses offres) : désactivées par défaut
            seed_catalog(db)
        seed_sources(db)  # sources de départ (même sans données de démo : ce sont de vraies sources)
    if RUN_BACKGROUND_THREADS:
        # Évaluation automatique des offres en tâche de fond (seulement si Gemini est configuré)
        if get_llm() is not None and _flag("ASSESSMENT_WORKER", "true"):
            assessments.assessor = assessments.Assessor(
                SessionLocal, get_llm, interval=float(os.environ.get("ASSESSMENT_INTERVAL_S", "120")),
            )
            assessments.assessor.start()
        # Collecte quotidienne des offres sur les ATS (sans LLM) : met une tâche « collect » en file
        if collector.enabled():
            collector.collector = collector.DailyCollector(SessionLocal)
            collector.collector.start()
        # File de tâches (collecte à la demande, présélection…) traitée dans ce processus
        tasks.local_worker = tasks.LocalWorker(SessionLocal)
        tasks.local_worker.start()
    yield
    if assessments.assessor is not None:
        assessments.assessor.stop()
    if collector.collector is not None:
        collector.collector.stop()
    if tasks.local_worker is not None:
        tasks.local_worker.stop()


app = FastAPI(title="Mire", lifespan=lifespan)
app.include_router(api_router)
app.include_router(profile_router)
app.include_router(chat_router)
app.include_router(assessments_router)
app.include_router(feedback_router)
app.include_router(radar_router)
app.include_router(agent_router)
app.include_router(public_router)
app.include_router(trial_router)  # /api/public/trial (essai gratuit sans compte), /api/admin/trial-leads
app.include_router(billing_router)  # /api/billing/webhook : sans session (appelé par Stripe)
app.include_router(account_router)  # /api/me/export, DELETE /api/me : droits RGPD
app.include_router(analytics_router)  # /api/analytics/market, /api/analytics/me, /api/admin/taxonomy-health
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="jobradar_session",
    max_age=7 * 24 * 3600,
    same_site="lax",
    https_only=PUBLIC_URL.startswith("https://"),  # derrière l'ingress Container Apps : cookie Secure
)
# Taille maximale du corps des requêtes (comme client_max_body_size de nginx) : 413 au-delà
app.add_middleware(BodySizeLimit)


def _save_user(info: dict) -> int:
    with SessionLocal() as db:
        return upsert_user(db, info).id


APP_HOME = "/tableau-de-bord"  # « / » est la landing publique


def _safe_next(value: str | None) -> str:
    """N'accepte que des chemins internes (évite les redirections ouvertes) ; la landing renvoie à l'app."""
    if value and value.startswith("/") and not value.startswith("//") and value not in ("/", "/bienvenue"):
        return value
    return APP_HOME


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    """Probe de liveness : le processus répond (sans session, sans accès à la base)."""
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
def readyz() -> JSONResponse:
    """Probe de readiness : la base répond à SELECT 1."""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("mire").warning("readyz : base injoignable (%s)", type(exc).__name__)
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})


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
    return RedirectResponse(request.session.pop("next", APP_HOME), status_code=303)


@app.get("/auth/me")
def me(request: Request) -> JSONResponse:
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "Non connecté")
    return JSONResponse(user)


@app.get("/auth/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/", status_code=303)  # retour sur la landing


@app.get("/auth/logged-out")
def logged_out() -> Response:
    html = (
        "<!doctype html><meta charset=utf-8><title>Déconnecté</title>"
        "<body style='font-family:Inter,sans-serif;padding:40px'>"
        "<p>Vous êtes déconnecté.</p><p><a href='/auth/login'>Se reconnecter avec LinkedIn</a></p>"
    )
    return Response(html, media_type="text/html")


# Production : le front est servi par l'API (pas de nginx) ; en local, nginx le sert (FRONTEND_DIR vide).
# En dernier : la route attrape-tout des pages ne doit masquer aucune route de l'API.
if os.environ.get("FRONTEND_DIR"):
    mount_frontend(app, Path(os.environ["FRONTEND_DIR"]), os.environ.get("ASSET_VERSION") or None)
