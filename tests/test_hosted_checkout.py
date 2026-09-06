"""Hosted checkout adapter and seller repricing.

Two properties carry the weight: a webhook cannot settle anything on its own,
and an unrecognised processor status is never read as payment.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from keystone.api import Deps, create_app
from keystone.auth import Principal, StaticTokenAuth
from keystone.db import Store
from keystone.payments import (
    ChargeStatus,
    Currency,
    DemoChainProvider,
    Money,
    RefundNotSupported,
)
from keystone.providers.hosted_checkout import (
    CheckoutConfig,
    CheckoutError,
    HostedCheckoutProvider,
    verify_webhook,
)
from keystone.storage import LocalStore, artifact_key

SECRET = "whsec_test"
PRICE = Money(25_000_000, Currency.USDC)
CREATOR = Principal("u_creator", "creator@example.com")
OTHER = Principal("u_other", "other@example.com")
FILES = [{"path": "config.json", "size_bytes": 4, "sha256": "a" * 64}]


def provider(handler) -> HostedCheckoutProvider:
    config = CheckoutConfig(
        api_key="key", base_url="https://pay.invalid", webhook_secret=SECRET
    )
    client = httpx.Client(base_url=config.base_url, transport=httpx.MockTransport(handler))
    return HostedCheckoutProvider(config, client=client)


def route(payload: dict, status: int = 200):
    return lambda request: httpx.Response(status, json=payload)


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------
# charges
# --------------------------------------------------------------------------

def test_create_charge_maps_the_hosted_page() -> None:
    p = provider(route({
        "id": "ch_1", "reference": "ord_1", "amount": "25.00", "currency": "USDC",
        "status": "new", "hosted_url": "https://pay.invalid/ch_1",
        "address": "0xabc", "chain": "base", "required_confirmations": 3,
    }))
    charge = p.create_charge(PRICE, "ord_1")
    assert charge.charge_id == "ch_1"
    assert charge.status is ChargeStatus.PENDING
    assert charge.checkout_url.endswith("/ch_1")
    assert charge.amount == PRICE


def test_amounts_convert_to_minor_units() -> None:
    """USDC has six decimals; a float here would be a rounding bug."""
    p = provider(route({"id": "c", "amount": "0.000001", "currency": "USDC", "status": "new"}))
    assert p.get_charge("c").amount.amount_minor == 1


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("new", ChargeStatus.PENDING),
        ("confirming", ChargeStatus.CONFIRMING),
        ("completed", ChargeStatus.SETTLED),
        ("resolved", ChargeStatus.SETTLED),
        ("expired", ChargeStatus.EXPIRED),
        ("failed", ChargeStatus.FAILED),
    ],
)
def test_status_vocabulary(raw: str, expected: ChargeStatus) -> None:
    p = provider(route({"id": "c", "amount": "1", "currency": "USDC", "status": raw}))
    assert p.get_charge("c").status is expected


def test_unknown_status_is_never_read_as_paid() -> None:
    """A vocabulary change at the vendor must not hand away the catalogue."""
    p = provider(route({"id": "c", "amount": "1", "currency": "USDC", "status": "brand_new"}))
    charge = p.get_charge("c")
    assert charge.status is ChargeStatus.PENDING and not charge.is_settled


def test_processor_errors_surface_clearly() -> None:
    with pytest.raises(CheckoutError, match="402"):
        provider(route({"error": "nope"}, status=402)).get_charge("c")


def test_unreachable_processor_is_an_error_not_a_settlement() -> None:
    def boom(request):
        raise httpx.ConnectError("network down")

    with pytest.raises(CheckoutError, match="unreachable"):
        provider(boom).get_charge("c")


def test_a_response_without_a_charge_id_is_rejected() -> None:
    p = provider(route({"amount": "1", "currency": "USDC", "status": "completed"}))
    with pytest.raises(CheckoutError, match="no charge id"):
        p.get_charge("c")


def test_refunds_are_refused() -> None:
    p = provider(route({"id": "c", "amount": "1", "currency": "USDC", "status": "new"}))
    with pytest.raises(RefundNotSupported):
        p.refund("c")


# --------------------------------------------------------------------------
# webhook signature
# --------------------------------------------------------------------------

def test_valid_signature_accepted() -> None:
    body = b'{"data":{"id":"ch_1"}}'
    assert verify_webhook(SECRET, body, sign(body))
    assert verify_webhook(SECRET, body, "sha256=" + sign(body))


def test_tampered_body_rejected() -> None:
    body = b'{"data":{"id":"ch_1"}}'
    assert not verify_webhook(SECRET, b'{"data":{"id":"ch_EVIL"}}', sign(body))


def test_wrong_secret_rejected() -> None:
    body = b'{"data":{"id":"ch_1"}}'
    assert not verify_webhook(SECRET, body, sign(body, "attacker"))


def test_missing_pieces_rejected() -> None:
    assert not verify_webhook("", b"{}", sign(b"{}"))
    assert not verify_webhook(SECRET, b"{}", "")


def test_only_the_charge_id_is_taken_from_a_callback() -> None:
    p = provider(route({"id": "ch_1", "amount": "1", "currency": "USDC", "status": "new"}))
    assert p.charge_id_from_webhook({"data": {"id": "ch_1", "status": "completed"}}) == "ch_1"
    assert p.charge_id_from_webhook({"charge_id": "ch_2"}) == "ch_2"
    assert p.charge_id_from_webhook({"status": "completed"}) is None


# --------------------------------------------------------------------------
# webhook endpoint
# --------------------------------------------------------------------------

def _client(handler) -> TestClient:
    store = Store("sqlite://")
    store.create_all()
    return TestClient(create_app(Deps(
        store, LocalStore("./unused"), provider(handler), StaticTokenAuth({})
    )))


def test_webhook_requires_a_valid_signature() -> None:
    client = _client(route({"id": "ch_1", "amount": "1", "currency": "USDC",
                            "status": "completed"}))
    body = json.dumps({"data": {"id": "ch_1"}}).encode()
    assert client.post("/v1/webhooks/payments", content=body).status_code == 401
    assert client.post(
        "/v1/webhooks/payments", content=body,
        headers={"x-webhook-signature": "deadbeef"},
    ).status_code == 401


def test_a_lying_webhook_cannot_settle_a_charge() -> None:
    """Signed, well formed, claiming payment -- and the processor says no.

    The callback is only ever a prompt to re-read; its own claims are discarded.
    """
    client = _client(route({
        "id": "ch_1", "reference": "ord_1", "amount": "25.00",
        "currency": "USDC", "status": "new",   # processor: still unpaid
    }))
    body = json.dumps({"data": {"id": "ch_1", "status": "completed",
                                "amount": "25.00"}}).encode()
    response = client.post(
        "/v1/webhooks/payments", content=body, headers={"x-webhook-signature": sign(body)}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "pending"  # not what the payload claimed


def test_webhook_records_a_genuine_settlement() -> None:
    client = _client(route({
        "id": "ch_1", "reference": "ord_1", "amount": "25.00",
        "currency": "USDC", "status": "completed",
    }))
    body = json.dumps({"data": {"id": "ch_1"}}).encode()
    response = client.post(
        "/v1/webhooks/payments", content=body, headers={"x-webhook-signature": sign(body)}
    )
    assert response.json()["status"] == "settled"


def test_no_webhook_endpoint_without_a_hosted_processor() -> None:
    store = Store("sqlite://")
    store.create_all()
    client = TestClient(create_app(Deps(
        store, LocalStore("./unused"), DemoChainProvider(), StaticTokenAuth({})
    )))
    assert client.post("/v1/webhooks/payments", content=b"{}").status_code == 404


# --------------------------------------------------------------------------
# seller repricing
# --------------------------------------------------------------------------

@pytest.fixture
def priced(tmp_path):
    store = Store("sqlite://")
    store.create_all()
    deps = Deps(
        store,
        LocalStore(tmp_path / "store"),
        DemoChainProvider(),
        StaticTokenAuth({"tok-creator": CREATOR, "tok-other": OTHER}),
    )
    client = TestClient(create_app(deps))

    digest = uuid.uuid4().hex + uuid.uuid4().hex
    body = {"digest": digest, "files": FILES}
    hdr = {"Authorization": "Bearer tok-creator"}
    client.post("/v1/artifacts", json=body, headers=hdr)
    target = deps.artifacts.root / artifact_key(digest, FILES[0]["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"xxxx")
    client.post(f"/v1/artifacts/{digest}/finalize", json=body, headers=hdr)
    listing_id = client.post(
        "/v1/listings",
        json={"artifact_digest": digest, "title": "Original", "price_minor": 45_000_000},
        headers=hdr,
    ).json()["listing_id"]
    return client, listing_id


def test_seller_can_reprice(priced) -> None:
    client, listing_id = priced
    r = client.patch(
        f"/v1/listings/{listing_id}",
        json={"price_minor": 39_000_000},
        headers={"Authorization": "Bearer tok-creator"},
    )
    assert r.status_code == 200
    assert r.json()["price"] == "39.000000 USDC"


def test_reprice_leaves_the_title_alone(priced) -> None:
    """Every field is optional: changing a price should not restate the name."""
    client, listing_id = priced
    body = client.patch(
        f"/v1/listings/{listing_id}",
        json={"price_minor": 10_000_000},
        headers={"Authorization": "Bearer tok-creator"},
    ).json()
    assert body["title"] == "Original"


def test_a_model_can_be_made_free(priced) -> None:
    client, listing_id = priced
    body = client.patch(
        f"/v1/listings/{listing_id}",
        json={"price_minor": 0},
        headers={"Authorization": "Bearer tok-creator"},
    ).json()
    assert body["price_minor"] == 0


def test_only_the_seller_can_reprice(priced) -> None:
    client, listing_id = priced
    r = client.patch(
        f"/v1/listings/{listing_id}",
        json={"price_minor": 1},
        headers={"Authorization": "Bearer tok-other"},
    )
    assert r.status_code == 403


def test_repricing_requires_authentication(priced) -> None:
    client, listing_id = priced
    assert client.patch(f"/v1/listings/{listing_id}", json={"price_minor": 1}).status_code == 401


def test_negative_prices_are_rejected(priced) -> None:
    client, listing_id = priced
    r = client.patch(
        f"/v1/listings/{listing_id}",
        json={"price_minor": -1},
        headers={"Authorization": "Bearer tok-creator"},
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------
# Coinbase Commerce: the two things that differ from a generic checkout
# --------------------------------------------------------------------------

def coinbase(handler):
    from keystone.providers.coinbase_commerce import CoinbaseCommerceProvider, CoinbaseConfig

    config = CoinbaseConfig(
        api_key="key", base_url="https://api.commerce.coinbase.com", webhook_secret=SECRET
    )
    return CoinbaseCommerceProvider(
        config,
        client=httpx.Client(base_url=config.base_url, transport=httpx.MockTransport(handler)),
    )


def charge_body(timeline, **overrides):
    data = {
        "id": "ch_abc",
        "hosted_url": "https://commerce.coinbase.com/charges/ABCD1234",
        "pricing": {"local": {"amount": "25.00", "currency": "USDC"}},
        "addresses": {"base": "0xdeadbeef", "ethereum": "0xother"},
        "metadata": {"reference": "ord_1"},
        "timeline": [{"status": s} for s in timeline],
    }
    data.update(overrides)
    return {"data": data}


@pytest.mark.parametrize(
    "timeline,expected",
    [
        (["NEW"], ChargeStatus.PENDING),
        (["NEW", "PENDING"], ChargeStatus.CONFIRMING),
        (["NEW", "PENDING", "COMPLETED"], ChargeStatus.SETTLED),
        (["NEW", "EXPIRED"], ChargeStatus.EXPIRED),
        (["NEW", "UNRESOLVED"], ChargeStatus.CONFIRMING),
    ],
)
def test_status_comes_from_the_end_of_the_timeline(timeline, expected) -> None:
    """Coinbase has no top-level status; reading one leaves charges stuck."""
    assert coinbase(route(charge_body(timeline))).get_charge("ch_abc").status is expected


def test_an_empty_timeline_is_pending_not_settled() -> None:
    assert coinbase(route(charge_body([]))).get_charge("ch_abc").status is ChargeStatus.PENDING


def test_an_unknown_timeline_status_is_never_settled() -> None:
    charge = coinbase(route(charge_body(["NEW", "SOME_NEW_STATE"]))).get_charge("ch_abc")
    assert charge.status is ChargeStatus.PENDING and not charge.is_settled


def test_price_is_read_from_the_nested_local_object() -> None:
    charge = coinbase(route(charge_body(["NEW"]))).get_charge("ch_abc")
    assert charge.amount == Money(25_000_000, Currency.USDC)


def test_reference_round_trips_through_metadata() -> None:
    """The reference is what ties a payment to one order."""
    assert coinbase(route(charge_body(["NEW"]))).get_charge("ch_abc").reference == "ord_1"


def test_address_prefers_the_configured_chain() -> None:
    assert coinbase(route(charge_body(["NEW"]))).get_charge("ch_abc").address == "0xdeadbeef"


def test_a_missing_local_price_is_an_error_not_a_zero() -> None:
    body = charge_body(["NEW"])
    body["data"]["pricing"] = {}
    with pytest.raises(CheckoutError, match="local price"):
        coinbase(route(body)).get_charge("ch_abc")


def test_webhook_id_is_read_from_the_event_envelope() -> None:
    provider = coinbase(route(charge_body(["NEW"])))
    assert provider.charge_id_from_webhook({"event": {"data": {"id": "ch_1"}}}) == "ch_1"
    assert provider.charge_id_from_webhook({"event": {"type": "charge:confirmed"}}) is None


def test_payouts_are_refused_rather_than_faked() -> None:
    """Commerce takes payments; it does not send them."""
    with pytest.raises(CheckoutError, match="payouts"):
        coinbase(route(charge_body(["NEW"]))).create_payout(
            "0xcreator", Money(1, Currency.USDC), "ord_1"
        )
