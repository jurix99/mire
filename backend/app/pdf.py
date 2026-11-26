"""PDF des candidatures (CV adapté, lettre) : gabarit HTML + CSS sobre, rendu par WeasyPrint dans le worker.

- Lisible par les ATS : texte sélectionnable, une seule colonne, pas de tableau ni d'image, polices système
  (Liberation / DejaVu, installées dans l'image worker), titres de section en clair.
- Une ou deux pages A4 ; sans photo.
- Le PDF est rendu à partir du TEXTE du CV (draft.cv_text : « ## » sections, « ### » postes, « - » puces) : une
  modification faite par l'utilisateur sur la page Candidature se retrouve donc dans le PDF.
WeasyPrint et ses bibliothèques (Pango) ne sont que dans l'image worker (groupe uv « worker ») : ailleurs,
render_pdf lève PDFUnavailable et la page propose de copier le texte.
"""

import html
import logging
import re
from datetime import date

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import Application, ApplicationDraft, GeneratedFile

log = logging.getLogger("mire.pdf")

MONTHS_FR = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre")
MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December")

CSS = """
@page { size: A4; margin: 16mm 17mm 16mm 17mm; }
html { font-family: "Liberation Sans", "DejaVu Sans", Arial, sans-serif; font-size: 10pt; color: #111; line-height: 1.38; }
body { margin: 0; }
h1 { font-size: 18pt; margin: 0 0 2pt; font-weight: 700; }
.headline { font-size: 11.5pt; margin: 0 0 3pt; color: #222; }
.contact { font-size: 9pt; color: #444; margin: 0 0 10pt; }
h2 { font-size: 10.5pt; text-transform: uppercase; letter-spacing: .06em; margin: 12pt 0 4pt; padding-bottom: 2pt;
     border-bottom: 0.6pt solid #999; }
h3 { font-size: 10pt; margin: 7pt 0 2pt; font-weight: 700; page-break-after: avoid; }
p { margin: 0 0 4pt; }
ul { margin: 0 0 2pt; padding-left: 13pt; }
li { margin: 0 0 1.5pt; }
.letter { font-size: 10.5pt; line-height: 1.5; }
.letter .meta { margin: 18pt 0 14pt; }
.letter .subject { font-weight: 700; margin: 0 0 12pt; }
.letter p { margin: 0 0 9pt; text-align: left; }
.letter .signature { margin-top: 16pt; }
"""


class PDFUnavailable(Exception):
    """WeasyPrint absent (hors image worker) ou rendu impossible."""


def render_pdf(document: str) -> bytes:
    try:
        from weasyprint import HTML  # importé ici : seulement dans l'image worker
    except (ImportError, OSError) as exc:  # OSError : bibliothèques Pango absentes
        raise PDFUnavailable(type(exc).__name__) from None
    try:
        return HTML(string=document).write_pdf()
    except Exception as exc:  # noqa: BLE001
        raise PDFUnavailable(type(exc).__name__) from None


def _page(title: str, body: str, language: str) -> str:
    return (f'<!doctype html><html lang="{language}"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def cv_html(cv_text: str, language: str = "fr") -> str:
    """Texte du CV -> HTML : 1re ligne = nom, 2e = titre, 3e = contact ; puis « ## », « ### », « - », paragraphes."""
    lines = (cv_text or "").splitlines()
    head, rest = lines[:3], lines[3:]
    while len(head) < 3:
        head.append("")
    out = [f"<h1>{html.escape(head[0])}</h1>"]
    if head[1].strip():
        out.append(f'<p class="headline">{html.escape(head[1])}</p>')
    if head[2].strip():
        out.append(f'<p class="contact">{html.escape(head[2])}</p>')
    in_list = False
    for line in rest:
        text = line.strip()
        is_item = text.startswith("- ")
        if in_list and not is_item:
            out.append("</ul>")
            in_list = False
        if not text:
            continue
        if text.startswith("### "):
            out.append(f"<h3>{html.escape(text[4:])}</h3>")
        elif text.startswith("## "):
            out.append(f"<h2>{html.escape(text[3:])}</h2>")
        elif is_item:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{html.escape(text[2:])}</li>")
        else:
            out.append(f"<p>{html.escape(text)}</p>")
    if in_list:
        out.append("</ul>")
    return _page(head[0] or "CV", "".join(out), language)


def long_date(day: date, language: str) -> str:
    """« 26 novembre 2026 » / « November 26, 2026 » (sans dépendre de la locale du système)."""
    if language == "fr":
        return f"{day.day} {MONTHS_FR[day.month - 1]} {day.year}"
    return f"{MONTHS_EN[day.month - 1]} {day.day}, {day.year}"


def letter_html(text: str, ident: dict, company: str, job_title: str, language: str = "fr",
                day: date | None = None) -> str:
    day = day or date.today()
    contact = " · ".join(filter(None, [ident.get("email"), ident.get("phone"), ident.get("city")]))
    city = ident.get("city")
    when = long_date(day, language)
    dated = f"{city}, le {when}" if language == "fr" and city else (f"{city}, {when}" if city else when)
    subject = f"Objet : candidature – {job_title}" if language == "fr" else f"Application – {job_title}"
    paragraphs = "".join(f"<p>{html.escape(p.strip())}</p>" for p in re.split(r"\n\s*\n|\n", text or "") if p.strip())
    body = (f'<div class="letter"><h1>{html.escape(ident.get("name") or "")}</h1>'
            f'<p class="contact">{html.escape(contact)}</p>'
            f'<div class="meta"><p>{html.escape(company)}</p><p>{html.escape(dated)}</p></div>'
            f'<p class="subject">{html.escape(subject)}</p>{paragraphs}'
            f'<p class="signature">{html.escape(ident.get("name") or "")}</p></div>')
    return _page(f"Lettre {company}", body, language)


def store_files(db: Session, app: Application, draft: ApplicationDraft, *, render=None) -> list[str]:
    """(Re)génère les PDF du CV et de la lettre du brouillon, remplace les précédents (sans commit).

    Retourne les types générés ; PDF impossible : avertissement sur le brouillon (une seule fois).
    render : fonction HTML -> PDF (tests) ; par défaut render_pdf.
    """
    from . import application_prep as prep
    from .profile import get_profile, profile_data
    from .models import User

    render = render or render_pdf
    user = db.get(User, app.user_id)
    ident = prep.identity(user, profile_data(get_profile(db, user)).preferences.application_defaults)
    company = app.job.company.name
    wanted = []
    if draft.cv_text:
        wanted.append(("cv", lambda: cv_html(draft.cv_text, draft.language)))
    if draft.cover_letter:
        wanted.append(("lettre", lambda: letter_html(draft.cover_letter, ident, company, app.job.title,
                                                     draft.language)))
    done = []
    warnings = [w for w in draft.warnings or [] if w.get("kind") != "pdf"]
    for kind, build in wanted:
        try:
            content = render(build())
        except PDFUnavailable as exc:
            log.warning("PDF %s du process %s impossible (%s)", kind, app.id, exc)
            warnings.append({"kind": "pdf", "message": "PDF indisponible pour le moment : copie le texte, ou réessaie "
                                                       "plus tard (« Tout recharger »)."})
            break
        db.execute(delete(GeneratedFile).where(GeneratedFile.application_id == app.id, GeneratedFile.kind == kind))
        db.add(GeneratedFile(user_id=app.user_id, application_id=app.id, kind=kind,
                             filename=prep.filename(kind, ident, company), content=content))
        done.append(kind)
    for kind in {"cv", "lettre"} - {k for k, _ in wanted}:  # plus de CV / de lettre : l'ancien PDF est retiré
        db.execute(delete(GeneratedFile).where(GeneratedFile.application_id == app.id, GeneratedFile.kind == kind))
    draft.warnings = warnings
    return done
