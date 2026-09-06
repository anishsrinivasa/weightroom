"""Taking a listing off the marketplace when its creator is unreachable.

The seller-facing unlist requires the caller to be the creator. That is right
for sellers and useless for the case it does not cover: a listing created by a
principal that no longer exists -- a static dev token from before real accounts
-- leaves the marketplace with something on it that nobody can remove.
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

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
FILES = [{"path": "model.safetensors", "size_bytes": 2048, "sha256": "a" * 64}]
CREATOR = Principal("u_creator", "creator@example.com")
STRANGER = Principal("u_stranger", "stranger@example.com")
ADMIN = Principal("u_admin", "admin@example.com", is_admin=True)


def _report(digest: str) -> CertificationReport:
    return CertificationReport(
        report_id=f"rep_{uuid.uuid4().hex[:12]}",
        created_at=NOW,
        status=Status.PASS,
        subject=Subject(
            source=Source(kind=SourceKind.UPLOAD, ref="rcpt"),
            artifact_digest=digest, files=[], total_bytes=0,
        ),
        environment=Environment(sandboxed=True),
        serving_profile=ServingProfile(parameter_count=1),
        suite_results=[SuiteResult(
            suite_id="harm_gate", suite_version="1",
            status=Status.PASS, gate=True, score=0.95,
        )],
        cost=Cost(gpu_seconds=1.0),
        rating=Rating(grade="A", certified=True, as_tested_at=NOW),
    )


@pytest.fixture
def deps(tmp_path: Path) -> Deps:
    store = Store("sqlite://")
    store.create_all()
    return Deps(
        store=store,
        artifacts=LocalStore(tmp_path / "store"),
        payments=MockPaymentProvider(),
        auth=StaticTokenAuth({
            "tok-creator": CREATOR, "tok-stranger": STRANGER, "tok-admin": ADMIN,
        }),
    )


@pytest.fixture
def client(deps: Deps) -> TestClient:
    return TestClient(create_app(deps))


def _hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _live(client: TestClient, deps: Deps) -> str:
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
        json={"artifact_digest": digest, "title": "Live", "price_minor": 10_000_000},
        headers=_hdr("tok-creator"),
    ).json()["listing_id"]
    record_outcome(deps.store, listing_id, Outcome(digest, report=_report(digest)), now=NOW)
    publish_certified(deps.store, listing_id)
    return listing_id


def test_an_admin_can_delist_someone_elses_listing(client: TestClient, deps: Deps) -> None:
    listing_id = _live(client, deps)
    body = client.post(
        f"/v1/admin/listings/{listing_id}/delist", headers=_hdr("tok-admin")
    ).json()
    assert body["state"] == "delisted"
    assert body["previous_state"] == "listed"


def test_a_delisted_model_leaves_the_marketplace(client: TestClient, deps: Deps) -> None:
    """The point of the endpoint, rather than the state transition."""
    listing_id = _live(client, deps)
    listed = client.get("/v1/listings").json()["listings"]
    assert listing_id in {row["listing_id"] for row in listed}

    client.post(f"/v1/admin/listings/{listing_id}/delist", headers=_hdr("tok-admin"))
    after = client.get("/v1/listings").json()["listings"]
    assert listing_id not in {row["listing_id"] for row in after}


def test_a_non_admin_cannot_delist(client: TestClient, deps: Deps) -> None:
    listing_id = _live(client, deps)
    for token in ("tok-stranger", "tok-creator"):
        r = client.post(f"/v1/admin/listings/{listing_id}/delist", headers=_hdr(token))
        assert r.status_code == 403


def test_delisting_requires_authentication(client: TestClient, deps: Deps) -> None:
    listing_id = _live(client, deps)
    assert client.post(f"/v1/admin/listings/{listing_id}/delist").status_code == 401


def test_delisting_twice_is_not_an_error(client: TestClient, deps: Deps) -> None:
    """Idempotent: a cleanup action that fails the second time invites someone
    to wonder whether the first one worked."""
    listing_id = _live(client, deps)
    client.post(f"/v1/admin/listings/{listing_id}/delist", headers=_hdr("tok-admin"))
    second = client.post(
        f"/v1/admin/listings/{listing_id}/delist", headers=_hdr("tok-admin")
    ).json()
    assert second["already"] is True
    assert second["state"] == "delisted"


def test_an_unknown_listing_is_a_404(client: TestClient) -> None:
    assert client.post(
        "/v1/admin/listings/lst_nope/delist", headers=_hdr("tok-admin")
    ).status_code == 404


def test_the_listing_row_survives(client: TestClient, deps: Deps) -> None:
    """Delisted, not deleted. Orders and reports reference a listing, and
    removing the row would break the record of purchases that really happened."""
    from keystone.db import ListingRow

    listing_id = _live(client, deps)
    client.post(f"/v1/admin/listings/{listing_id}/delist", headers=_hdr("tok-admin"))
    with deps.store.session() as s:
        assert s.get(ListingRow, listing_id) is not None
