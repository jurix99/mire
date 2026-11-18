"""Récupération des logos d'entreprises, mis en cache en base.

Ordre d'essai : logo_url renseignée à la main, puis le favicon du site (services Google puis DuckDuckGo).
Seul le domaine public de l'entreprise est envoyé à ces services.
"""

import ipaddress
import socket
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx

from .models import Company
from .net import TLS_CONTEXT

MAX_LOGO_BYTES = 512 * 1024
ALLOWED_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "image/x-icon",
    "image/vnd.microsoft.icon",
}
# Si aucun logo n'a été trouvé, on ne réessaie pas avant ce délai
RETRY_AFTER = timedelta(hours=12)


def _candidate_urls(company: Company) -> Iterator[str]:
    if company.logo_url:
        yield company.logo_url
    if company.domain:
        yield f"https://www.google.com/s2/favicons?domain={company.domain}&sz=128"
        yield f"https://icons.duckduckgo.com/ip3/{company.domain}.ico"


def _refuse_private_hosts(request: httpx.Request) -> None:
    """Évite qu'une logo_url pointe vers le réseau interne (db, backend, 127.0.0.1...)."""
    host = request.url.host
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise httpx.ConnectError(f"Hôte introuvable : {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise httpx.ConnectError(f"Adresse non publique refusée : {host}")


def fetch_logo(company: Company) -> tuple[bytes, str] | None:
    with httpx.Client(
        verify=TLS_CONTEXT,
        timeout=5,
        follow_redirects=True,
        headers={"User-Agent": "job-radar/1.0"},
        event_hooks={"request": [_refuse_private_hosts]},
    ) as client:
        for url in _candidate_urls(company):
            try:
                resp = client.get(url)
            except httpx.HTTPError:
                continue
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if resp.status_code == 200 and ctype in ALLOWED_TYPES and 0 < len(resp.content) <= MAX_LOGO_BYTES:
                return resp.content, ctype
    return None


def should_fetch(company: Company) -> bool:
    if company.logo_data is not None:
        return False
    if company.logo_fetched_at is None:
        return True
    return datetime.now(UTC) - company.logo_fetched_at > RETRY_AFTER


def reset_logo(company: Company) -> None:
    company.logo_data = None
    company.logo_content_type = None
    company.logo_fetched_at = None
