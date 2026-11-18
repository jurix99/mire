"""API du profil utilisateur (/api/profile) et imports (export LinkedIn, PDF, texte collé).

Import en deux temps : l'analyse renvoie un APERÇU (import_id + changements), rien n'est enregistré
dans le profil avant POST /api/profile/import/{import_id}/apply.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from . import linkedin_export, pdf_import
from .db import get_db
from .llm import LLM, get_llm, require_llm
from .models import ProfileImport, User
from .profile import (
    ProfileData,
    ProfileOut,
    ProfilePatch,
    add_import_trace,
    apply_import,
    get_profile,
    merge_profile,
    profile_data,
    profile_out,
    save_fields,
)
from .users import current_user

router = APIRouter(prefix="/api/profile", dependencies=[Depends(current_user)])

DB = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]
Gemini = Annotated[LLM | None, Depends(get_llm)]

IMPORT_TTL = timedelta(hours=24)
SOURCE_LABELS = {"linkedin_export": "Export LinkedIn", "pdf": "PDF", "text": "Texte collé"}


@router.get("")
def read_profile(db: DB, user: Me) -> ProfileOut:
    return profile_out(get_profile(db, user))


@router.patch("")
def update_profile(body: ProfilePatch, db: DB, user: Me) -> ProfileOut:
    """Édition manuelle : les champs envoyés remplacent les valeurs actuelles."""
    profile = get_profile(db, user)
    fields = body.model_fields_set
    data = profile_data(profile).model_copy(update={f: getattr(body, f) for f in fields})
    save_fields(profile, data, fields)
    db.commit()
    return profile_out(profile)


# --- Imports -----------------------------------------------------------------


class ImportChange(BaseModel):
    field: str
    label: str
    action: str  # ajout | modification
    before: object | None = None
    after: object | None = None
    items: list[str] = []


class ImportPreview(BaseModel):
    import_id: str
    source: str
    source_label: str
    filename: str | None
    changes: list[ImportChange]
    details: list[str] = []  # infos sur l'analyse (fichiers reconnus…)


class ImportText(BaseModel):
    text: Annotated[str, Field(min_length=30, max_length=pdf_import.MAX_IMPORT_TEXT_CHARS)]


class ImportApply(BaseModel):
    # Champs à appliquer. Absent : tous les ajouts, aucune modification d'une valeur existante.
    accept: list[str] | None = None


def _safe_filename(name: str | None) -> str | None:
    if not name:
        return None
    return name.replace("\\", "/").rsplit("/", 1)[-1][:255] or None


def create_import(
    db: Session, user: User, source: str, filename: str | None, data: ProfileData, details: list[str] | None = None
) -> ImportPreview:
    # Ménage : imports expirés ou déjà appliqués de cet utilisateur
    db.execute(delete(ProfileImport).where(
        ProfileImport.user_id == user.id,
        (ProfileImport.applied_at.is_not(None)) | (ProfileImport.created_at < datetime.now(UTC) - IMPORT_TTL),
    ))
    current = profile_data(get_profile(db, user))
    _, changes = merge_profile(current, data)
    imp = ProfileImport(
        id=str(uuid.uuid4()), user_id=user.id, source=source, filename=_safe_filename(filename),
        data=data.model_dump(mode="json"), created_at=datetime.now(UTC),
    )
    db.add(imp)
    db.commit()
    return ImportPreview(
        import_id=imp.id, source=source, source_label=SOURCE_LABELS[source], filename=imp.filename,
        changes=[ImportChange(**c) for c in changes], details=details or [],
    )


def _load_import(db: Session, user: User, import_id: str) -> ProfileImport:
    imp = db.scalar(select(ProfileImport).where(ProfileImport.id == import_id, ProfileImport.user_id == user.id))
    if imp is None or imp.applied_at is not None:
        raise HTTPException(404, "Import introuvable ou déjà appliqué")
    created = imp.created_at if imp.created_at.tzinfo else imp.created_at.replace(tzinfo=UTC)
    if created < datetime.now(UTC) - IMPORT_TTL:
        raise HTTPException(410, "Aperçu expiré, relancez l'import")
    return imp


async def _read_upload(file: UploadFile, limit: int, label: str) -> bytes:
    data = await file.read(limit + 1)
    if not data:
        raise HTTPException(422, "Fichier vide")
    if len(data) > limit:
        raise HTTPException(413, f"{label} trop lourd ({limit // (1024 * 1024)} Mo max)")
    return data


@router.post("/import/linkedin-export")
async def import_linkedin_export(file: UploadFile, db: DB, user: Me) -> ImportPreview:
    data = await _read_upload(file, linkedin_export.MAX_ZIP_BYTES, "Fichier")
    profile, found = linkedin_export.parse_export(data)
    details = [f"Fichiers reconnus : {', '.join(found)}"]
    return create_import(db, user, "linkedin_export", file.filename, profile, details)


@router.post("/import/pdf")
async def import_pdf(file: UploadFile, db: DB, user: Me, llm: Gemini) -> ImportPreview:
    llm = require_llm(llm)
    data = await _read_upload(file, pdf_import.MAX_PDF_BYTES, "PDF")
    text = pdf_import.extract_text(data)
    profile = await run_in_threadpool(llm.extract_profile, text)
    return create_import(db, user, "pdf", file.filename, profile, [f"{len(text)} caractères analysés par Gemini"])


@router.post("/import/text")
def import_text(body: ImportText, db: DB, user: Me, llm: Gemini) -> ImportPreview:
    profile = require_llm(llm).extract_profile(body.text)
    return create_import(db, user, "text", None, profile)


@router.post("/import/{import_id}/apply")
def apply_profile_import(import_id: str, db: DB, user: Me, body: ImportApply | None = None) -> ProfileOut:
    imp = _load_import(db, user, import_id)
    profile = get_profile(db, user)
    accept = set(body.accept) if body and body.accept is not None else None
    merged, touched = apply_import(profile_data(profile), ProfileData.model_validate(imp.data), accept)
    save_fields(profile, merged, touched)
    add_import_trace(profile, imp.source, imp.filename)
    imp.applied_at = datetime.now(UTC)
    db.commit()
    return profile_out(profile)


@router.delete("/import/{import_id}", status_code=204)
def cancel_profile_import(import_id: str, db: DB, user: Me) -> Response:
    db.delete(_load_import(db, user, import_id))
    db.commit()
    return Response(status_code=204)
