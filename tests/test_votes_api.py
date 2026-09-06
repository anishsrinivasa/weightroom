"""Voting and licence acceptance over the API.

Both gates exist for the same reason: a number a buyer reads has to mean what
they think it means, and an agreement they are held to has to be one they
actually made.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keystone.api import Deps, create_app
from keystone.auth import Principal, StaticTokenAuth
from keystone.db import Store
from keystone.worker import publish_certified, record_outcome
from keystone.payments import Currency, MockPaymentProvider, Money
from keystone.pipeline import Outcome
from keystone.storage import LocalStore, artifact_key

from tests.test_orders import CREATOR, BUYER, OTHER, FILES, NOW, PRICE, _report


@pytest.fixture
def deps(tmp_path: Path) -> Deps:
    store = Store("sqlite://")
    store.create_all()
    auth = StaticTokenAuth({"tok-creator": CREATOR, "tok-buyer": BUYER, "tok-other": OTHER})
    return Deps(
        store=store,
        artifacts=LocalStore(tmp_path / "store"),
        payments=MockPaymentProvider(),
        auth=auth,
    )


@pytest.fixture
def client(deps: Deps) -> TestClient:
    return TestClient(create_app(deps))


def _hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _live(client: TestClient, deps: Deps, license_kind: str = "non_distributive") -> str:
    digest = uuid.uuid4().hex + uuid.uuid4().hex
    body = {"digest": digest, "files": FILES}
    client.post("/v1/artifacts", json=body, headers=_hdr("tok-creator"))
    for f in FILES:
        target = deps.artifacts.root / artifact_key(digest, f["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * f["size_bytes"])
    client.post(f"/v1/artifacts/{digest}/finalize", json=body, headers=_hdr("tok-creator"))
    listing_id = client.post(
        "/v1/listings",
        json={
            "artifact_digest": digest, "title": "Votable",
            "price_minor": PRICE.amount_minor, "license_kind": license_kind,
        },
        headers=_hdr("tok-creator"),
    ).json()["listing_id"]
    record_outcome(deps.store, listing_id, Outcome(digest, report=_report(digest)), now=NOW)
    publish_certified(deps.store, listing_id)
    return listing_id


# -- votes -----------------------------------------------------------------

def test_voting_requires_an_account(client: TestClient, deps: Deps) -> None:
    """The whole reason the number is worth showing."""
    listing_id = _live(client, deps)
    r = client.post(f"/v1/listings/{listing_id}/vote", json={"value": 1})
    assert r.status_code == 401


def test_a_vote_is_counted_and_reported_back(client: TestClient, deps: Deps) -> None:
    listing_id = _live(client, deps)
    body = client.post(
        f"/v1/listings/{listing_id}/vote", json={"value": 1}, headers=_hdr("tok-buyer")
    ).json()
    assert (body["up"], body["down"], body["score"], body["mine"]) == (1, 0, 1, 1)


def test_pressing_the_same_button_twice_does_not_double_the_score(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _live(client, deps)
    for _ in range(3):
        body = client.post(
            f"/v1/listings/{listing_id}/vote", json={"value": 1}, headers=_hdr("tok-buyer")
        ).json()
    assert body["score"] == 1


def test_a_seller_cannot_vote_on_their_own_listing(client: TestClient, deps: Deps) -> None:
    listing_id = _live(client, deps)
    r = client.post(
        f"/v1/listings/{listing_id}/vote", json={"value": 1}, headers=_hdr("tok-creator")
    )
    assert r.status_code == 409


def test_a_vote_outside_the_range_is_refused(client: TestClient, deps: Deps) -> None:
    """A gate that coerced this would record a vote nobody cast."""
    listing_id = _live(client, deps)
    r = client.post(
        f"/v1/listings/{listing_id}/vote", json={"value": 7}, headers=_hdr("tok-buyer")
    )
    assert r.status_code == 422


# -- licence acceptance ----------------------------------------------------

def test_purchase_without_accepting_the_terms_is_refused(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _live(client, deps)
    r = client.post(f"/v1/listings/{listing_id}/purchase", headers=_hdr("tok-buyer"))
    assert r.status_code == 409
    assert "accept those terms" in r.json()["detail"]


def test_accepting_the_wrong_terms_is_refused(client: TestClient, deps: Deps) -> None:
    """A client can only report what it rendered. If it rendered stale terms,
    the buyer agreed to something this listing is not sold under."""
    listing_id = _live(client, deps, license_kind="non_distributive")
    r = client.post(
        f"/v1/listings/{listing_id}/purchase",
        json={"accept_license": "full_access"},
        headers=_hdr("tok-buyer"),
    )
    assert r.status_code == 409


def test_accepting_the_listings_terms_succeeds_and_is_recorded(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _live(client, deps, license_kind="full_access")
    order = client.post(
        f"/v1/listings/{listing_id}/purchase",
        json={"accept_license": "full_access"},
        headers=_hdr("tok-buyer"),
    )
    assert order.status_code == 201
    from keystone.db import OrderRow

    with deps.store.session() as s:
        row = s.get(OrderRow, order.json()["order_id"])
        assert row.license_kind == "full_access"
        assert row.license_accepted_at is not None


def test_the_licence_catalogue_is_public(client: TestClient) -> None:
    """A buyer has to be able to read the terms before deciding to sign in."""
    body = client.get("/v1/licenses").json()
    assert {entry["kind"] for entry in body["licenses"]} == {
        "non_distributive", "full_access",
    }
    assert body["default"] == "non_distributive"
