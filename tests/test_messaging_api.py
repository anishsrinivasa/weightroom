"""Direct messaging over the API.

The access rule is the interesting part: a thread you are not in must be
indistinguishable from a thread that does not exist.
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
from keystone.payments import MockPaymentProvider
from keystone.pipeline import Outcome
from keystone.schema import (
    CertificationReport, Cost, Environment, Rating, ServingProfile,
    Source, SourceKind, Status, Subject, SuiteResult,
)
from keystone.storage import LocalStore, artifact_key
from keystone.worker import publish_certified, record_outcome

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
FILES = [{"path": "model.safetensors", "size_bytes": 2048, "sha256": "a" * 64}]
SELLER = Principal("u_seller", "seller@example.com")
BUYER = Principal("u_buyer", "buyer@example.com")
OTHER = Principal("u_other", "other@example.com")


def _report(digest: str) -> CertificationReport:
    return CertificationReport(
        report_id=f"rep_{uuid.uuid4().hex[:12]}", created_at=NOW, status=Status.PASS,
        subject=Subject(source=Source(kind=SourceKind.UPLOAD, ref="r"),
                        artifact_digest=digest, files=[], total_bytes=0),
        environment=Environment(sandboxed=True),
        serving_profile=ServingProfile(parameter_count=1),
        suite_results=[SuiteResult(suite_id="g", suite_version="1",
                                   status=Status.PASS, gate=True, score=0.9)],
        cost=Cost(gpu_seconds=1.0),
        rating=Rating(grade="A", certified=True, as_tested_at=NOW),
    )


@pytest.fixture
def deps(tmp_path: Path) -> Deps:
    store = Store("sqlite://")
    store.create_all()
    return Deps(
        store=store, artifacts=LocalStore(tmp_path / "s"),
        payments=MockPaymentProvider(),
        auth=StaticTokenAuth({"tok-seller": SELLER, "tok-buyer": BUYER, "tok-other": OTHER}),
    )


@pytest.fixture
def client(deps: Deps) -> TestClient:
    return TestClient(create_app(deps))


def _hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def listing(client: TestClient, deps: Deps) -> str:
    digest = uuid.uuid4().hex + uuid.uuid4().hex
    body = {"digest": digest, "files": FILES}
    client.post("/v1/artifacts", json=body, headers=_hdr("tok-seller"))
    for f in FILES:
        target = deps.artifacts.root / artifact_key(digest, f["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * f["size_bytes"])
    client.post(f"/v1/artifacts/{digest}/finalize", json=body, headers=_hdr("tok-seller"))
    listing_id = client.post(
        "/v1/listings",
        json={"artifact_digest": digest, "title": "Chatty", "price_minor": 1_000_000},
        headers=_hdr("tok-seller"),
    ).json()["listing_id"]
    record_outcome(deps.store, listing_id, Outcome(digest, report=_report(digest)), now=NOW)
    publish_certified(deps.store, listing_id)
    return listing_id


def _open(client: TestClient, listing: str, tok: str = "tok-buyer") -> str:
    return client.post(f"/v1/listings/{listing}/threads", headers=_hdr(tok)).json()["thread_id"]


def test_a_buyer_can_open_a_thread_with_the_seller(client, listing) -> None:
    body = client.post(f"/v1/listings/{listing}/threads", headers=_hdr("tok-buyer")).json()
    assert body["counterpart_id"] == "u_seller"
    assert body["listing_title"] == "Chatty"


def test_opening_twice_returns_the_same_thread(client, listing) -> None:
    assert _open(client, listing) == _open(client, listing)


def test_messages_go_both_ways(client, listing) -> None:
    thread = _open(client, listing)
    client.post(f"/v1/threads/{thread}/messages",
                json={"body": "does it run on 8GB?"}, headers=_hdr("tok-buyer"))
    client.post(f"/v1/threads/{thread}/messages",
                json={"body": "yes"}, headers=_hdr("tok-seller"))
    seen = client.get(f"/v1/threads/{thread}", headers=_hdr("tok-seller")).json()
    assert [m["body"] for m in seen["messages"]] == ["does it run on 8GB?", "yes"]
    assert [m["mine"] for m in seen["messages"]] == [False, True]


def test_a_stranger_cannot_read_a_thread(client, listing) -> None:
    """404, not 403: a thread nobody told you about should not be confirmed to
    exist by the error you get for asking."""
    thread = _open(client, listing)
    assert client.get(f"/v1/threads/{thread}", headers=_hdr("tok-other")).status_code == 404


def test_a_stranger_cannot_post_into_a_thread(client, listing) -> None:
    thread = _open(client, listing)
    r = client.post(f"/v1/threads/{thread}/messages",
                    json={"body": "hello"}, headers=_hdr("tok-other"))
    assert r.status_code == 404


def test_a_stranger_cannot_delete_a_thread(client, listing) -> None:
    thread = _open(client, listing)
    assert client.delete(f"/v1/threads/{thread}", headers=_hdr("tok-other")).status_code == 404
    assert client.get(f"/v1/threads/{thread}", headers=_hdr("tok-buyer")).status_code == 200


def test_threads_are_listed_only_for_participants(client, listing) -> None:
    _open(client, listing)
    assert len(client.get("/v1/threads", headers=_hdr("tok-buyer")).json()["threads"]) == 1
    assert len(client.get("/v1/threads", headers=_hdr("tok-seller")).json()["threads"]) == 1
    assert client.get("/v1/threads", headers=_hdr("tok-other")).json()["threads"] == []


def test_unread_counts_the_other_side_only(client, listing) -> None:
    thread = _open(client, listing)
    client.post(f"/v1/threads/{thread}/messages",
                json={"body": "hello"}, headers=_hdr("tok-buyer"))
    seller = client.get("/v1/threads", headers=_hdr("tok-seller")).json()["threads"][0]
    buyer = client.get("/v1/threads", headers=_hdr("tok-buyer")).json()["threads"][0]
    assert seller["unread"] == 1
    assert buyer["unread"] == 0  # your own message is not unread


def test_opening_a_thread_clears_its_unread(client, listing) -> None:
    thread = _open(client, listing)
    client.post(f"/v1/threads/{thread}/messages",
                json={"body": "hello"}, headers=_hdr("tok-buyer"))
    client.get(f"/v1/threads/{thread}", headers=_hdr("tok-seller"))
    seller = client.get("/v1/threads", headers=_hdr("tok-seller")).json()["threads"][0]
    assert seller["unread"] == 0


def test_either_participant_can_delete_and_it_goes_for_both(client, listing) -> None:
    thread = _open(client, listing)
    client.post(f"/v1/threads/{thread}/messages",
                json={"body": "hello"}, headers=_hdr("tok-buyer"))
    assert client.delete(f"/v1/threads/{thread}", headers=_hdr("tok-seller")).status_code == 200
    assert client.get(f"/v1/threads/{thread}", headers=_hdr("tok-buyer")).status_code == 404
    assert client.get("/v1/threads", headers=_hdr("tok-buyer")).json()["threads"] == []


def test_a_seller_cannot_open_a_thread_with_themselves(client, listing) -> None:
    r = client.post(f"/v1/listings/{listing}/threads", headers=_hdr("tok-seller"))
    assert r.status_code == 409


def test_messaging_requires_an_account(client, listing) -> None:
    assert client.post(f"/v1/listings/{listing}/threads").status_code == 401
    assert client.get("/v1/threads").status_code == 401


def test_an_empty_message_is_refused(client, listing) -> None:
    thread = _open(client, listing)
    r = client.post(f"/v1/threads/{thread}/messages",
                    json={"body": ""}, headers=_hdr("tok-buyer"))
    assert r.status_code == 422
