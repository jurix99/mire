"""Dédoublonnage des offres (docs/processus-recherche.md, §4).

- URL normalisée : schéma et hôte en minuscules, sans « www. », sans paramètres de suivi (utm_*, ref, source…),
  sans fragment ni « / » final -> contrainte unique en base (jobs.url_key).
  Une page d'accueil (chemin vide) n'est pas une URL d'offre : pas de clé.
- Même entreprise + même titre normalisé = doublon, même si l'URL diffère (agrégateurs).
Une offre déjà vue n'est jamais reproposée, quel que soit son statut.
"""

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"ref", "source", "src", "gh_src", "lever-origin", "lever-source", "utm", "fbclid", "gclid",
                   "trk", "trackingid", "refid", "origin", "via"}


def normalize_url(url: str | None) -> str | None:
    if not url or not url.strip():
        return None
    raw = url.strip()
    if "://" not in raw:
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower().removeprefix("www.")
    if not host:
        return None
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS]
    if not path and not query:
        return None  # page d'accueil : pas une offre
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def title_key(title: str | None) -> str | None:
    """Titre normalisé : sans accents, ponctuation ni casse ; « Sr. » = « Senior », « H/F » retiré."""
    if not title:
        return None
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\((?:h|f|m|w|x)\s*/\s*(?:h|f|m|w|x)(?:\s*/\s*(?:h|f|m|w|x))?\)|\b(?:h|f|m|w)\s*/\s*(?:h|f|m|w)\b", " ", t)
    t = re.sub(r"\bsr\b\.?", "senior", t)
    t = re.sub(r"\bjr\b\.?", "junior", t)
    t = re.sub(r"[^a-z0-9+#]+", " ", t)
    return " ".join(t.split())[:300] or None
