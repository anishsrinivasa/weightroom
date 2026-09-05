"""Purchase and entitlement tests.

The central assertion: a download URL is never minted without a paid order.
Before this existed, any authenticated user could download any listed model.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keystone.api import Deps, create_app
from keystone.auth import Principal, StaticTokenAuth
from keystone.db import Store
from keystone.orders import (
    EntitlementError,
    Order,
    OrderStatus,
    RevenueSplit,
    check_entitlement,
    settle,
)
from keystone.payments import Currency, MockPaymentProvider, Money
from keystone.pipeline import Outcome
from keystone.schema import (
    CertificationReport,
    Cost,
    Environment,
    Rating,
    Source,
    SourceKind,
    Status,
    Subject,
    SuiteResult,
)
from keystone.storage import LocalStore, artifact_key
from keystone.worker import publish_certified, record_outcome

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
PRICE = Money(50_000_000, Currency.USDC)  # 50 USDC

CREATOR = Principal("u_creator", "creator@example.com")
BUYER = Principal("u_buyer", "buyer@example.com")
OTHER = Principal("u_other", "other@example.com")

FILES = [{"path": "model.safetensors", "size_bytes": 2048, "sha256": "a" * 64}]


@pytest.fixture
def deps(tmp_path: Path) -> Deps:
    store = Store("sqlite://")
    store.create_all()
    auth = StaticTokenAuth(
        {"tok-creator": CREATOR, "tok-buyer": BUYER, "tok-other": OTHER}
    )
    return Deps(store, LocalStore(tmp_path / "store"), MockPaymentProvider(), auth)


@pytest.fixture
def client(deps: Deps) -> TestClient:
    return TestClient(create_app(deps))


def _hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _report(digest: str) -> CertificationReport:
    return CertificationReport(
        report_id=f"rep_{uuid.uuid4().hex[:12]}",
        created_at=NOW,
        status=Status.PASS,
        subject=Subject(
            source=Source(kind=SourceKind.UPLOAD, ref="rcpt"),
            artifact_digest=digest,
            files=[],
            total_bytes=0,
        ),
        environment=Environment(sandboxed=True),
        suite_results=[
            SuiteResult(
                suite_id="harm_gate",
                suite_version="1",
                status=Status.PASS,
                gate=True,
                score=0.95,
            )
        ],
        cost=Cost(gpu_seconds=80.0),
        rating=Rating(grade="A", as_tested_at=NOW),
    )


def _live_listing(client: TestClient, deps: Deps, price_minor: int = PRICE.amount_minor) -> str:
    """Upload, list, certify, and publish -- a model actually on sale."""
    digest = uuid.uuid4().hex + uuid.uuid4().hex  # 64 hex chars
    body = {"digest": digest, "files": FILES}
    client.post("/v1/artifacts", json=body, headers=_hdr("tok-creator"))
    for f in FILES:
        target = deps.artifacts.root / artifact_key(digest, f["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * f["size_bytes"])
    client.post(f"/v1/artifacts/{digest}/finalize", json=body, headers=_hdr("tok-creator"))

    listing_id = client.post(
        "/v1/listings",
        json={"artifact_digest": digest, "title": "Sellable", "price_minor": price_minor},
        headers=_hdr("tok-creator"),
    ).json()["listing_id"]

    record_outcome(deps.store, listing_id, Outcome(digest, report=_report(digest)), now=NOW)
    publish_certified(deps.store, listing_id)
    return listing_id


def _buy(client: TestClient, deps: Deps, listing_id: str, token: str = "tok-buyer") -> dict:
    order = client.post(f"/v1/listings/{listing_id}/purchase", headers=_hdr(token)).json()
    deps.payments.settle(order["charge_id"])
    client.post(f"/v1/orders/{order['order_id']}/confirm", headers=_hdr(token))
    return order


# --------------------------------------------------------------------------
# the hole this closes
# --------------------------------------------------------------------------

def test_download_refused_without_purchase(client: TestClient, deps: Deps) -> None:
    """Previously: any authenticated user got the weights. Now: 402."""
    listing_id = _live_listing(client, deps)
    r = client.get(f"/v1/listings/{listing_id}/download", headers=_hdr("tok-buyer"))
    assert r.status_code == 402
    assert "purchase required" in r.json()["detail"]


def test_download_allowed_after_purchase(client: TestClient, deps: Deps) -> None:
    listing_id = _live_listing(client, deps)
    _buy(client, deps, listing_id)
    r = client.get(f"/v1/listings/{listing_id}/download", headers=_hdr("tok-buyer"))
    assert r.status_code == 200
    assert r.json()["files"][0]["sha256"] == "a" * 64


def test_unsettled_order_does_not_entitle(client: TestClient, deps: Deps) -> None:
    listing_id = _live_listing(client, deps)
    client.post(f"/v1/listings/{listing_id}/purchase", headers=_hdr("tok-buyer"))  # unpaid
    r = client.get(f"/v1/listings/{listing_id}/download", headers=_hdr("tok-buyer"))
    assert r.status_code == 402


def test_one_buyers_order_does_not_entitle_another(client: TestClient, deps: Deps) -> None:
    listing_id = _live_listing(client, deps)
    _buy(client, deps, listing_id, token="tok-buyer")
    r = client.get(f"/v1/listings/{listing_id}/download", headers=_hdr("tok-other"))
    assert r.status_code == 402


def test_paying_for_one_model_does_not_unlock_another(client: TestClient, deps: Deps) -> None:
    bought = _live_listing(client, deps)
    other = _live_listing(client, deps)
    _buy(client, deps, bought)
    assert client.get(f"/v1/listings/{bought}/download", headers=_hdr("tok-buyer")).status_code == 200
    assert client.get(f"/v1/listings/{other}/download", headers=_hdr("tok-buyer")).status_code == 402


def test_anonymous_cannot_download(client: TestClient, deps: Deps) -> None:
    listing_id = _live_listing(client, deps)
    assert client.get(f"/v1/listings/{listing_id}/download").status_code == 401


def test_creator_reaches_their_own_artifact(client: TestClient, deps: Deps) -> None:
    listing_id = _live_listing(client, deps)
    assert client.get(
        f"/v1/listings/{listing_id}/download", headers=_hdr("tok-creator")
    ).status_code == 200


def test_free_listings_entitle_directly(client: TestClient, deps: Deps) -> None:
    listing_id = _live_listing(client, deps, price_minor=0)
    assert client.get(
        f"/v1/listings/{listing_id}/download", headers=_hdr("tok-buyer")
    ).status_code == 200


def test_free_listings_cannot_be_purchased(client: TestClient, deps: Deps) -> None:
    listing_id = _live_listing(client, deps, price_minor=0)
    r = client.post(f"/v1/listings/{listing_id}/purchase", headers=_hdr("tok-buyer"))
    assert r.status_code == 409 and "free" in r.json()["detail"]


# --------------------------------------------------------------------------
# purchase flow
# --------------------------------------------------------------------------

def test_purchase_returns_a_charge(client: TestClient, deps: Deps) -> None:
    listing_id = _live_listing(client, deps)
    r = client.post(f"/v1/listings/{listing_id}/purchase", headers=_hdr("tok-buyer")).json()
    assert r["amount"] == "50.000000 USDC"
    assert r["order_id"].startswith("ord_") and r["address"]


def test_cannot_buy_an_unpublished_model(client: TestClient, deps: Deps) -> None:
    digest = uuid.uuid4().hex + uuid.uuid4().hex
    listing_id = client.post(
        "/v1/listings",
        json={"artifact_digest": digest, "price_minor": 1000},
        headers=_hdr("tok-creator"),
    ).json()["listing_id"]
    # Draft listings have no artifact row, but the state check fires first.
    r = client.post(f"/v1/listings/{listing_id}/purchase", headers=_hdr("tok-buyer"))
    assert r.status_code == 409 and "not published" in r.json()["detail"]


def test_creator_cannot_buy_their_own(client: TestClient, deps: Deps) -> None:
    listing_id = _live_listing(client, deps)
    r = client.post(f"/v1/listings/{listing_id}/purchase", headers=_hdr("tok-creator"))
    assert r.status_code == 409


def test_double_purchase_refused(client: TestClient, deps: Deps) -> None:
    listing_id = _live_listing(client, deps)
    _buy(client, deps, listing_id)
    r = client.post(f"/v1/listings/{listing_id}/purchase", headers=_hdr("tok-buyer"))
    assert r.status_code == 409 and "already purchased" in r.json()["detail"]


def test_confirm_refuses_someone_elses_order(client: TestClient, deps: Deps) -> None:
    listing_id = _live_listing(client, deps)
    order = client.post(f"/v1/listings/{listing_id}/purchase", headers=_hdr("tok-buyer")).json()
    deps.payments.settle(order["charge_id"])
    r = client.post(f"/v1/orders/{order['order_id']}/confirm", headers=_hdr("tok-other"))
    assert r.status_code == 403


def test_orders_list_is_scoped_to_the_caller(client: TestClient, deps: Deps) -> None:
    listing_id = _live_listing(client, deps)
    _buy(client, deps, listing_id)
    assert len(client.get("/v1/orders", headers=_hdr("tok-buyer")).json()["orders"]) == 1
    assert client.get("/v1/orders", headers=_hdr("tok-other")).json()["orders"] == []


# --------------------------------------------------------------------------
# payouts
# --------------------------------------------------------------------------

def test_paid_order_credits_the_creator(client: TestClient, deps: Deps) -> None:
    listing_id = _live_listing(client, deps)
    order = _buy(client, deps, listing_id)

    with deps.store.session() as s:
        payout = deps.store.payout_for_order(s, order["order_id"])

    assert payout is not None
    assert payout.creator_id == "u_creator"
    assert payout.amount_minor == 42_500_000  # 50 USDC less the 15% take


def test_replayed_confirmation_does_not_pay_twice(client: TestClient, deps: Deps) -> None:
    """A retried webhook must not mint a second payout."""
    listing_id = _live_listing(client, deps)
    order = _buy(client, deps, listing_id)
    client.post(f"/v1/orders/{order['order_id']}/confirm", headers=_hdr("tok-buyer"))
    client.post(f"/v1/orders/{order['order_id']}/confirm", headers=_hdr("tok-buyer"))

    with deps.store.session() as s:
        assert len(deps.store.unpaid_payouts(s)) == 1


def test_split_never_loses_a_unit() -> None:
    """Integer arithmetic: the creator gets the remainder, so nothing evaporates."""
    split = RevenueSplit(take_rate_bps=1500)
    for minor in (1, 3, 7, 99, 12_345, 50_000_000, 999_999_999):
        amount = Money(minor, Currency.USDC)
        assert (
            split.platform_cut(amount).amount_minor + split.creator_cut(amount).amount_minor
            == minor
        )


def test_zero_take_rate_gives_everything_to_the_creator() -> None:
    split = RevenueSplit(take_rate_bps=0)
    assert split.creator_cut(PRICE) == PRICE
    assert split.platform_cut(PRICE).amount_minor == 0


@pytest.mark.parametrize("bps", [-1, 10_001])
def test_absurd_take_rates_are_rejected(bps: int) -> None:
    with pytest.raises(ValueError):
        RevenueSplit(take_rate_bps=bps)


# --------------------------------------------------------------------------
# settle()
# --------------------------------------------------------------------------

def _order(amount: Money = PRICE) -> Order:
    return Order(
        order_id="ord_1",
        buyer_id="u_buyer",
        listing_id="lst_1",
        artifact_digest="d" * 64,
        amount=amount,
        created_at=NOW,
    )


def test_settle_marks_paid() -> None:
    provider = MockPaymentProvider()
    charge = provider.create_charge(PRICE, "ord_1")
    provider.settle(charge.charge_id)
    order = settle(_order(), provider.get_charge(charge.charge_id), NOW)
    assert order.status is OrderStatus.PAID and order.paid_at == NOW


def test_settle_is_idempotent() -> None:
    provider = MockPaymentProvider()
    charge = provider.create_charge(PRICE, "ord_1")
    provider.settle(charge.charge_id)
    order = settle(_order(), provider.get_charge(charge.charge_id), NOW)
    again = settle(order, provider.get_charge(charge.charge_id), datetime(2027, 1, 1))
    assert again.paid_at == NOW  # not overwritten


def test_settle_rejects_a_charge_for_another_order() -> None:
    provider = MockPaymentProvider()
    charge = provider.create_charge(PRICE, "ord_SOMEONE_ELSE")
    provider.settle(charge.charge_id)
    with pytest.raises(EntitlementError, match="does not reference"):
        settle(_order(), provider.get_charge(charge.charge_id), NOW)


def test_settle_rejects_underpayment() -> None:
    provider = MockPaymentProvider()
    charge = provider.create_charge(Money(1, Currency.USDC), "ord_1")
    provider.settle(charge.charge_id)
    with pytest.raises(EntitlementError, match="underpaid"):
        settle(_order(), provider.get_charge(charge.charge_id), NOW)


def test_unsettled_charge_leaves_the_order_pending() -> None:
    provider = MockPaymentProvider()
    charge = provider.create_charge(PRICE, "ord_1")
    order = settle(_order(), charge, NOW)
    assert order.status is OrderStatus.PENDING


# --------------------------------------------------------------------------
# check_entitlement, directly
# --------------------------------------------------------------------------

def _check(**kw):
    base = dict(
        buyer_id="u_buyer",
        listing_id="lst_1",
        listing_state="listed",
        listing_creator_id="u_creator",
        price=PRICE,
        order=None,
    )
    base.update(kw)
    return check_entitlement(**base)


def test_refunded_order_withdraws_entitlement() -> None:
    order = _order()
    order.status = OrderStatus.REFUNDED
    verdict = _check(order=order)
    assert not verdict.allowed and "refunded" in verdict.reason
    # Not a payment problem -- paying again would not help.
    assert not verdict.payment_required


def test_paid_order_entitles() -> None:
    order = _order()
    order.status = OrderStatus.PAID
    assert _check(order=order).allowed


def test_delisted_model_cannot_be_downloaded_by_a_stranger() -> None:
    verdict = _check(listing_state="delisted")
    assert not verdict.allowed and "not published" in verdict.reason


def test_missing_and_unsettled_orders_are_both_payment_problems() -> None:
    """Both resolve by paying, so both must surface as 402."""
    assert _check(order=None).payment_required
    assert _check(order=_order()).payment_required  # pending


def test_a_paid_order_for_another_listing_is_not_a_payment_problem() -> None:
    """Paying again would not help -- this is the wrong order entirely."""
    order = _order()
    order.status = OrderStatus.PAID
    order.listing_id = "lst_ANOTHER"
    verdict = _check(order=order)
    assert not verdict.allowed and not verdict.payment_required
