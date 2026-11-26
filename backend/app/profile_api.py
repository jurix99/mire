"""API du profil utilisateur (/api/profile), imports (export LinkedIn, PDF, texte collé) et propositions.

Tout ce qui modifie le profil sans être une saisie directe (import, analyse, synthèse des règles) passe
par une PROPOSITION : un aperçu élément par élément (voir proposals.py). Rien n'est enregistré avant
POST /api/profile/proposals/{id}/apply avec les éléments acceptés.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from . import entitlements, linkedin_export, pdf_import, tasks
from .analysis import analysis_input, analysis_proposals, compute_years, missing_topics
from .db import get_db
from .entitlements import RequirePro
from .llm import LLM, get_llm, require_llm
from .models import ProfileImport, User
from .profile import (
    ApplicationDefaults,
    ProfileData,
    ProfileOut,
    ProfilePatch,
    add_import_trace,
    get_profile,
    profile_data,
    profile_out,
    save_fields,
)
from .proposals import ProposalItem, apply_items, import_proposals, select_items
from .users import current_user

router = APIRouter(prefix="/api/profile", dependencies=[Depends(current_user)])

DB = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]
Gemini = Annotated[LLM | None, Depends(get_llm)]

IMPORT_TTL = timedelta(hours=24)
SOURCE_LABELS = {
    "linkedin_export": "Export LinkedIn", "pdf": "PDF", "text": "Texte collé",
    "analyse": "Analyse de votre profil", "feedback": "Synthèse de vos retours sur les offres",
}


@router.get("")
def read_profile(db: DB, user: Me) -> ProfileOut:
    return profile_out(get_profile(db, user))


@router.patch("")
def update_profile(body: ProfilePatch, db: DB, user: Me) -> ProfileOut:
    """Édition manuelle : les champs envoyés remplacent les valeurs actuelles.

    preferences sans application_defaults (formulaire « Préférences ») : les réponses types sont conservées.
    """
    profile = get_profile(db, user)
    fields = body.model_fields_set
    current = profile_data(profile)
    if "preferences" in fields and "application_defaults" not in body.preferences.model_fields_set:
        body.preferences = body.preferences.model_copy(
            update={"application_defaults": current.preferences.application_defaults})
    data = current.model_copy(update={f: getattr(body, f) for f in fields})
    changed = save_fields(profile, data, fields)
    db.commit()
    if changed:
        tasks.profile_changed(db, user.id)  # profil modifié : nouvelle présélection et réévaluation
    return profile_out(profile)


@router.put("/application-defaults")
def update_application_defaults(body: ApplicationDefaults, db: DB, user: Me) -> ProfileOut:
    """Réponses types pour les candidatures (remplacées en entier). Ne change pas la version du profil : aucune
    offre n'est réévaluée."""
    profile = get_profile(db, user)
    current = profile_data(profile)
    data = current.model_copy(update={"preferences": current.preferences.model_copy(
        update={"application_defaults": body})})
    save_fields(profile, data, {"preferences"})
    db.commit()
    return profile_out(profile)


# --- Propositions (imports, analyse, synthèse des règles) ---------------------------


class ProposalPreview(BaseModel):
    import_id: str  # identifiant de la proposition (nom historique)
    kind: str  # import | analyse | regles
    source: str
    source_label: str
    filename: str | None = None
    items: list[ProposalItem]
    questions: list[str] = []  # questions à poser pour compléter le profil (analyse)
    details: list[str] = []  # infos sur l'analyse (fichiers reconnus…)


class ImportText(BaseModel):
    text: Annotated[str, Field(min_length=30, max_length=pdf_import.MAX_IMPORT_TEXT_CHARS)]


class ProposalApply(BaseModel):
    # Identifiants des éléments acceptés. Absent : les éléments cochés par défaut.
    accept: list[str] | None = None


def _safe_filename(name: str | None) -> str | None:
    if not name:
        return None
    return name.replace("\\", "/").rsplit("/", 1)[-1][:255] or None


def preview_of(imp: ProfileImport) -> ProposalPreview:
    extra = imp.extra or {}
    return ProposalPreview(
        import_id=imp.id, kind=imp.kind, source=imp.source, source_label=SOURCE_LABELS.get(imp.source, imp.source),
        filename=imp.filename, items=[ProposalItem(**i) for i in imp.proposals or []],
        questions=extra.get("questions", []), details=extra.get("details", []),
    )


def create_proposal(
    db: Session, user: User, *, kind: str, source: str, items: list[dict], filename: str | None = None,
    data: ProfileData | None = None, extra: dict | None = None,
) -> ProposalPreview:
    # Ménage : propositions expirées ou déjà appliquées de cet utilisateur. Les imports ne sont pas supprimés mais
    # vidés de leur contenu : ils restent comptés dans le quota d'imports du plan Free (entitlements).
    done = (ProfileImport.user_id == user.id) & (
        (ProfileImport.applied_at.is_not(None)) | (ProfileImport.created_at < datetime.now(UTC) - IMPORT_TTL))
    db.execute(delete(ProfileImport).where(done, ProfileImport.kind != "import"))
    db.execute(update(ProfileImport).where(done, ProfileImport.kind == "import")
               .values(data=None, proposals=None, extra=None))
    imp = ProfileImport(
        id=str(uuid.uuid4()), user_id=user.id, kind=kind, source=source, filename=_safe_filename(filename),
        data=data.model_dump(mode="json") if data else None, proposals=items, extra=extra or {},
        created_at=datetime.now(UTC),
    )
    db.add(imp)
    db.commit()
    return preview_of(imp)


def create_import(
    db: Session, user: User, source: str, filename: str | None, data: ProfileData, details: list[str] | None = None
) -> ProposalPreview:
    items = import_proposals(profile_data(get_profile(db, user)), data)
    return create_proposal(db, user, kind="import", source=source, items=items, filename=filename, data=data,
                           extra={"details": details or []})


def _load_proposal(db: Session, user: User, proposal_id: str) -> ProfileImport:
    imp = db.scalar(select(ProfileImport).where(ProfileImport.id == proposal_id, ProfileImport.user_id == user.id))
    if imp is None or imp.applied_at is not None or (imp.extra or {}).get("cancelled"):
        raise HTTPException(404, "Proposition introuvable ou déjà appliquée")
    created = imp.created_at if imp.created_at.tzinfo else imp.created_at.replace(tzinfo=UTC)
    if created < datetime.now(UTC) - IMPORT_TTL:
        raise HTTPException(410, "Aperçu expiré, relancez l'opération")
    return imp


async def _read_upload(file: UploadFile, limit: int, label: str) -> bytes:
    data = await file.read(limit + 1)
    if not data:
        raise HTTPException(422, "Fichier vide")
    if len(data) > limit:
        raise HTTPException(413, f"{label} trop lourd ({limit // (1024 * 1024)} Mo max)")
    return data


@router.post("/import/linkedin-export")
async def import_linkedin_export(file: UploadFile, db: DB, user: Me) -> ProposalPreview:
    entitlements.check_quota(db, user, "imports")
    data = await _read_upload(file, linkedin_export.MAX_ZIP_BYTES, "Fichier")
    profile, found = linkedin_export.parse_export(data)
    details = [f"Fichiers reconnus : {', '.join(found)}"]
    return create_import(db, user, "linkedin_export", file.filename, profile, details)


@router.post("/import/pdf")
async def import_pdf(file: UploadFile, db: DB, user: Me, llm: Gemini) -> ProposalPreview:
    llm = require_llm(llm)
    entitlements.check_quota(db, user, "imports")
    data = await _read_upload(file, pdf_import.MAX_PDF_BYTES, "PDF")
    text = pdf_import.extract_text(data)
    profile = await run_in_threadpool(llm.extract_profile, text)
    return create_import(db, user, "pdf", file.filename, profile, [f"{len(text)} caractères analysés par Gemini"])


@router.post("/import/text")
def import_text(body: ImportText, db: DB, user: Me, llm: Gemini) -> ProposalPreview:
    llm = require_llm(llm)
    entitlements.check_quota(db, user, "imports")
    profile = llm.extract_profile(body.text)
    return create_import(db, user, "text", None, profile)


@router.get("/proposals/{proposal_id}")
def read_proposal(proposal_id: str, db: DB, user: Me) -> ProposalPreview:
    return preview_of(_load_proposal(db, user, proposal_id))


@router.post("/proposals/{proposal_id}/apply")
@router.post("/import/{proposal_id}/apply", include_in_schema=False)  # ancien chemin
def apply_proposal(proposal_id: str, db: DB, user: Me, body: ProposalApply | None = None) -> ProfileOut:
    """Applique les éléments acceptés, rejoués sur le profil actuel (rien n'est supprimé)."""
    imp = _load_proposal(db, user, proposal_id)
    profile = get_profile(db, user)
    accepted = select_items(imp.proposals or [], body.accept if body else None)
    merged, touched = apply_items(profile_data(profile), accepted)
    save_fields(profile, merged, touched)
    if imp.kind == "import":
        add_import_trace(profile, imp.source, imp.filename)
    imp.applied_at = datetime.now(UTC)
    db.commit()
    tasks.profile_changed(db, user.id)
    return profile_out(profile)


@router.delete("/proposals/{proposal_id}", status_code=204)
@router.delete("/import/{proposal_id}", status_code=204, include_in_schema=False)  # ancien chemin
def cancel_proposal(proposal_id: str, db: DB, user: Me) -> Response:
    imp = _load_proposal(db, user, proposal_id)
    if imp.kind == "import":  # conservé (vidé) : compte dans le quota d'imports
        imp.data, imp.proposals, imp.extra = None, None, {"cancelled": True}
    else:
        db.delete(imp)
    db.commit()
    return Response(status_code=204)


# --- Analyse du profil (déductions) ------------------------------------------------


@router.post("/analyze", dependencies=[RequirePro("profile_analysis")])
def analyze_profile(db: DB, user: Me, llm: Gemini) -> ProposalPreview:
    """Déductions sur le profil structuré -> propositions « déduit », validées une par une."""
    llm = require_llm(llm)
    current = profile_data(get_profile(db, user))
    if not current.experiences and not current.skills:
        raise HTTPException(422, "Profil trop vide pour être analysé : importez d'abord votre profil LinkedIn ou un CV.")
    analysis = llm.analyze_profile(analysis_input(current))
    items = analysis_proposals(current, analysis, compute_years(current.experiences))
    missing = missing_topics(current)
    questions = list(analysis.questions) or [f"Pouvez-vous préciser : {t} ?" for t in missing[:6]]
    return create_proposal(db, user, kind="analyse", source="analyse", items=items,
                           extra={"questions": questions[:8], "missing": missing})
