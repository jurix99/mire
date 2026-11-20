"""Présélection des offres envoyées à Gemini, et enchaînement des traitements de fond (job « daily »).

Pour un utilisateur (match_user) :
1. offres ouvertes sans évaluation pour la version actuelle de son profil (assessments.jobs_to_assess) ;
2. exclusions dures, sans LLM (assessments.hard_exclusion) : écartées tout de suite ;
3. tri par similarité entre l'embedding du profil et celui de l'offre (voir embeddings.py), et on garde les
   MATCH_TOP_K meilleures (50 par défaut) : seules celles-ci partent en évaluation Gemini. Les autres restent
   visibles dans la liste, sans note.

Offres Free / Pro : les utilisateurs Pro passent d'abord (daily : dans l'ordre ; file : tâches des Free
différées de FREE_DELAY).
"""

import logging
import os
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import assessments, tasks
from .models import User
from .profile import get_profile, profile_data

log = logging.getLogger("mire.matching")

MATCH_TOP_K = int(os.environ.get("MATCH_TOP_K", "50"))
FREE_DELAY = timedelta(minutes=2)


def users_by_priority(db: Session) -> list[tuple[User, assessments.Scope]]:
    """Utilisateurs dans l'ordre de service : Pro d'abord (priorité de leur offre), puis Free."""
    users = [(u, assessments.scope_of(db, u)) for u in db.scalars(select(User).order_by(User.id)).all()]
    return sorted(users, key=lambda pair: pair[1].priority)


def rank(db: Session, user: User, job_ids: list[int]) -> list[tuple[int, float]] | None:
    """Offres triées par similarité avec le profil (None : présélection indisponible, ex. sans embeddings)."""
    return None


def match_user(db: Session, user: User, top_k: int | None = None) -> dict:
    """Présélection pour l'utilisateur. Retourne {"job_ids": [...], "similarity": {job_id: similarité}}."""
    top_k = top_k or MATCH_TOP_K
    profile = get_profile(db, user)
    data = profile_data(profile)
    if not assessments.profile_is_usable(data):
        return {"job_ids": [], "similarity": {}}
    scope = assessments.scope_of(db, user)
    pending = assessments.jobs_to_assess(db, user, profile, before=scope.before)
    remaining = assessments.apply_hard_exclusions(db, user, profile, data, pending)
    ranked = rank(db, user, [j.id for j in remaining])
    if ranked is None:  # sans similarité : les plus récentes d'abord (comportement historique)
        return {"job_ids": [j.id for j in remaining][:top_k], "similarity": {}}
    ranked = ranked[:top_k]
    return {"job_ids": [job_id for job_id, _ in ranked], "similarity": {job_id: sim for job_id, sim in ranked}}


def enqueue_matches(db: Session) -> int:
    """Une tâche match_user par utilisateur (dédoublonnée) ; celles des Free sont différées."""
    count = 0
    for user, scope in users_by_priority(db):
        delay = FREE_DELAY if scope.priority > 0 else None
        if tasks.enqueue(db, "match_user", {"user_id": user.id}, dedup_key=f"match_user:{user.id}", delay=delay):
            count += 1
    return count


def assess_candidates(db: Session, user: User, llm, candidates: dict) -> dict:
    """Évaluation Gemini des seules offres présélectionnées (dans les limites de l'offre de l'utilisateur)."""
    job_ids = candidates.get("job_ids") or []
    if not job_ids:
        return {"evaluated": 0, "excluded": 0, "kept": 0, "remaining": 0}
    similarity = {int(k): float(v) for k, v in (candidates.get("similarity") or {}).items()}
    return assessments.assess_within_plan(db, user, llm, len(job_ids), job_ids=job_ids, similarity=similarity)
