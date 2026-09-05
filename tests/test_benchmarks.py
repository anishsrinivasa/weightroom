"""Benchmark selection: the menu, the pricing, and the anti-hiding rule.

Three properties under test:

  1. Price scales with what the creator picks.
  2. Every offered benchmark appears in the report, run or declined -- a
     benchmark you can silently omit is one you can hide a bad result behind.
  3. Safety is mandatory and cannot be declined by omission.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keystone.api import Deps, create_app
from keystone.auth import Principal, StaticTokenAuth
from keystone.benchmarks import (
    SelectionError,
    declined_ids,
    mandatory_ids,
    menu,
    normalise_selection,
    optional_ids,
    quote,
)
from keystone.db import Store
from keystone.payments import Currency, MockPaymentProvider, Money
from keystone.registry import discover
from keystone.schema import Audience, Modality, Status
from keystone.storage import LocalStore, artifact_key
from keystone.suites import SuiteManifest
from keystone.visibility import redact

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
CREATOR = Principal("u_creator", "creator@example.com")
BUYER = Principal("u_buyer", "buyer@example.com")
FILES = [{"path": "model.safetensors", "size_bytes": 2048, "sha256": "a" * 64}]

SAFETY = "stub_safety"
CAPABILITY = "stub_capability"
REASONING = "stub_reasoning"


@pytest.fixture
def suites():
    return discover()


@pytest.fixture
def deps(tmp_path: Path) -> Deps:
    store = Store("sqlite://")
    store.create_all()
    auth = StaticTokenAuth({"tok-creator": CREATOR, "tok-buyer": BUYER})
    return Deps(store, LocalStore(tmp_path / "store"), MockPaymentProvider(), auth)


@pytest.fixture
def client(deps: Deps) -> TestClient:
    return TestClient(create_app(deps))


def _hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# --------------------------------------------------------------------------
# the menu
# --------------------------------------------------------------------------

def test_mandatory_items_come_first(suites) -> None:
    items = menu(suites)
    assert items[0].mandatory
    assert not items[-1].mandatory


def test_menu_carries_prices(suites) -> None:
    prices = {i.suite_id: i.price.amount_minor for i in menu(suites)}
    assert prices[SAFETY] == 15_000_000
    assert prices[CAPABILITY] == 10_000_000
    assert prices[REASONING] == 20_000_000


def test_safety_is_the_mandatory_one(suites) -> None:
    assert mandatory_ids(suites) == [SAFETY]
    assert set(optional_ids(suites)) == {CAPABILITY, REASONING}


def test_menu_exposes_gate_metadata(suites) -> None:
    safety = next(i for i in menu(suites) if i.suite_id == SAFETY)
    assert safety.gate is False  # current public over-refusal diagnostic is not a harm gate


# --------------------------------------------------------------------------
# 1. price scales with selection
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "selected,expected_minor",
    [
        ([], 15_000_000),                          # mandatory only
        ([CAPABILITY], 25_000_000),
        ([REASONING], 35_000_000),
        ([CAPABILITY, REASONING], 45_000_000),     # everything
    ],
)
def test_price_scales(suites, selected, expected_minor) -> None:
    assert quote(suites, selected) == Money(expected_minor, Currency.USDC)


def test_price_is_never_below_the_mandatory_floor(suites) -> None:
    assert quote(suites, []).amount_minor == quote(suites, None).amount_minor


def test_duplicate_picks_are_billed_once(suites) -> None:
    assert quote(suites, [CAPABILITY, CAPABILITY]) == quote(suites, [CAPABILITY])


# --------------------------------------------------------------------------
# 3. safety cannot be declined
# --------------------------------------------------------------------------

def test_mandatory_is_folded_in_when_omitted(suites) -> None:
    assert normalise_selection(suites, []) == [SAFETY]
    assert normalise_selection(suites, [CAPABILITY]) == [SAFETY, CAPABILITY]


def test_safety_is_never_declined(suites) -> None:
    for selection in ([], [CAPABILITY], [CAPABILITY, REASONING], None):
        assert SAFETY not in declined_ids(suites, selection)


def test_unknown_benchmark_is_an_error_not_a_silent_drop(suites) -> None:
    """Quietly ignoring something someone thought they bought is worse."""
    with pytest.raises(SelectionError, match="unknown benchmarks"):
        normalise_selection(suites, ["gpt5-bench"])


# --------------------------------------------------------------------------
# 2. declined benchmarks are visible to everyone
# --------------------------------------------------------------------------

def test_declined_are_reported(suites) -> None:
    assert set(declined_ids(suites, [CAPABILITY])) == {REASONING}
    assert set(declined_ids(suites, [])) == {CAPABILITY, REASONING}
    assert declined_ids(suites, [CAPABILITY, REASONING]) == []


def test_declined_survives_redaction_for_every_audience() -> None:
    """A buyer must be able to see what the seller chose not to run."""
    from keystone.schema import (
        CertificationReport,
        Rating,
        Source,
        SourceKind,
        Subject,
        SuiteResult,
    )

    report = CertificationReport(
        report_id="r1",
        created_at=NOW,
        status=Status.PASS,
        subject=Subject(
            source=Source(kind=SourceKind.UPLOAD, ref="rcpt"),
            artifact_digest="d" * 64,
            files=[],
            total_bytes=0,
        ),
        suite_results=[
            SuiteResult(
                suite_id=REASONING,
                suite_version="-",
                display_name="Multi-step reasoning",
                status=Status.SKIPPED,
                declined=True,
                error="declined by the creator",
            )
        ],
        rating=Rating(grade="B", as_tested_at=NOW),
    )

    for audience in (Audience.BUYER, Audience.CREATOR, Audience.INTERNAL):
        view = redact(report, audience)
        entry = view.suite_results[0]
        assert entry.declined is True
        assert entry.display_name == "Multi-step reasoning"


def test_declining_is_distinct_from_being_ineligible() -> None:
    """Two different facts a buyer needs kept apart."""
    from keystone.registry import select
    from keystone.schema import Capabilities

    class VisionOnly:
        manifest = SuiteManifest(
            id="vision_probe",
            version="1",
            modality=[Modality.TEXT, Modality.IMAGE],
            required_capabilities=["vision"],
        )

        async def run(self, ctx):  # pragma: no cover
            raise AssertionError

    _, skipped = select([VisionOnly()], Capabilities(), [Modality.TEXT], only=[])
    assert skipped[0].declined is False  # the model cannot, not the seller would not
    assert "vision" in skipped[0].reason


# --------------------------------------------------------------------------
# through the API
# --------------------------------------------------------------------------

def _listing(client: TestClient, deps: Deps) -> str:
    digest = uuid.uuid4().hex + uuid.uuid4().hex
    body = {"digest": digest, "files": FILES}
    client.post("/v1/artifacts", json=body, headers=_hdr("tok-creator"))
    for f in FILES:
        target = deps.artifacts.root / artifact_key(digest, f["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * f["size_bytes"])
    client.post(f"/v1/artifacts/{digest}/finalize", json=body, headers=_hdr("tok-creator"))
    return client.post(
        "/v1/listings",
        json={"artifact_digest": digest, "title": "Pick-your-own"},
        headers=_hdr("tok-creator"),
    ).json()["listing_id"]


def test_menu_endpoint_is_public(client: TestClient) -> None:
    body = client.get("/v1/benchmarks").json()
    assert {b["suite_id"] for b in body["benchmarks"]} == {SAFETY, CAPABILITY, REASONING}
    assert body["mandatory_total"] == "15.000000 USDC"


def test_publish_quotes_the_selection(client: TestClient, deps: Deps) -> None:
    listing_id = _listing(client, deps)
    r = client.post(
        f"/v1/listings/{listing_id}/publish",
        json={"benchmarks": [REASONING]},
        headers=_hdr("tok-creator"),
    ).json()

    assert r["amount"] == "35.000000 USDC"       # 15 mandatory + 20 reasoning
    assert r["running"] == [SAFETY, REASONING]
    assert r["declined"] == [CAPABILITY]


def test_publish_rejects_an_unknown_benchmark(client: TestClient, deps: Deps) -> None:
    listing_id = _listing(client, deps)
    r = client.post(
        f"/v1/listings/{listing_id}/publish",
        json={"benchmarks": ["not-a-benchmark"]},
        headers=_hdr("tok-creator"),
    )
    assert r.status_code == 400 and "unknown benchmarks" in r.json()["detail"]


def test_selection_is_persisted_for_the_worker(client: TestClient, deps: Deps) -> None:
    listing_id = _listing(client, deps)
    client.post(
        f"/v1/listings/{listing_id}/publish",
        json={"benchmarks": [CAPABILITY]},
        headers=_hdr("tok-creator"),
    )
    with deps.store.session() as s:
        row = deps.store.get_listing(s, listing_id)
        assert row.selected_benchmarks == [SAFETY, CAPABILITY]


def test_worker_runs_only_what_was_paid_for(client: TestClient, deps: Deps) -> None:
    from keystone.pipeline import Outcome
    from keystone.worker import process_pending

    listing_id = _listing(client, deps)
    order = client.post(
        f"/v1/listings/{listing_id}/publish",
        json={"benchmarks": [CAPABILITY]},
        headers=_hdr("tok-creator"),
    ).json()
    deps.payments.settle(order["charge_id"])
    client.post(
        f"/v1/listings/{listing_id}/confirm",
        json={"charge_id": order["charge_id"]},
        headers=_hdr("tok-creator"),
    )

    seen: list[list[str] | None] = []

    def fake_certify(digest, only=None):
        seen.append(only)
        return Outcome(digest)

    process_pending(deps.store, certify=fake_certify)
    assert seen == [[SAFETY, CAPABILITY]]
