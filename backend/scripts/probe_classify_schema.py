"""Diagnostic : envoie UNE offre au classement Gemini et affiche l'erreur exacte (schéma refusé, quota…).

  docker compose exec backend uv run python scripts/probe_classify_schema.py
"""

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import classify
from app.db import SessionLocal
from app.llm import GEMINI_EVAL_MODELS, get_llm
from app.models import Job

llm = get_llm()
with SessionLocal() as db:
    job = db.scalars(select(Job).options(selectinload(Job.company)).limit(1)).first()
    t = llm.types
    for model in GEMINI_EVAL_MODELS:
        try:
            r = llm.client.models.generate_content(
                model=model, contents=f"<offres>[{classify.payload(job)}]</offres>",
                config=t.GenerateContentConfig(system_instruction=classify.instruction(),
                                               response_mime_type="application/json",
                                               response_json_schema=classify.llm_schema(), max_output_tokens=4000))  # noqa: E501
            print(model, "OK", (r.text or "")[:300])
        except Exception as exc:  # noqa: BLE001
            print(model, "ERREUR", type(exc).__name__, str(exc)[:800])
