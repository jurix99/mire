"""Récapitulatif des nouvelles offres par e-mail : ce qui fait revenir dans Mire entre deux visites.

Envoyé par le job quotidien (jobs.daily), après l'évaluation, à chaque utilisateur dont c'est le tour :
- fréquence choisie dans Profil > Compte : weekly (défaut), daily ou off ; pas d'adresse e-mail = rien ;
- contenu : les offres AJOUTÉES dans Mire depuis le dernier récapitulatif (ou depuis une période si aucun), ouvertes,
  sans process de l'utilisateur, proposées par le classement (ranking.py : ni écartées, ni exclues, ni fermées) et
  pertinentes (MIN_SCORE) ; les MAX_JOBS meilleures ;
- rien de nouveau : pas d'e-mail (et la date du dernier envoi ne bouge pas) ;
- Free : mêmes règles d'affichage que l'app (entitlements.access_for) : seules les offres en clair sont décrites,
  les autres sont seulement comptées, sans titre ni entreprise ; les raisons du classement restent réservées au Pro.

Forme : un message écrit (texte simple, un lien par offre, signé du nom de l'expéditeur, Reply-To si SMTP_REPLY_TO)
plutôt qu'une newsletter à cartes et boutons, que Gmail range dans l'onglet Promotions.
Liens J'aime / Pas intéressé de l'e-mail : des liens vers l'app (/postes?offre=<id>&retour=like|dislike). Rien n'est enregistré sans
session : c'est la page, une fois connecté, qui applique le retour (un antivirus qui suit les liens n'écrit rien).
Désabonnement : lien avec un jeton aléatoire propre à l'utilisateur (users.email_token), sans session ; en-têtes
List-Unsubscribe et List-Unsubscribe-Post (désabonnement en un clic des messageries, RFC 8058).
"""

import html
import logging
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import entitlements, mailer, ranking
from .db import get_db
from .models import Application, Job, User
from .users import current_user

log = logging.getLogger("mire.digest")

FREQUENCIES: dict[str, timedelta | None] = {"weekly": timedelta(days=7), "daily": timedelta(days=1), "off": None}
FREQUENCY_LABELS = {"weekly": "chaque semaine", "daily": "chaque jour", "off": "jamais"}
SLACK = timedelta(hours=3)  # le job quotidien ne part pas toujours à la même minute
MIN_SCORE = 60  # même seuil que « offre pertinente » des entreprises (ranking.company_match)
MAX_JOBS = 5

Frequency = Literal["weekly", "daily", "off"]


def public_url() -> str:
    return os.environ.get("PUBLIC_URL", "http://localhost:8080").rstrip("/")


# --- Qui reçoit quoi ----------------------------------------------------------------------------------------


def is_due(user: User, at: datetime) -> bool:
    period = FREQUENCIES.get(user.digest_frequency or "weekly")
    if period is None or not (user.email or "").strip():
        return False
    last = entitlements.aware(user.digest_sent_at)
    return last is None or at - last >= period - SLACK


def window_start(user: User, at: datetime) -> datetime:
    """Offres ajoutées depuis le dernier récapitulatif ; sinon depuis une période (premier envoi, reprise)."""
    period = FREQUENCIES.get(user.digest_frequency or "weekly") or timedelta(days=7)
    last = entitlements.aware(user.digest_sent_at)
    return max(last, at - period) if last else at - period


@dataclass
class Picked:
    job: Job
    match: ranking.Match


@dataclass
class Digest:
    user: User
    plan: str
    jobs: list[Picked] = field(default_factory=list)  # décrites dans l'e-mail (en clair)
    more: int = 0  # Pro : autres offres pertinentes au-delà de MAX_JOBS
    locked: int = 0  # Free : offres pertinentes floutées (seulement comptées)

    @property
    def empty(self) -> bool:
        return not self.jobs and not self.locked


def is_new(job: Job, since: datetime, at: datetime) -> bool:
    """Fenêtre ]since, at] : une offre ajoutée à l'instant d'un envoi n'est jamais comptée deux fois."""
    created = entitlements.aware(job.created_at) or at
    return since < created <= at


def pick(db: Session, user: User, at: datetime) -> Digest:
    since = window_start(user, at)
    own = set(db.scalars(select(Application.job_id).where(Application.user_id == user.id)))
    fresh = [j for j in db.scalars(select(Job).where(Job.closed_at.is_(None))
                                   .options(selectinload(Job.company)).order_by(Job.id))
             if is_new(j, since, at) and j.id not in own]
    plan = entitlements.plan_of(db, user)
    digest = Digest(user, plan)
    if not fresh:
        return digest
    ranker = ranking.Ranker(db, user)
    scored = [Picked(j, m) for j in fresh if (m := ranker.match(j)).hidden is None and m.score >= MIN_SCORE]
    scored.sort(key=lambda p: (-p.match.score, -p.job.id))
    access = entitlements.access_for(db, user)
    clear = [p for p in scored if not access.job_is_locked(p.job)]
    digest.jobs = clear[:MAX_JOBS]
    digest.more = len(clear) - len(digest.jobs)
    digest.locked = len(scored) - len(clear)
    return digest


# --- Contenu ------------------------------------------------------------------------------------------------


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _ago(d: date | datetime | None, at: datetime) -> str | None:
    if d is None:
        return None
    day = d.date() if isinstance(d, datetime) else d
    days = (at.date() - day).days
    return "aujourd'hui" if days <= 0 else "hier" if days == 1 else f"il y a {days} jours"


def _verified(job: Job, at: datetime) -> str | None:
    seen = entitlements.aware(job.verified_open_at)
    if seen is None:
        return None
    hours = int((at - seen).total_seconds() // 3600)
    when = "il y a moins d'une heure" if hours < 1 else f"il y a {hours} h" if hours < 48 else _ago(seen, at)
    return f"Vérifiée {when} sur le site de l'entreprise"


def _salary(job: Job) -> str | None:
    lo, hi = job.salary_min_keur, job.salary_max_keur
    if lo and hi and lo != hi:
        return f"{lo}-{hi} k€"
    if lo or hi:
        return f"{lo or hi} k€"
    return job.salary


def facts(job: Job) -> list[str]:
    mode = ranking.MODE_LABEL.get(job.work_mode or "")
    return [x for x in (ranking.city_of(job.location) or job.location, mode, _salary(job)) if x]


def job_link(job_id: int, reaction: str | None = None) -> str:
    return f"{public_url()}/postes?offre={job_id}" + (f"&retour={reaction}" if reaction else "")


def unsubscribe_link(user: User) -> str:
    return f"{public_url()}/api/public/email/desabonnement?t={user.email_token}"


def subject(d: Digest) -> str:
    """Le poste d'abord, comme un message écrit à la main (un compteur en tête fait « catalogue » : onglet Promotions)."""
    if d.jobs:
        first = d.jobs[0].job
        others = len(d.jobs) + d.more + d.locked - 1
        head = f"{first.title} chez {first.company.name}"
        return f"{head}, et {_plural(others, 'autre offre', 'autres offres')}" if others else head
    return _plural(d.locked, "nouvelle offre correspond à votre recherche",
                   "nouvelles offres correspondent à votre recherche")


SINCE = {"weekly": "cette semaine", "daily": "depuis hier"}


def intro(d: Digest) -> str:
    n = len(d.jobs) + d.more + d.locked
    since = SINCE.get(d.user.digest_frequency, "")
    head = ("Une nouvelle offre correspond" if n == 1 else f"{n} nouvelles offres correspondent") + \
        f" à votre recherche {since}".rstrip()
    if not d.jobs:
        return f"{head}."
    return f"{head}. Voici celle qui colle le mieux :" if len(d.jobs) == 1 else \
        f"{head}. Voici les {len(d.jobs)} qui collent le mieux :"


def _closing() -> list[str]:
    reply = ["Une remarque, une offre à côté de la plaque ? Répondez simplement à ce message."] if mailer.reply_to() else []
    return [*reply, "", "Bonne recherche,", mailer.sender_name()]


def render_text(d: Digest, at: datetime) -> str:
    hello = f"Bonjour {d.user.given_name}," if d.user.given_name else "Bonjour,"
    lines = [hello, "", intro(d), ""]
    for p in d.jobs:
        j = p.job
        lines.append(f"{j.title} chez {j.company.name}")
        if facts(j):
            lines.append(" · ".join(facts(j)))
        if d.plan == "pro" and p.match.reasons:
            lines.append(f"Pourquoi : {' ; '.join(p.match.reasons[:2])}")
        if v := _verified(j, at):
            lines.append(v)
        lines.append(job_link(j.id))
        lines.append(f"J'aime : {job_link(j.id, 'like')}  ·  Pas intéressé : {job_link(j.id, 'dislike')}")
        lines.append("")
    if d.more:
        lines += [f"Et {_plural(d.more, 'autre offre', 'autres offres')} dans Mire : {public_url()}/postes", ""]
    if d.locked:
        lines += [f"{_plural(d.locked, 'offre pertinente est réservée', 'offres pertinentes sont réservées')} "
                  f"à l'offre Pro : {public_url()}/postes?pro=1", ""]
    lines += ["Vos J'aime et Pas intéressé reclassent les offres tout de suite.", *_closing(), "", "--",
              f"Vous recevez ce récapitulatif {FREQUENCY_LABELS[d.user.digest_frequency]}. "
              f"Changer la fréquence : {public_url()}/mon-profil#compte",
              f"Ne plus le recevoir : {unsubscribe_link(d.user)}"]
    return "\n".join(lines)


_P = "margin:0 0 16px"
_SMALL = "font-size:13px;color:#57575e"


def render_html(d: Digest, at: datetime) -> str:
    """Mise en page de message écrit (texte, un lien par offre, pas de cartes ni de boutons) : moins « newsletter »."""
    e = html.escape
    hello = f"Bonjour {e(d.user.given_name)}," if d.user.given_name else "Bonjour,"
    offers = []
    for p in d.jobs:
        j = p.job
        lines = [f'<a href="{e(job_link(j.id))}" style="font-weight:600;color:#1d4ed8">{e(j.title)}</a>'
                 f" chez {e(j.company.name)}"]
        meta = " · ".join(e(x) for x in [*facts(j), f"publiée {_ago(j.published_on or j.created_at, at)}"] if x)
        lines.append(f'<span style="{_SMALL}">{meta}</span>')
        if d.plan == "pro" and p.match.reasons:
            lines.append(f'<span style="font-size:14px">Pourquoi : {e(" ; ".join(p.match.reasons[:2]))}</span>')
        if v := _verified(j, at):
            lines.append(f'<span style="font-size:13px;color:#15803d">✓ {e(v)}</span>')
        lines.append(f'<span style="{_SMALL}"><a href="{e(job_link(j.id, "like"))}" style="color:#57575e">👍 J\'aime</a>'
                     f' &nbsp;·&nbsp; <a href="{e(job_link(j.id, "dislike"))}" style="color:#57575e">👎 Pas intéressé</a></span>')
        offers.append(f'<p style="margin:0 0 20px">{"<br>".join(lines)}</p>')
    more = (f'<p style="{_P}">Et <a href="{e(public_url())}/postes" style="color:#1d4ed8">'
            f'{_plural(d.more, "autre offre", "autres offres")} dans Mire</a>.</p>' if d.more else "")
    locked = ""
    if d.locked:
        n = _plural(d.locked, "offre pertinente est réservée", "offres pertinentes sont réservées")
        locked = (f'<p style="{_P}">{"Et " if d.jobs else ""}{e(n)} à l\'offre Pro : '
                  f'<a href="{e(public_url())}/postes?pro=1" style="color:#1d4ed8">les voir avec Pro</a>.</p>')
    closing = "<br>".join(e(x) for x in _closing() if x)
    freq = FREQUENCY_LABELS[d.user.digest_frequency]
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(subject(d))}</title></head>
<body style="margin:0;padding:16px;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.5;color:#18181b">
<div style="max-width:600px">
<p style="{_P}">{hello}</p>
<p style="{_P}">{e(intro(d))}</p>
{"".join(offers)}{more}{locked}
<p style="{_P}">Vos J'aime et Pas intéressé reclassent les offres tout de suite.</p>
<p style="{_P}">{closing}</p>
<p style="font-size:12px;color:#71717a;margin:24px 0 0">Vous recevez ce récapitulatif {e(freq)}.
<a href="{e(public_url())}/mon-profil#compte" style="color:#71717a">Changer la fréquence</a> ·
<a href="{e(unsubscribe_link(d.user))}" style="color:#71717a">Ne plus le recevoir</a></p>
</div></body></html>"""


def build_mail(d: Digest, at: datetime) -> mailer.Mail:
    unsubscribe = unsubscribe_link(d.user)
    return mailer.Mail(
        to=d.user.email.strip(), subject=subject(d), text=render_text(d, at), html=render_html(d, at),
        headers={"List-Unsubscribe": f"<{unsubscribe}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
    )


# --- Envoi --------------------------------------------------------------------------------------------------


def ensure_token(user: User) -> str:
    if not user.email_token:
        user.email_token = secrets.token_urlsafe(32)
    return user.email_token


def send_digests(db: Session, at: datetime | None = None,
                 sender: Callable[[mailer.Mail], None] | None = None) -> dict:
    """Un récapitulatif à chaque utilisateur dont c'est le tour. Une erreur d'envoi n'arrête pas les autres."""
    at = at or entitlements.now()
    stats = {"sent": 0, "empty": 0, "failed": 0}
    if sender is None:
        if not mailer.configured():
            return {**stats, "disabled": True}
        sender = mailer.send
    users = db.scalars(select(User).where(User.digest_frequency != "off", User.email.is_not(None))
                       .order_by(User.id)).all()
    for user in users:
        if not is_due(user, at):
            continue
        try:
            if not entitlements.limits(entitlements.plan_of(db, user))["digest_email"]:
                continue
            digest = pick(db, user, at)
            if digest.empty:
                stats["empty"] += 1
                continue
            ensure_token(user)
            db.flush()
            sender(build_mail(digest, at))
            user.digest_sent_at = at
            db.commit()
            stats["sent"] += 1
        except Exception as exc:  # SMTP, données inattendues : l'utilisateur suivant reçoit quand même le sien
            db.rollback()
            stats["failed"] += 1
            log.warning("Récapitulatif non envoyé à l'utilisateur %s : %s", user.id, type(exc).__name__,
                        extra={"user_id": user.id})
    return stats


# --- API ----------------------------------------------------------------------------------------------------

router = APIRouter()
DB = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]


class DigestSettings(BaseModel):
    frequency: Frequency


class DigestSettingsOut(DigestSettings):
    email: str | None
    available: bool  # un serveur d'envoi est configuré


def settings_out(user: User) -> DigestSettingsOut:
    return DigestSettingsOut(frequency=user.digest_frequency or "weekly", email=user.email,
                             available=mailer.configured())


@router.get("/api/me/digest")
def get_settings(user: Me) -> DigestSettingsOut:
    return settings_out(user)


@router.put("/api/me/digest")
def put_settings(body: DigestSettings, db: DB, user: Me) -> DigestSettingsOut:
    user = db.merge(user)
    user.digest_frequency = body.frequency
    db.commit()
    return settings_out(user)


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{html.escape(title)} · Mire</title><link rel="stylesheet" href="/assets/app.css"></head>
<body><main class="main" style="max-width:520px;margin:48px auto;padding:0 16px">
<h1>{html.escape(title)}</h1>{body}</main></body></html>""", status_code=status,
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _by_token(db: Session, token: str) -> User | None:
    token = (token or "").strip()
    return db.scalar(select(User).where(User.email_token == token)) if len(token) >= 20 else None


INVALID = ("<p>Ce lien de désabonnement n'est plus valable. Vous pouvez changer la fréquence du récapitulatif "
           "dans <a href=\"/mon-profil#compte\">Profil &gt; Compte</a>.</p>")


@router.get("/api/public/email/desabonnement", include_in_schema=False)
def unsubscribe_page(db: DB, t: str = "") -> HTMLResponse:
    """Page de confirmation : un GET ne change rien (les antivirus et aperçus suivent les liens des e-mails)."""
    user = _by_token(db, t)
    if user is None:
        return _page("Lien expiré", INVALID, 404)
    if user.digest_frequency == "off":
        return _page("Déjà désabonné", "<p>Vous ne recevez plus le récapitulatif des nouvelles offres.</p>"
                     "<p><a href=\"/mon-profil#compte\">Le réactiver</a></p>")
    return _page("Ne plus recevoir le récapitulatif", f"""
<p>Vous recevez le récapitulatif des nouvelles offres {html.escape(FREQUENCY_LABELS[user.digest_frequency])}.</p>
<form method="post" action="/api/public/email/desabonnement?t={html.escape(t)}">
<button class="btn" type="submit">Ne plus le recevoir</button></form>
<p class="small muted">Vous pourrez le réactiver dans Profil &gt; Compte.</p>""")


@router.post("/api/public/email/desabonnement", include_in_schema=False)
def unsubscribe(db: DB, t: str = "") -> HTMLResponse:
    """Désabonnement (bouton de la page, ou « en un clic » des messageries : POST List-Unsubscribe=One-Click)."""
    user = _by_token(db, t)
    if user is None:
        raise HTTPException(404, "Lien de désabonnement inconnu")
    user.digest_frequency = "off"
    db.commit()
    log.info("Récapitulatif désactivé (lien de l'e-mail)", extra={"user_id": user.id})
    return _page("C'est noté", "<p>Vous ne recevrez plus le récapitulatif des nouvelles offres.</p>"
                 "<p><a href=\"/mon-profil#compte\">Le réactiver</a></p>")
