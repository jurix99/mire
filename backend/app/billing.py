"""Paiement de l'offre Pro par Stripe (/api/billing).

- POST /checkout : session Stripe Checkout (abonnement mensuel, ou Pass 3 mois en paiement unique). L'utilisateur
  coche d'abord dans l'app la renonciation au droit de rétractation (service numérique exécuté tout de suite) :
  elle est horodatée sur la ligne subscriptions créée « en attente » avec la session.
- POST /portal : Customer Portal Stripe (carte, résiliation à la fin de la période, factures).
- POST /webhook : SANS session ni cookie (nginx le laisse passer sans auth_request). Signature vérifiée avec
  STRIPE_WEBHOOK_SECRET sur le corps brut ; chaque événement est enregistré dans stripe_events (un événement déjà
  vu est ignoré). Seul ce webhook écrit les droits (subscriptions) ; entitlements.plan_of les lit.

Aucune clé n'est envoyée au navigateur ni écrite dans les journaux. Pas de clé publique : on redirige vers Checkout.
"""

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .db import get_db
from .entitlements import PASS_DURATION, aware
from .models import StripeEvent, Subscription, User
from .users import current_user

log = logging.getLogger("mire.billing")

router = APIRouter(prefix="/api/billing")  # pas de dépendance globale : le webhook n'a pas de session

DB = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]

PRICE_ENV = {"monthly": "STRIPE_PRICE_MONTHLY", "pass_3m": "STRIPE_PRICE_PASS_3M"}
# Statuts Stripe d'un abonnement encore en cours (un nouveau Checkout mensuel renvoie alors vers le portal)
LIVE_STATUSES = ("active", "trialing", "past_due", "unpaid")
PENDING = "checkout"  # ligne créée au départ vers Checkout, complétée par le webhook
WITHDRAWAL_TEXT = ("En payant, vous demandez l'accès immédiat à Mire Pro et renoncez expressément à votre droit "
                   "de rétractation (art. L221-28 du Code de la consommation).")


def public_url() -> str:
    return os.environ.get("PUBLIC_URL", "http://localhost:8080").rstrip("/")


def stripe_client() -> stripe.StripeClient:
    """Client Stripe (remplacé dans les tests)."""
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise HTTPException(503, "Paiement indisponible : STRIPE_SECRET_KEY n'est pas configurée sur le serveur")
    return stripe.StripeClient(key)


def _ts(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value else None


def _as_dict(obj: Any) -> dict:
    return obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)


# --- Checkout et portal ------------------------------------------------------------------------


class CheckoutIn(BaseModel):
    plan: Literal["monthly", "pass_3m"]
    # Case « je renonce à mon droit de rétractation » cochée dans l'app (obligatoire)
    waive_withdrawal: bool = False


class UrlOut(BaseModel):
    url: str
    portal: bool = False  # True : abonnement déjà actif, redirection vers le portal au lieu de Checkout


def _ensure_customer(db: Session, client: stripe.StripeClient, user: User) -> str:
    user = db.get(User, user.id)  # l'utilisateur de cette session (l'enregistrement de l'id doit persister)
    if user.stripe_customer_id:
        return user.stripe_customer_id
    params: dict[str, Any] = {"metadata": {"user_id": str(user.id)}}
    if user.email:
        params["email"] = user.email
    if user.name:
        params["name"] = user.name
    customer = client.v1.customers.create(params=params)
    user.stripe_customer_id = customer.id
    db.commit()
    return customer.id


def _portal_url(client: stripe.StripeClient, customer_id: str) -> str:
    session = client.v1.billing_portal.sessions.create(
        params={"customer": customer_id, "return_url": f"{public_url()}/mon-profil"})
    return session.url


@router.post("/checkout")
def checkout(body: CheckoutIn, db: DB, user: Me) -> UrlOut:
    if not body.waive_withdrawal:
        # Envoyé par le front au clic sur « Passer Pro », sous la mention de renonciation (CGV art. 6)
        raise HTTPException(422, "Demande d'accès immédiat et renonciation au droit de rétractation requises (CGV, art. 6)")
    price = os.environ.get(PRICE_ENV[body.plan], "")
    if not price:
        raise HTTPException(503, f"Paiement indisponible : {PRICE_ENV[body.plan]} n'est pas configuré sur le serveur")
    client = stripe_client()
    user = db.get(User, user.id)

    if body.plan == "monthly" and user.stripe_customer_id and db.scalar(select(Subscription.id).where(
            Subscription.user_id == user.id, Subscription.kind == "subscription",
            Subscription.status.in_(LIVE_STATUSES))):
        return UrlOut(url=_portal_url(client, user.stripe_customer_id), portal=True)

    customer_id = _ensure_customer(db, client, user)
    waived_at = datetime.now(UTC)
    metadata = {"user_id": str(user.id), "plan": body.plan, "withdrawal_waived_at": waived_at.isoformat()}
    params: dict[str, Any] = {
        "mode": "subscription" if body.plan == "monthly" else "payment",
        "customer": customer_id,
        "client_reference_id": str(user.id),
        "line_items": [{"price": price, "quantity": 1}],
        "automatic_tax": {"enabled": True},
        "customer_update": {"address": "auto", "name": "auto"},
        "allow_promotion_codes": True,
        "locale": "fr",
        "success_url": f"{public_url()}/mon-profil?checkout=success",
        "cancel_url": f"{public_url()}/mon-profil?checkout=cancel",
        "metadata": metadata,
        "custom_text": {"submit": {"message": WITHDRAWAL_TEXT}},
    }
    if body.plan == "monthly":
        params["subscription_data"] = {"metadata": metadata}
    else:
        params["payment_intent_data"] = {"metadata": metadata}
        params["invoice_creation"] = {"enabled": True}  # facture du Pass, visible dans le portal
    session = client.v1.checkout.sessions.create(params=params)

    db.add(Subscription(user_id=user.id, kind="subscription" if body.plan == "monthly" else "pass",
                        stripe_checkout_session_id=session.id, price_id=price, status=PENDING,
                        withdrawal_waived_at=waived_at))
    db.commit()
    return UrlOut(url=session.url)


@router.post("/portal")
def portal(db: DB, user: Me) -> UrlOut:
    user = db.get(User, user.id)
    if not user.stripe_customer_id:
        raise HTTPException(409, "Aucun abonnement Stripe pour ce compte")
    return UrlOut(url=_portal_url(stripe_client(), user.stripe_customer_id))


# --- Webhook -------------------------------------------------------------------------------------


def _user_for(db: Session, *, user_id: Any = None, customer: str | None = None) -> User | None:
    if user_id and str(user_id).isdigit() and (user := db.get(User, int(user_id))):
        return user
    if customer:
        return db.scalar(select(User).where(User.stripe_customer_id == customer))
    return None


def _pending_row(db: Session, user: User, kind: str, session_id: str | None = None) -> Subscription | None:
    if session_id and (row := db.scalar(select(Subscription).where(
            Subscription.stripe_checkout_session_id == session_id))):
        return row
    return db.scalars(select(Subscription).where(
        Subscription.user_id == user.id, Subscription.kind == kind, Subscription.status == PENDING,
    ).order_by(Subscription.id.desc())).first()


def sync_subscription(db: Session, sub: dict, session_id: str | None = None) -> Subscription | None:
    """Recopie l'état d'un abonnement lu depuis l'API Stripe (source de vérité, quel que soit l'ordre des événements)."""
    meta = sub.get("metadata") or {}
    user = _user_for(db, user_id=meta.get("user_id"), customer=sub.get("customer"))
    if user is None:
        log.warning("Abonnement %s : utilisateur introuvable", sub.get("id"))
        return None
    row = db.scalar(select(Subscription).where(Subscription.stripe_subscription_id == sub["id"])) \
        or _pending_row(db, user, "subscription", session_id)
    if row is None:
        row = Subscription(user_id=user.id, kind="subscription")
        db.add(row)
    items = (sub.get("items") or {}).get("data") or [{}]
    row.stripe_subscription_id = sub["id"]
    if session_id and not row.stripe_checkout_session_id:
        row.stripe_checkout_session_id = session_id
    row.price_id = (items[0].get("price") or {}).get("id") or row.price_id
    row.status = sub.get("status") or row.status
    # Selon la version de l'API, la fin de période est sur l'abonnement ou sur ses éléments
    row.current_period_end = _ts(sub.get("current_period_end") or items[0].get("current_period_end"))
    row.cancel_at_period_end = bool(sub.get("cancel_at_period_end") or sub.get("cancel_at"))
    if row.status == "past_due":
        row.past_due_since = row.past_due_since or datetime.now(UTC)
    else:
        row.past_due_since = None
    if row.status == "canceled" and sub.get("ended_at"):
        row.current_period_end = _ts(sub["ended_at"])
    if not user.stripe_customer_id and sub.get("customer"):
        user.stripe_customer_id = sub["customer"]
    return row


def _grant_pass(db: Session, user: User, session: dict) -> None:
    """Pass payé : 90 jours, à partir de la fin du Pass en cours s'il y en a un (prolongation)."""
    row = _pending_row(db, user, "pass", session["id"])
    if row is not None and row.status == "paid":
        return  # déjà accordé
    now = datetime.now(UTC)
    ends = [aware(p.current_period_end) for p in db.scalars(select(Subscription).where(
        Subscription.user_id == user.id, Subscription.kind == "pass", Subscription.status == "paid"))]
    start = max([e for e in ends if e and e > now], default=now)
    if row is None:
        row = Subscription(user_id=user.id, kind="pass", stripe_checkout_session_id=session["id"])
        db.add(row)
    row.status = "paid"
    row.current_period_end = start + PASS_DURATION
    row.stripe_payment_intent_id = session.get("payment_intent")
    row.cancel_at_period_end = False
    if not row.withdrawal_waived_at and (waived := (session.get("metadata") or {}).get("withdrawal_waived_at")):
        row.withdrawal_waived_at = datetime.fromisoformat(waived)


def _refund_pass(db: Session, charge: dict) -> None:
    """Pass remboursé : il prend fin maintenant ; les Pass achetés à sa suite sont avancés d'autant."""
    if not charge.get("refunded"):
        log.info("Remboursement partiel %s : droits inchangés", charge.get("id"))
        return
    row = db.scalar(select(Subscription).where(Subscription.kind == "pass",
                                               Subscription.stripe_payment_intent_id == charge.get("payment_intent")))
    if row is None or row.status != "paid":
        return
    now = datetime.now(UTC)
    end = aware(row.current_period_end) or now
    left = max(end - now, timedelta(0))
    if left:
        for later in db.scalars(select(Subscription).where(
                Subscription.user_id == row.user_id, Subscription.kind == "pass", Subscription.status == "paid",
                Subscription.id != row.id)):
            if (later_end := aware(later.current_period_end)) and later_end > end:
                later.current_period_end = later_end - left
    row.status, row.current_period_end = "refunded", now


def handle_event(db: Session, event: dict, client_factory=None) -> None:
    kind = event["type"]
    obj = event["data"]["object"]
    client_factory = client_factory or stripe_client
    match kind:
        case "checkout.session.completed":
            meta = obj.get("metadata") or {}
            user = _user_for(db, user_id=obj.get("client_reference_id") or meta.get("user_id"),
                             customer=obj.get("customer"))
            if user is None:
                log.warning("Checkout %s : utilisateur introuvable", obj.get("id"))
                return
            if obj.get("customer") and not user.stripe_customer_id:
                user.stripe_customer_id = obj["customer"]
            if obj.get("mode") == "payment":
                if obj.get("payment_status") == "paid":
                    _grant_pass(db, user, obj)
            elif obj.get("mode") == "subscription" and obj.get("subscription"):
                sub = _as_dict(client_factory().v1.subscriptions.retrieve(obj["subscription"]))
                sync_subscription(db, sub, session_id=obj.get("id"))
        case "customer.subscription.created" | "customer.subscription.updated" | "customer.subscription.deleted":
            sub = _as_dict(client_factory().v1.subscriptions.retrieve(obj["id"]))
            sync_subscription(db, sub)
        case "invoice.payment_failed":
            # Le statut (past_due, puis canceled ou unpaid) arrive avec customer.subscription.updated
            log.warning("Paiement échoué : facture %s, client %s", obj.get("id"), obj.get("customer"))
        case "charge.refunded":
            _refund_pass(db, obj)
        case _:
            log.info("Événement Stripe ignoré : %s", kind)


def process_webhook(db: Session, payload: bytes, signature: str | None) -> dict:
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "Webhook indisponible : STRIPE_WEBHOOK_SECRET n'est pas configuré")
    if not signature:
        raise HTTPException(400, "Signature Stripe absente")
    try:
        text = payload.decode("utf-8")
        stripe.WebhookSignature.verify_header(text, signature, secret, tolerance=300)
        event = json.loads(text)
    except (stripe.SignatureVerificationError, ValueError):
        raise HTTPException(400, "Signature Stripe invalide") from None

    db.add(StripeEvent(id=event["id"], type=event["type"], received_at=datetime.now(UTC)))
    try:
        db.flush()
    except IntegrityError:  # déjà reçu (Stripe rejoue un événement, ou deux livraisons simultanées)
        db.rollback()
        return {"received": True, "duplicate": True}
    try:
        handle_event(db, event)
        db.commit()  # l'événement n'est marqué « traité » qu'avec ses effets
    except Exception:
        db.rollback()
        log.exception("Webhook Stripe %s (%s) : échec, Stripe réessaiera", event["id"], event["type"])
        raise
    return {"received": True}


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request, db: DB) -> dict:
    payload = await request.body()  # corps brut : indispensable à la vérification de la signature
    return await run_in_threadpool(process_webhook, db, payload, request.headers.get("stripe-signature"))
