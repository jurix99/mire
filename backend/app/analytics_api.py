"""API des onglets « Marché » et « Toi face au marché » (page Analytics), et santé de la liste fermée (admin).

GET /api/analytics (suivi personnel, api.py) ne change pas. Tout est lu dans des faits précalculés (market.py) :
aucun appel au LLM pendant l'affichage. Masquage Free / Pro : voir mask_market / mask_me (entitlements).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from . import market
from . import taxonomy as tx
from .db import get_db
from .entitlements import RequireAdmin
from .models import User
from .users import current_user

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])

DB = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]


def _check(period: str, domain: str | None, family: str | None, count_by: str) -> None:
    if period not in market.PERIODS:
        raise HTTPException(422, f"Période inconnue : {period}")
    if domain and domain not in tx.DOMAINS:
        raise HTTPException(422, f"Domaine inconnu : {domain}")
    if family and family not in tx.FAMILY_LABEL:
        raise HTTPException(422, f"Famille de poste inconnue : {family}")
    if count_by not in market.COUNT_BY:
        raise HTTPException(422, "count_by : entreprise ou offre")


@router.get("/analytics/market")
def analytics_market(db: DB, user: Me, period: str = "90j", domain: str | None = None, family: str | None = None,
                     count_by: str = "entreprise") -> dict:
    """Indicateurs de l'onglet Marché, avec leurs effectifs et le périmètre réel."""
    domain, family = domain or None, family or None
    _check(period, domain, family, count_by)
    out = market.cached(("market", period, domain, family, count_by), lambda: market.market_overview(
        market.load_facts(db), period=period, domain=domain, family=family, count_by=count_by))
    return {**out, "options": market.options()}


@router.get("/analytics/me")
def analytics_me(db: DB, user: Me) -> dict:
    """Indicateurs de l'onglet « Toi face au marché »."""
    return market.me_overview(db, user, market.cached(("facts",), lambda: market.load_facts(db)))


@router.get("/admin/taxonomy-health", dependencies=[RequireAdmin])
def taxonomy_health(db: DB) -> dict:
    """Part d'offres en « autre », libellés hors liste les plus fréquents, compétences jamais rencontrées."""
    return market.taxonomy_health(db)
