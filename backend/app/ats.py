"""Vérification des offres ouvertes via les API publiques des ATS (docs/processus-recherche.md, §3-4).

Une page d'offre reste souvent en ligne après fermeture : on vérifie qu'elle figure dans la LISTE actuelle
des offres de l'entreprise.
- offre listée -> verified_open_at = maintenant ;
- offre absente de la liste -> closed_at = maintenant (donnée partagée : « Offre fermée » chez ceux qui ne l'ont
  pas traitée) ;
- offres listées inconnues de Mire -> renvoyées comme « nouvelles offres » (à ajouter).
Garde-fous : identifiant (slug) strictement validé avant de construire l'URL ; une liste vide ou une erreur
ne ferme jamais rien (slug probablement faux).
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .dedup import normalize_url, title_key
from .models import Company, Job
from .net import TLS_CONTEXT

log = logging.getLogger("mire.ats")

SUPPORTED = ("ashby", "lever", "greenhouse", "recruitee")
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
TIMEOUT = 15


@dataclass
class Posting:
    ats_job_id: str
    title: str
    url: str | None = None
    location: str | None = None
    published_on: date | None = None


@dataclass
class CheckResult:
    company_id: int
    ats: str | None
    listed: int = 0
    verified: list[int] = field(default_factory=list)  # job_id
    closed: list[int] = field(default_factory=list)
    new_postings: list[Posting] = field(default_factory=list)
    error: str | None = None


class ATSError(Exception):
    pass


def _date(value) -> date | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, int | float):  # millisecondes (Lever)
            return datetime.fromtimestamp(value / 1000, UTC).date()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (ValueError, OSError):
        return None


def board_url(ats: str, slug: str) -> str:
    if ats not in SUPPORTED:
        raise ATSError(f"ATS non pris en charge pour la lecture automatique : {ats}")
    if not slug or not SLUG.match(slug):
        raise ATSError("Identifiant ATS invalide")
    return {
        "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        "lever": f"https://api.lever.co/v0/postings/{slug}?mode=json",
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        "recruitee": f"https://{slug}.recruitee.com/api/offers/",
    }[ats]


def parse_postings(ats: str, data) -> list[Posting]:
    """Réponse JSON de l'ATS -> offres ouvertes."""
    out: list[Posting] = []
    if ats == "ashby":
        for j in (data or {}).get("jobs", []):
            if j.get("isListed") is False:
                continue
            out.append(Posting(str(j.get("id")), j.get("title") or "", j.get("jobUrl") or j.get("applyUrl"),
                               j.get("location") or j.get("locationName"), _date(j.get("publishedAt"))))
    elif ats == "lever":
        for j in data or []:
            loc = (j.get("categories") or {}).get("location")
            out.append(Posting(str(j.get("id")), j.get("text") or "", j.get("hostedUrl"), loc, _date(j.get("createdAt"))))
    elif ats == "greenhouse":
        for j in (data or {}).get("jobs", []):
            loc = (j.get("location") or {}).get("name")
            out.append(Posting(str(j.get("id")), j.get("title") or "", j.get("absolute_url"), loc,
                               _date(j.get("first_published") or j.get("updated_at"))))
    elif ats == "recruitee":
        for j in (data or {}).get("offers", []):
            out.append(Posting(str(j.get("id")), j.get("title") or "", j.get("careers_url"), j.get("location"),
                               _date(j.get("published_at"))))
    return [p for p in out if p.title.strip()]


def fetch_postings(ats: str, slug: str, client: httpx.Client | None = None) -> list[Posting]:
    url = board_url(ats, slug)
    own = client is None
    client = client or httpx.Client(verify=TLS_CONTEXT, timeout=TIMEOUT, follow_redirects=False,
                                    headers={"User-Agent": "Mire job tracker"})
    try:
        resp = client.get(url)
        if resp.status_code == 404:
            raise ATSError("Tableau d'offres introuvable : identifiant ATS à vérifier")
        resp.raise_for_status()
        return parse_postings(ats, resp.json())
    except httpx.HTTPError as exc:
        raise ATSError(f"ATS injoignable ({type(exc).__name__})") from None
    except ValueError:
        raise ATSError("Réponse de l'ATS illisible") from None
    finally:
        if own:
            client.close()


def check_company(db: Session, company: Company, fetch=fetch_postings) -> CheckResult:
    """Compare les offres de Mire à la liste actuelle de l'ATS (sans commit)."""
    result = CheckResult(company_id=company.id, ats=company.ats)
    if not company.ats or not company.ats_slug:
        result.error = "Renseignez l'ATS et son identifiant (slug) de l'entreprise"
        return result
    try:
        postings = fetch(company.ats, company.ats_slug)
    except ATSError as exc:
        result.error = str(exc)
        return result
    now = datetime.now(UTC)
    company.last_checked_at = now
    result.listed = len(postings)
    by_id = {p.ats_job_id: p for p in postings}
    by_url = {k: p for p in postings if (k := normalize_url(p.url))}
    by_title = {k: p for p in postings if (k := title_key(p.title))}
    matched: set[str] = set()
    jobs = db.scalars(select(Job).where(Job.company_id == company.id)).all()
    for job in jobs:
        p = (by_id.get(job.ats_job_id or "") or by_url.get(job.url_key or "") or by_title.get(job.title_key or ""))
        if p is not None:
            matched.add(p.ats_job_id)
            job.ats_job_id = job.ats_job_id or p.ats_job_id
            job.verified_open_at = now
            if job.closed_at is not None:
                job.closed_at = None  # republiée
            result.verified.append(job.id)
        elif job.closed_at is None and postings:
            # Absente de la liste actuelle : fermée. Liste vide -> on ne ferme rien (slug probablement faux).
            job.closed_at = now
            result.closed.append(job.id)
    result.new_postings = [p for p in postings if p.ats_job_id not in matched]
    return result
