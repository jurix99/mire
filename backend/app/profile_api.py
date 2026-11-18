"""API du profil utilisateur (/api/profile)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .profile import ProfileOut, ProfilePatch, get_profile, profile_data, profile_out, save_fields
from .users import current_user

router = APIRouter(prefix="/api/profile", dependencies=[Depends(current_user)])

DB = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]


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
