"""Front (HTML/CSS/JS de frontend/) servi par FastAPI, en production (un seul conteneur exposé, sans nginx).

Reproduit nginx.conf (toujours utilisé par docker compose en local) :
- landing publique sur « / » (et /index.html) ; /bienvenue -> 301 vers « / » ;
- toute autre page exige une session valide (même règle que /auth/check), sinon 302 vers la landing
  « /?next=<chemin> » (le bouton LinkedIn ramène ensuite à la page demandée) ;
- /auth/* et /api/* ne sont pas concernés (l'API répond 401 en JSON) ;
- URLs propres : /entreprises sert entreprises.html ; les pages supprimées (REMOVED_PAGES) renvoient aux offres ;
- /assets/* : fichiers statiques publics.
Cache : pages HTML en « no-cache » ; dans le HTML, les URL /assets/… reçoivent « ?v=<ASSET_VERSION> » (SHA du
commit, fixé au build de l'image) et sont mises en cache un an (immutable) : un déploiement change l'URL.
Les fichiers demandés sans version (imports entre modules JS, polices des CSS) restent en « no-cache »
(revalidés par ETag / Last-Modified : 304 s'ils n'ont pas changé).

Limites de taille du corps des requêtes (comme client_max_body_size dans nginx) : BodySizeLimit.
"""

import re
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles

# Pages publiques : landing et pages légales (accessibles sans compte, comme l'exige la loi)
LEGAL_PAGES = ("/mentions-legales", "/cgv", "/confidentialite")
PUBLIC_PAGES = {"/", "/index.html", *LEGAL_PAGES, *(f"{p}.html" for p in LEGAL_PAGES)}
REMOVED_PAGES = ("/tableau-de-bord", "/analytics")  # pages supprimées : redirigées vers les offres
LONG_CACHE = "public, max-age=31536000, immutable"
ASSET_URL = re.compile(r'((?:href|src)=")(/assets/[^"?#]+)(")')

MB = 1024 * 1024
# (préfixe, taille max) : le premier préfixe qui correspond s'applique, sinon DEFAULT_BODY_LIMIT
BODY_LIMITS: tuple[tuple[str, int], ...] = (
    ("/api/profile/import/", 21 * MB),  # export LinkedIn (ZIP, 20 Mo), PDF
    ("/api/chat", 64 * 1024),
    ("/api/billing/webhook", 1 * MB),
)
DEFAULT_BODY_LIMIT = 2 * MB


class VersionedStaticFiles(StaticFiles):
    """Fichiers de /assets : cache long si l'URL porte une version (?v=…), sinon revalidation."""

    def file_response(self, full_path, stat_result, scope, status_code=200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        versioned = b"v=" in scope.get("query_string", b"")
        response.headers["Cache-Control"] = LONG_CACHE if versioned else "no-cache"
        return response


def mount_frontend(app: FastAPI, directory: Path, asset_version: str | None = None) -> None:
    """Ajoute à l'application les routes du front. À appeler APRÈS toutes les autres routes (route attrape-tout)."""
    directory = directory.resolve()
    version = asset_version or str(int(time.time()))  # à défaut de SHA : change à chaque démarrage
    rendered: dict[Path, tuple[float, str]] = {}

    def render(path: Path) -> str:
        mtime = path.stat().st_mtime
        cached = rendered.get(path)
        if cached is None or cached[0] != mtime:
            html = ASSET_URL.sub(lambda m: f"{m[1]}{m[2]}?v={version}{m[3]}", path.read_text(encoding="utf-8"))
            rendered[path] = cached = (mtime, html)
        return cached[1]

    def resolve(url_path: str) -> Path | None:
        rel = url_path.strip("/") or "index.html"
        for candidate in (rel, f"{rel}.html", f"{rel}/index.html"):  # try_files $uri $uri.html $uri/
            file = (directory / candidate).resolve()
            if file.is_file() and directory in file.parents:
                return file
        return None

    app.mount("/assets", VersionedStaticFiles(directory=directory / "assets"), name="assets")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def page(path: str, request: Request) -> Response:
        url_path = "/" + path
        if url_path.split("/")[1] in ("api", "auth"):  # routes de l'API inconnues : 404 JSON, pas de redirection
            raise HTTPException(404, "Not Found")
        if url_path == "/bienvenue":  # ancienne adresse de la landing
            query = request.url.query
            return RedirectResponse("/" + (f"?{query}" if query else ""), status_code=301)
        if url_path not in PUBLIC_PAGES and not request.session.get("user"):
            # Paramètres gardés : un lien du récapitulatif (/postes?offre=…&retour=like) mène à l'offre après connexion
            query = request.url.query
            target = url_path + (f"?{query}" if query else "")
            return RedirectResponse(f"/?next={quote(target, safe='/')}", status_code=302)
        if url_path.removesuffix(".html") in REMOVED_PAGES:  # anciens liens et favoris : les offres
            query = request.url.query
            return RedirectResponse("/postes" + (f"?{query}" if query else ""), status_code=302)
        file = resolve(url_path)
        if file is None:
            return HTMLResponse("<!doctype html><meta charset=utf-8><title>Page introuvable</title>"
                                "<p>Page introuvable. <a href='/'>Retour à l'accueil</a></p>", status_code=404)
        headers = {"Cache-Control": "no-cache"}
        if file.suffix == ".html":
            return HTMLResponse(render(file), headers=headers)
        return FileResponse(file, headers=headers)


class _BodyTooLarge(HTTPException):
    def __init__(self, limit: int):
        super().__init__(413, f"Requête trop volumineuse (maximum {limit // 1024} Ko)")


class BodySizeLimit:
    """Middleware ASGI : 413 si le corps dépasse la limite du chemin (Content-Length, ou octets reçus)."""

    def __init__(self, app, limits: tuple[tuple[str, int], ...] = BODY_LIMITS, default: int = DEFAULT_BODY_LIMIT):
        self.app, self.limits, self.default = app, limits, default

    def limit_for(self, path: str) -> int:
        return next((size for prefix, size in self.limits if path.startswith(prefix)), self.default)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.limit_for(scope["path"])
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and length.isdigit() and int(length) > limit:
            return await self._reject(limit, scope, receive, send)
        received = 0
        started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # HTTPException : FastAPI la laisse passer telle quelle pendant la lecture du corps -> 413
                    raise _BodyTooLarge(limit)
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(limit, scope, receive, send)

    @staticmethod
    async def _reject(limit: int, scope, receive, send):
        response = JSONResponse({"detail": _BodyTooLarge(limit).detail}, status_code=413)
        await response(scope, receive, send)
