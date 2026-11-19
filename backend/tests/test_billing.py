"""Offre Pro : calcul du plan (entitlements.plan_of), webhook Stripe, Checkout et portal (Stripe simulé, aucun réseau)."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import stripe
from sqlalchemy import func, select

from app import billing, entitlements
from app.models import StripeEvent, Subscription, User

SECRET = "whsec_test_secret"
NOW = datetime.now(UTC)


@pytest.fixture
def billing_on(monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "true")
    monkeypatch.delenv("PRO_USER_EMAILS", raising=False)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_jamais_utilisee")
    monkeypatch.setenv("STRIPE_PRICE_MONTHLY", "price_monthly")
    monkeypatch.setenv("STRIPE_PRICE_PASS_3M", "price_pass")


def add_sub(db, user, kind="subscription", **fields) -> Subscription:
    row = Subscription(user_id=user.id, kind=kind, **fields)
    db.add(row)
    db.commit()
    return row


def plan(db, user) -> str:
    db.expire_all()
    return entitlements.plan_of(db, db.get(User, user.id))


# --- plan_of ---------------------------------------------------------------------------------------


def test_billing_disabled_everyone_is_pro(db, alice, monkeypatch):
    monkeypatch.delenv("BILLING_ENABLED", raising=False)
    assert plan(db, alice) == "pro"
    monkeypatch.setenv("BILLING_ENABLED", "false")
    assert plan(db, alice) == "pro"


def test_no_subscription_is_free(db, alice, billing_on):
    assert plan(db, alice) == "free"
    assert entitlements.pro_until(db, alice) is None


def test_active_subscription_is_pro(db, alice, billing_on):
    add_sub(db, alice, status="active", current_period_end=NOW + timedelta(days=20))
    assert plan(db, alice) == "pro"


def test_past_due_grace_period(db, alice, bob, billing_on):
    add_sub(db, alice, status="past_due", past_due_since=NOW - timedelta(days=2),
            current_period_end=NOW - timedelta(days=2))
    add_sub(db, bob, status="past_due", past_due_since=NOW - timedelta(days=4),
            current_period_end=NOW - timedelta(days=4))
    assert plan(db, alice) == "pro"  # moins de 3 jours d'impayé
    assert plan(db, bob) == "free"  # plus de 3 jours


def test_cancelled_at_period_end_stays_pro_until_end(db, alice, bob, billing_on):
    add_sub(db, alice, status="active", cancel_at_period_end=True, current_period_end=NOW + timedelta(days=5))
    add_sub(db, bob, status="active", cancel_at_period_end=True, current_period_end=NOW - timedelta(days=1))
    assert plan(db, alice) == "pro"
    summary = entitlements.billing_summary(db, alice)
    assert summary["cancel_at_period_end"] is True and summary["kind"] == "subscription"
    # Fin de période passée : Stripe envoie customer.subscription.deleted, mais en attendant la date fait foi
    bob_sub = db.scalar(select(Subscription).where(Subscription.user_id == bob.id))
    bob_sub.status = "canceled"
    db.commit()
    assert plan(db, bob) == "free"


def test_ended_subscription_is_free(db, alice, billing_on):
    add_sub(db, alice, status="canceled", current_period_end=NOW - timedelta(days=1))
    add_sub(db, alice, status="incomplete_expired")
    assert plan(db, alice) == "free"


def test_pass_running_and_expired(db, alice, bob, billing_on):
    add_sub(db, alice, kind="pass", status="paid", current_period_end=NOW + timedelta(days=30))
    add_sub(db, bob, kind="pass", status="paid", current_period_end=NOW - timedelta(minutes=1))
    assert plan(db, alice) == "pro"
    assert plan(db, bob) == "free"
    add_sub(db, bob, kind="pass", status="refunded", current_period_end=NOW + timedelta(days=60))
    assert plan(db, bob) == "free"


def test_two_stacked_passes(db, alice, billing_on):
    add_sub(db, alice, kind="pass", status="paid", current_period_end=NOW + timedelta(days=10))
    add_sub(db, alice, kind="pass", status="paid", current_period_end=NOW + timedelta(days=100))
    assert plan(db, alice) == "pro"
    assert entitlements.pro_until(db, alice) > NOW + timedelta(days=99)


def test_pro_user_emails(db, alice, bob, billing_on, monkeypatch):
    alice.email = "Alice@Example.com"
    bob.email = "bob@example.com"
    db.commit()
    monkeypatch.setenv("PRO_USER_EMAILS", " alice@example.com , autre@example.com")
    assert plan(db, alice) == "pro"
    assert plan(db, bob) == "free"


def test_me_exposes_billing(alice, as_user, billing_on):
    b = as_user(alice).get("/api/me").json()["billing"]
    assert b["enabled"] is True and b["plan"] == "free"
    assert b["limits"] == entitlements.PLAN_LIMITS["free"]
    assert b["prices"]["monthly"] == entitlements.PLAN_LIMITS["pro"]["price_monthly_label"]
    assert set(b["usage"]) == {"chat", "imports", "processes", "assessments"}


# --- Stripe simulé ---------------------------------------------------------------------------------


class FakeStripe:
    """Imite StripeClient.v1 (customers, checkout.sessions, billing_portal.sessions, subscriptions)."""

    def __init__(self):
        self.subscriptions_data: dict[str, dict] = {}
        self.sessions: list[dict] = []
        self.customers: list[dict] = []
        self.v1 = SimpleNamespace(
            customers=SimpleNamespace(create=self._customer),
            checkout=SimpleNamespace(sessions=SimpleNamespace(create=self._session)),
            billing_portal=SimpleNamespace(sessions=SimpleNamespace(
                create=lambda params: SimpleNamespace(url="https://billing.stripe.test/portal"))),
            subscriptions=SimpleNamespace(retrieve=lambda sid: self.subscriptions_data[sid]),
        )

    def _customer(self, params):
        self.customers.append(params)
        return SimpleNamespace(id=f"cus_{len(self.customers)}")

    def _session(self, params):
        self.sessions.append(params)
        return SimpleNamespace(id=f"cs_{len(self.sessions)}", url=f"https://checkout.stripe.test/{len(self.sessions)}")


@pytest.fixture
def fake_stripe(monkeypatch, billing_on):
    fake = FakeStripe()
    monkeypatch.setattr(billing, "stripe_client", lambda: fake)
    return fake


def send(client, event: dict, secret: str = SECRET):
    payload = json.dumps(event)
    header = stripe.WebhookSignature.generate_signature_header(payload, secret)
    return client.post("/api/billing/webhook", content=payload,
                       headers={"Stripe-Signature": header, "Content-Type": "application/json"})


def event(evt_id: str, kind: str, obj: dict) -> dict:
    return {"id": evt_id, "object": "event", "type": kind, "data": {"object": obj}}


def pass_session(user, session_id="cs_pass_1", payment_intent="pi_1") -> dict:
    return {"id": session_id, "object": "checkout.session", "mode": "payment", "payment_status": "paid",
            "client_reference_id": str(user.id), "customer": "cus_1", "payment_intent": payment_intent,
            "metadata": {"user_id": str(user.id), "plan": "pass_3m"}}


def stripe_sub(user, **fields) -> dict:
    end = int((NOW + timedelta(days=30)).timestamp())
    return {"id": "sub_1", "object": "subscription", "customer": "cus_1", "status": "active",
            "cancel_at_period_end": False, "cancel_at": None, "ended_at": None,
            "metadata": {"user_id": str(user.id)},
            "items": {"data": [{"price": {"id": "price_monthly"}, "current_period_end": end}]}, **fields}


# --- Webhook ---------------------------------------------------------------------------------------


def test_webhook_rejects_invalid_signature(db, alice, as_user, fake_stripe):
    client = as_user(alice)
    r = send(client, event("evt_bad", "checkout.session.completed", pass_session(alice)), secret="whsec_autre")
    assert r.status_code == 400
    r = client.post("/api/billing/webhook", content=b"{}", headers={"Stripe-Signature": "t=1,v1=abc"})
    assert r.status_code == 400
    assert client.post("/api/billing/webhook", content=b"{}").status_code == 400  # signature absente
    assert db.scalar(select(func.count(StripeEvent.id))) == 0
    assert plan(db, alice) == "free"


def test_webhook_same_event_twice_processed_once(db, alice, as_user, fake_stripe):
    client = as_user(alice)
    evt = event("evt_1", "checkout.session.completed", pass_session(alice))
    assert send(client, evt).json() == {"received": True}
    assert send(client, evt).json() == {"received": True, "duplicate": True}
    passes = list(db.scalars(select(Subscription).where(Subscription.user_id == alice.id)))
    assert len(passes) == 1
    end = entitlements.aware(passes[0].current_period_end)
    assert NOW + timedelta(days=89) < end < NOW + timedelta(days=91)
    assert plan(db, alice) == "pro"


def test_webhook_pass_bought_during_pass_extends_it(db, alice, as_user, fake_stripe):
    client = as_user(alice)
    send(client, event("evt_1", "checkout.session.completed", pass_session(alice, "cs_a", "pi_a")))
    send(client, event("evt_2", "checkout.session.completed", pass_session(alice, "cs_b", "pi_b")))
    until = entitlements.pro_until(db, db.get(User, alice.id))
    assert NOW + timedelta(days=179) < until < NOW + timedelta(days=181)

    # Remboursement total du premier Pass : il prend fin, le second est avancé de la durée restante
    send(client, event("evt_3", "charge.refunded", {"id": "ch_a", "payment_intent": "pi_a", "refunded": True}))
    db.expire_all()
    until = entitlements.pro_until(db, db.get(User, alice.id))
    assert NOW + timedelta(days=89) < until < NOW + timedelta(days=91)


def test_webhook_refund_ends_pass(db, alice, as_user, fake_stripe):
    client = as_user(alice)
    send(client, event("evt_1", "checkout.session.completed", pass_session(alice)))
    send(client, event("evt_2", "charge.refunded", {"id": "ch_1", "payment_intent": "pi_1", "refunded": True}))
    assert plan(db, alice) == "free"


def test_webhook_subscription_cancelled_then_deleted(db, alice, as_user, fake_stripe):
    client = as_user(alice)
    fake_stripe.subscriptions_data["sub_1"] = stripe_sub(alice)
    assert send(client, event("evt_1", "customer.subscription.created", {"id": "sub_1"})).status_code == 200
    assert plan(db, alice) == "pro"

    # Résiliation dans le portal : reste Pro jusqu'à la fin de la période
    fake_stripe.subscriptions_data["sub_1"] = stripe_sub(alice, cancel_at_period_end=True)
    send(client, event("evt_2", "customer.subscription.updated", {"id": "sub_1"}))
    me = client.get("/api/me").json()["billing"]
    assert me["plan"] == "pro" and me["cancel_at_period_end"] is True and me["kind"] == "subscription"
    assert me["pro_until"] is not None

    # Fin de période : l'abonnement est supprimé
    fake_stripe.subscriptions_data["sub_1"] = stripe_sub(alice, status="canceled", cancel_at_period_end=True,
                                                         ended_at=int(NOW.timestamp()))
    send(client, event("evt_3", "customer.subscription.deleted", {"id": "sub_1"}))
    assert plan(db, alice) == "free"
    assert db.scalar(select(func.count(Subscription.id))) == 1  # même ligne mise à jour


def test_webhook_events_out_of_order_use_stripe_state(db, alice, as_user, fake_stripe):
    """Un vieil événement « created » rejoué après la résiliation relit l'état réel chez Stripe."""
    client = as_user(alice)
    fake_stripe.subscriptions_data["sub_1"] = stripe_sub(alice, status="canceled", ended_at=int(NOW.timestamp()))
    send(client, event("evt_1", "customer.subscription.created", {"id": "sub_1", "status": "active"}))
    assert plan(db, alice) == "free"


def test_webhook_past_due_then_recovered(db, alice, as_user, fake_stripe):
    client = as_user(alice)
    fake_stripe.subscriptions_data["sub_1"] = stripe_sub(alice, status="past_due")
    send(client, event("evt_1", "customer.subscription.updated", {"id": "sub_1"}))
    send(client, event("evt_2", "invoice.payment_failed", {"id": "in_1", "customer": "cus_1"}))
    assert plan(db, alice) == "pro"  # délai de grâce
    row = db.scalar(select(Subscription))
    assert row.past_due_since is not None
    fake_stripe.subscriptions_data["sub_1"] = stripe_sub(alice, status="active")
    send(client, event("evt_3", "customer.subscription.updated", {"id": "sub_1"}))
    db.expire_all()
    assert db.scalar(select(Subscription)).past_due_since is None


# --- Checkout et portal ----------------------------------------------------------------------------


def test_checkout_requires_withdrawal_waiver(alice, as_user, fake_stripe):
    r = as_user(alice).post("/api/billing/checkout", json={"plan": "monthly"})
    assert r.status_code == 422
    assert not fake_stripe.sessions


def test_checkout_monthly_and_pass(db, alice, as_user, fake_stripe):
    client = as_user(alice)
    r = client.post("/api/billing/checkout", json={"plan": "monthly", "waive_withdrawal": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"url": "https://checkout.stripe.test/1", "portal": False}
    params = fake_stripe.sessions[0]
    assert params["mode"] == "subscription" and params["customer"] == "cus_1"
    assert params["client_reference_id"] == str(alice.id)
    assert params["automatic_tax"] == {"enabled": True}
    assert params["customer_update"]["address"] == "auto"
    assert params["allow_promotion_codes"] is True
    assert params["line_items"] == [{"price": "price_monthly", "quantity": 1}]
    assert params["success_url"].endswith("/mon-profil?checkout=success")
    assert len(fake_stripe.customers) == 1

    r = client.post("/api/billing/checkout", json={"plan": "pass_3m", "waive_withdrawal": True})
    params = fake_stripe.sessions[1]
    assert params["mode"] == "payment" and params["line_items"][0]["price"] == "price_pass"
    assert len(fake_stripe.customers) == 1  # client Stripe réutilisé

    rows = list(db.scalars(select(Subscription).order_by(Subscription.id)))
    assert [(s.kind, s.status) for s in rows] == [("subscription", "checkout"), ("pass", "checkout")]
    assert all(s.withdrawal_waived_at is not None for s in rows)  # renonciation horodatée
    assert plan(db, alice) == "free"  # rien n'est accordé avant le webhook


def test_checkout_monthly_with_live_subscription_returns_portal(db, alice, as_user, fake_stripe):
    alice.stripe_customer_id = "cus_9"
    db.commit()
    add_sub(db, alice, status="active", stripe_subscription_id="sub_9", current_period_end=NOW + timedelta(days=9))
    r = as_user(alice).post("/api/billing/checkout", json={"plan": "monthly", "waive_withdrawal": True})
    assert r.json() == {"url": "https://billing.stripe.test/portal", "portal": True}
    assert not fake_stripe.sessions


def test_portal(db, alice, as_user, fake_stripe):
    client = as_user(alice)
    assert client.post("/api/billing/portal").status_code == 409
    alice.stripe_customer_id = "cus_1"
    db.commit()
    assert client.post("/api/billing/portal").json()["url"] == "https://billing.stripe.test/portal"


def test_checkout_completed_links_pending_subscription(db, alice, as_user, fake_stripe):
    client = as_user(alice)
    client.post("/api/billing/checkout", json={"plan": "monthly", "waive_withdrawal": True})
    fake_stripe.subscriptions_data["sub_1"] = stripe_sub(alice)
    session = {"id": "cs_1", "object": "checkout.session", "mode": "subscription", "subscription": "sub_1",
               "client_reference_id": str(alice.id), "customer": "cus_1"}
    send(client, event("evt_1", "checkout.session.completed", session))
    rows = list(db.scalars(select(Subscription)))
    assert len(rows) == 1
    assert rows[0].status == "active" and rows[0].stripe_subscription_id == "sub_1"
    assert rows[0].withdrawal_waived_at is not None
    assert plan(db, alice) == "pro"
