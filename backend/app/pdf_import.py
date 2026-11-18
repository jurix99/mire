"""Extraction du texte d'un PDF (profil LinkedIn « Enregistrer au format PDF » ou CV), en mémoire."""

import io
import logging

from fastapi import HTTPException

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PAGES = 30
MAX_TEXT_CHARS = 60_000
MAX_IMPORT_TEXT_CHARS = 60_000

log = logging.getLogger(__name__)


def extract_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, "PDF trop lourd (10 Mo max)")
    if not data.startswith(b"%PDF"):
        raise HTTPException(422, "Ce fichier n'est pas un PDF")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise HTTPException(422, "PDF protégé par mot de passe : non pris en charge")
        chunks, size = [], 0
        for page in reader.pages[:MAX_PAGES]:
            text = page.extract_text() or ""
            chunks.append(text)
            size += len(text)
            if size >= MAX_TEXT_CHARS:
                break
    except HTTPException:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        log.info("PDF illisible: %s", exc)
        raise HTTPException(422, "PDF illisible") from None
    text = "\n".join(chunks).strip()[:MAX_TEXT_CHARS]
    if len(text) < 30:
        raise HTTPException(422, "Aucun texte trouvé dans ce PDF (document scanné ?). Essayez de coller le texte.")
    return text
