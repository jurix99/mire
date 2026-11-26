"""API des onglets « Marché » et « Toi face au marché » (page Analytics), et santé de la liste fermée (admin).

GET /api/analytics (suivi personnel, api.py) ne change pas. Tout est lu dans des faits précalculés (market.py) :
aucun appel au LLM pendant l'affichage. Masquage Free / Pro : voir mask_market / mask_me (entitlements).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from . import engine_health, entitlements, market
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
    """Indicateurs de l'onglet Marché, avec leurs effectifs et le périmètre réel.

    Free (PLAN_LIMITS market_*) : période ramenée à 90 jours, listes courtes, indicateurs réservés floutés."""
    domain, family = domain or None, family or None
    _check(period, domain, family, count_by)
    plan = entitlements.plan_of(db, user)
    limits = entitlements.limits(plan)
    allowed = limits["market_periods"]
    period_locked = allowed is not None and period not in allowed
    if period_locked:
        period = allowed[0]
    out = market.cached(("market", period, domain, family, count_by), lambda: market.market_overview(
        market.load_facts(db), period=period, domain=domain, family=family, count_by=count_by,
        limit=market.FULL_LIST))
    out = market.mask_market(out, limits)
    return {**out, "plan": plan, "period_locked": period_locked, "allowed_periods": allowed,
            "options": market.options()}


@router.get("/analytics/me")
def analytics_me(db: DB, user: Me) -> dict:
    """Indicateurs de l'onglet « Toi face au marché » (Pro) ; Free : aperçu générique flouté."""
    plan = entitlements.plan_of(db, user)
    if not entitlements.limits(plan)["market_me"]:
        return {**market.locked_me(), "plan": plan}
    return {**market.me_overview(db, user, market.cached(("facts",), lambda: market.load_facts(db))), "plan": plan,
            "locked": False}


@router.get("/admin/taxonomy-health", dependencies=[RequireAdmin])
def taxonomy_health(db: DB) -> dict:
    """Part d'offres en « autre », libellés hors liste les plus fréquents, compétences jamais rencontrées."""
    return market.taxonomy_health(db)


@router.get("/admin/engine-health", dependencies=[RequireAdmin])
def engine_health_report(db: DB) -> dict:
    """Santé du moteur sur 14 jours : offres, évaluations (bascule de modèle, délai), file, retours, passages,
    alertes (engine_health.py)."""
    return engine_health.engine_health(db)


@router.get("/admin/beta", dependencies=[RequireAdmin])
def beta_report(db: DB) -> dict:
    """Pertinence par utilisateur (bêta) : précision sur ses offres les mieux notées qu'il a jugées."""
    return engine_health.beta_report(db)
