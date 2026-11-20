"""Utilisateur courant : session LinkedIn -> ligne de la table users."""

import os
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

# Données de démo (fausses offres de la maquette, process de démo) : désactivées par défaut
SEED_DEMO = os.environ.get("SEED_DEMO", "false").lower() in ("1", "true", "yes")

# Accès sans login (port 8081, local uniquement) : nginx ajoute l'en-tête X-Dev-User sur ce port
# et le supprime sur le port public. Le backend n'en tient compte que si cette variable est activée.
DEV_LOGIN_BYPASS = os.environ.get("DEV_LOGIN_BYPASS", "").lower() in ("1", "true", "yes")
DEV_USER = {"sub": "dev-local", "name": "Julien (dev)", "given_name": "Julien", "family_name": None, "email": None}

PROFILE_FIELDS = ("name", "given_name", "family_name", "email", "picture", "locale")


def normalize_locale(value) -> str | None:
    """`locale` OIDC : "fr_FR", "fr-FR" ou {"language": "fr", "country": "FR"} -> "fr_FR"."""
    if isinstance(value, dict):
        value = "_".join(filter(None, [value.get("language"), value.get("country")]))
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().replace("-", "_")[:20]


def upsert_user(db: Session, info: dict) -> User:
    """Crée ou met à jour l'utilisateur à partir du profil OpenID Connect."""
    user = db.scalar(select(User).where(User.linkedin_sub == info["sub"]))
    is_new = user is None
    if is_new:
        user = User(linkedin_sub=info["sub"])
        db.add(user)
    for field in PROFILE_FIELDS:
        if field in info:
            setattr(user, field, normalize_locale(info[field]) if field == "locale" else info[field])
    user.last_login_at = datetime.now(UTC)
    db.commit()

    if is_new and SEED_DEMO:
        from .seed import seed_demo_applications

        seed_demo_applications(db, user)
    return user


def current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User:
    info = request.session.get("user")
    from_session = info is not None
    if not info and DEV_LOGIN_BYPASS and request.headers.get("x-dev-user") == "1":
        info = DEV_USER
    if not info or not info.get("sub"):
        raise HTTPException(401, "Non connecté")

    user_id = request.session.get("user_id") if from_session else None
    user = db.get(User, user_id) if user_id else None
    if user is None or user.linkedin_sub != info["sub"]:
        user = upsert_user(db, info)
        if from_session:
            request.session["user_id"] = user.id
    return user


CurrentUser = Annotated[User, Depends(current_user)]
