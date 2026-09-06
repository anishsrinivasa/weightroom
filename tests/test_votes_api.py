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

from datetime import datetime, timezone

from keystone.api import Deps, create_app
from keystone.auth import Principal, StaticTokenAuth
from keystone.db import Store
from keystone.payments import Currency, MockPaymentProvider, Money
from keystone.pipeline import Outcome
from keystone.schema import (
    CertificationReport,
    Cost,
    Environment,
    Rating,
    ServingProfile,
    Source,
    SourceKind,
    Status,
    Subject,
    SuiteResult,
)
from keystone.storage import LocalStore, artifact_key
from keystone.worker import publish_certified, record_outcome

# Defined here rather than imported from a sibling test module. `tests` is not
# a package -- no `__init__.py` -- so `from tests.test_orders import ...`
# resolves locally, where the rootdir happens to be on sys.path, and fails in
# CI with ModuleNotFoundError. It blocked every deploy until it was noticed.
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
PRICE = Money(50_000_000, Currency.USDC)
CREATOR = Principal("u_creator", "creator@example.com")
BUYER = Principal("u_buyer", "buyer@example.com")
OTHER = Principal("u_other", "other@example.com")
FILES = [{"path": "model.safetensors", "size_bytes": 2048, "sha256": "a" * 64}]


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
        serving_profile=ServingProfile(parameter_count=3_500_000_000),
        suite_results=[
            SuiteResult(
                suite_id="harm_gate", suite_version="1",
                status=Status.PASS, gate=True, score=0.95,
            )
        ],
        cost=Cost(gpu_seconds=80.0),
        rating=Rating(grade="A", certified=True, as_tested_at=NOW),
    )


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
