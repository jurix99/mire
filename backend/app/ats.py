"""Lecture des offres via les API publiques des ATS (docs/processus-recherche.md, §3-4).

- fetch_postings : liste ACTUELLE des offres d'une entreprise (titre, lien, lieu, mode, salaire, service,
  description complète en texte, date de publication).
- check_company : compare aux offres de Mire -> listées (ouverture vérifiée), absentes (fermées), nouvelles.
- detect_ats : retrouve l'ATS et l'identifiant (slug) d'une entreprise, d'après son URL carrières / site, puis en
  testant des identifiants probables. Une détection n'est retenue que si le nom de l'entreprise apparaît dans
  ses offres (pour ne pas confondre avec une homonyme).
Garde-fous : identifiant strictement validé avant de construire l'URL ; une liste vide ou une erreur ne ferme
jamais rien (identifiant probablement faux).
"""

import html
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

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
DESCRIPTION_CHARS = 20_000


@dataclass
class Posting:
    ats_job_id: str
    title: str
    url: str | None = None
    location: str | None = None
    published_on: date | None = None
    description: str | None = None
    work_mode: str | None = None  # sur_site | hybride | remote
    salary: str | None = None
    department: str | None = None


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


# --- Conversion -----------------------------------------------------------------------------


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "tr", "section"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(value: str | None) -> str | None:
    """HTML (éventuellement échappé, cas de Greenhouse) -> texte lisible."""
    if not value:
        return None
    if "&lt;" in value:
        value = html.unescape(value)
    parser = _Text()
    parser.feed(value)
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    return text[:DESCRIPTION_CHARS] or None


def _date(value) -> date | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, int | float):  # millisecondes (Lever)
            return datetime.fromtimestamp(value / 1000, UTC).date()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" UTC", "+00:00")).date()
    except (ValueError, OSError):
        return None


WORK_MODES = {"onsite": "sur_site", "on-site": "sur_site", "hybrid": "hybride", "remote": "remote"}


def _mode(value: str | None, location: str | None = None) -> str | None:
    key = (value or "").strip().lower().replace("_", "")
    if key in WORK_MODES:
        return WORK_MODES[key]
    if location and re.search(r"\bremote\b|t[ée]l[ée]travail", location, re.IGNORECASE):
        return "remote"
    return None


def _range(r: dict | None) -> str | None:
    if not r or not (r.get("min") or r.get("max")):
        return None
    cur = r.get("currency") or ""
    lo, hi = r.get("min"), r.get("max")
    return f"{lo or '?'}-{hi or '?'} {cur}".strip()


# --- Lecture des tableaux d'offres ------------------------------------------------------------


def board_url(ats: str, slug: str) -> str:
    if ats not in SUPPORTED:
        raise ATSError(f"ATS non pris en charge pour la lecture automatique : {ats}")
    if not slug or not SLUG.match(slug):
        raise ATSError("Identifiant ATS invalide")
    return {
        "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
        "lever": f"https://api.lever.co/v0/postings/{slug}?mode=json",
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        "recruitee": f"https://{slug}.recruitee.com/api/offers/",
    }[ats]


def parse_postings(ats: str, data) -> list[Posting]:
    """Réponse JSON de l'ATS -> offres ouvertes."""
    out: list[Posting] = []
    if ats == "ashby":
        for j in (data or {}).get("jobs", []):
            if j.get("isListed") is False:
                continue
            comp = j.get("compensation") or {}
            out.append(Posting(
                str(j.get("id")), j.get("title") or "", j.get("jobUrl") or j.get("applyUrl"),
                j.get("location") or j.get("locationName"), _date(j.get("publishedAt")),
                description=(j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")) or "")[:DESCRIPTION_CHARS] or None,
                work_mode=_mode(j.get("workplaceType"), j.get("location")),
                salary=comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary"),
                department=j.get("department") or j.get("team"),
            ))
    elif ats == "lever":
        for j in data or []:
            cat = j.get("categories") or {}
            desc = "\n\n".join(filter(None, [j.get("descriptionPlain"), *[
                f"{li.get('text', '')}\n{html_to_text(li.get('content')) or ''}" for li in j.get("lists") or []
            ], j.get("additionalPlain")]))
            out.append(Posting(
                str(j.get("id")), j.get("text") or "", j.get("hostedUrl"), cat.get("location"), _date(j.get("createdAt")),
                description=desc[:DESCRIPTION_CHARS] or None, work_mode=_mode(j.get("workplaceType"), cat.get("location")),
                salary=_range(j.get("salaryRange")), department=cat.get("team") or cat.get("department"),
            ))
    elif ats == "greenhouse":
        for j in (data or {}).get("jobs", []):
            loc = (j.get("location") or {}).get("name")
            deps = j.get("departments") or []
            out.append(Posting(
                str(j.get("id")), j.get("title") or "", j.get("absolute_url"), loc,
                _date(j.get("first_published") or j.get("updated_at")), description=html_to_text(j.get("content")),
                work_mode=_mode(None, loc), department=deps[0].get("name") if deps else None,
            ))
    elif ats == "recruitee":
        for j in (data or {}).get("offers", []):
            desc = "\n\n".join(filter(None, [html_to_text(j.get("description")), html_to_text(j.get("requirements"))]))
            mode = "remote" if j.get("remote") else "hybride" if j.get("hybrid") else _mode(None, j.get("location"))
            out.append(Posting(
                str(j.get("id")), j.get("title") or "", j.get("careers_url"), j.get("location"),
                _date(j.get("published_at")), description=desc or None, work_mode=mode,
                salary=_range(j.get("salary")), department=j.get("department"),
            ))
    return [p for p in out if p.title.strip()]


def _client() -> httpx.Client:
    return httpx.Client(verify=TLS_CONTEXT, timeout=TIMEOUT, follow_redirects=False,
                        headers={"User-Agent": "Mire job tracker"})


def fetch_postings(ats: str, slug: str, client: httpx.Client | None = None) -> list[Posting]:
    url = board_url(ats, slug)
    own = client is None
    client = client or _client()
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


# --- Détection de l'ATS -------------------------------------------------------------------------

URL_PATTERNS = [
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9._-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9._-]+)", re.I)),
    ("greenhouse", re.compile(r"(?:job-)?boards\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9._-]+)", re.I)),
    ("recruitee", re.compile(r"//([A-Za-z0-9-]+)\.recruitee\.com", re.I)),
]


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def slug_candidates(company: Company) -> list[str]:
    name = _ascii(company.name)
    base = re.sub(r"\b(sas|sa|inc|ltd|ai|labs?|technologies|tech|hq)\b", " ", name)
    words = re.findall(r"[a-z0-9]+", name)
    core = re.findall(r"[a-z0-9]+", base)
    cands = ["".join(words), "-".join(words), "".join(core), "-".join(core)]
    host = urlsplit(company.website if company.website and "://" in company.website else f"https://{company.website or ''}").hostname
    if host:
        cands.append(host.removeprefix("www.").split(".")[0])
    return [c for c in dict.fromkeys(cands) if c and SLUG.match(c)][:5]


def mentions_company(company: Company, postings: list[Posting]) -> bool:
    """Le nom de l'entreprise apparaît-il dans ses offres ? (évite d'importer les offres d'une homonyme)"""
    name = _ascii(company.name).strip()
    if not postings:
        return False
    for p in postings[:10]:
        text = _ascii(" ".join(filter(None, [p.title, p.description, p.url])))
        if name and name in text:
            return True
    return False


def detect_ats(company: Company, fetch=fetch_postings) -> tuple[str, str] | None:
    """(ats, slug) trouvé et cohérent avec l'entreprise, sinon None."""
    tried: list[tuple[str, str]] = []
    for url in filter(None, [company.careers_url, company.website]):
        for ats, pattern in URL_PATTERNS:
            if m := pattern.search(url):
                tried.append((ats, m[1]))
    for slug in slug_candidates(company):
        for ats in SUPPORTED:
            tried.append((ats, slug))
    for ats, slug in dict.fromkeys(tried):
        try:
            postings = fetch(ats, slug)
        except ATSError:
            continue
        if mentions_company(company, postings):
            return ats, slug
    return None


# --- Vérification : ouvertes, fermées, nouvelles ------------------------------------------------


def check_company(db: Session, company: Company, fetch=fetch_postings) -> CheckResult:
    """Compare les offres de Mire à la liste actuelle de l'ATS (sans commit).

    Les offres listées complètent les champs vides des postes correspondants (description, mode, salaire…).
    """
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
            job.closed_at = None  # republiée le cas échéant
            fill_from_posting(job, p)
            result.verified.append(job.id)
        elif job.closed_at is None and postings:
            # Absente de la liste actuelle : fermée. Liste vide -> on ne ferme rien (slug probablement faux).
            job.closed_at = now
            result.closed.append(job.id)
    result.new_postings = [p for p in postings if p.ats_job_id not in matched]
    return result


def fill_from_posting(job: Job, p: Posting) -> None:
    """Complète les champs VIDES d'un poste à partir de l'offre listée (jamais d'écrasement)."""
    for attr, value in (("summary", p.description), ("work_mode", p.work_mode), ("salary", p.salary),
                        ("published_on", p.published_on), ("location", p.location), ("category", p.department)):
        if value and not getattr(job, attr):
            setattr(job, attr, value[:200] if isinstance(value, str) and attr in ("salary", "location", "category") else value)
