"""Persistence tests. SQLite in-memory, no external services."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from keystone.db import ArtifactRow, ReportRow, Store, to_domain_listing
from keystone.listing import Attempt, Listing, ListingState
from keystone.payments import ChargeStatus, Currency, MockPaymentProvider, Money
from keystone.schema import (
    Audience,
    CertificationReport,
    Cost,
    Environment,
    FileEntry,
    Rating,
    Source,
    SourceKind,
    Status,
    Subject,
    SuiteResult,
)
from keystone.visibility import redact

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)

FILES = [
    FileEntry(path="config.json", size_bytes=120, sha256="a" * 64),
    FileEntry(path="model.safetensors", size_bytes=2048, sha256="b" * 64),
]


@pytest.fixture
def store() -> Store:
    s = Store("sqlite://")  # in-memory
    s.create_all()
    return s


def _report(report_id: str = "r1", grade: str = "B", sandboxed: bool = True):
    return CertificationReport(
        report_id=report_id,
        created_at=NOW,
        status=Status.PASS,
        subject=Subject(
            source=Source(kind=SourceKind.UPLOAD, ref="rcpt_1"),
            artifact_digest="d" * 64,
            files=FILES,
            total_bytes=2168,
        ),
        environment=Environment(sandboxed=sandboxed, seed=0),
        suite_results=[
            SuiteResult(
                suite_id="heldout", suite_version="1", status=Status.PASS,
                held_out=True, score=0.88, metrics={"x": 0.88},
            )
        ],
        cost=Cost(gpu_seconds=81.0, usd_estimate=0.0248),
        rating=Rating(grade=grade, as_tested_at=NOW),
    )


def _seed_listing(store: Store, s, listing_id="l1", digest="d" * 64):
    store.upsert_user(s, "c1", "creator@example.com")
    store.put_artifact(s, digest, FILES, 2168)
    return store.create_listing(s, listing_id, "c1", digest, title="Demo model")


# --------------------------------------------------------------------------
# artifacts
# --------------------------------------------------------------------------

def test_artifact_is_idempotent_by_digest(store: Store) -> None:
    """Content-addressed: the same weights uploaded twice are one row."""
    with store.session() as s:
        store.put_artifact(s, "d" * 64, FILES, 2168)
        store.put_artifact(s, "d" * 64, FILES, 2168)
        s.commit()
        assert s.query(ArtifactRow).count() == 1


def test_artifact_manifest_round_trips(store: Store) -> None:
    with store.session() as s:
        store.put_artifact(s, "d" * 64, FILES, 2168)
        s.commit()
        row = s.get(ArtifactRow, "d" * 64)
        assert [f.path for f in row.files()] == ["config.json", "model.safetensors"]
        assert row.files()[1].sha256 == "b" * 64


# --------------------------------------------------------------------------
# listings
# --------------------------------------------------------------------------

def test_listing_round_trips_to_domain(store: Store) -> None:
    with store.session() as s:
        _seed_listing(store, s)
        s.commit()
        listing = store.load_listing(s, "l1")

    assert isinstance(listing, Listing)
    assert listing.state is ListingState.DRAFT
    assert listing.creator_id == "c1"
    assert listing.attempts == []


def test_state_and_attempts_persist(store: Store) -> None:
    with store.session() as s:
        _seed_listing(store, s)
        s.commit()

        listing = store.load_listing(s, "l1")
        listing.state = ListingState.CERTIFYING
        listing.record(Attempt("a1", "d" * 64, NOW, grade="B", passed=True, internal_score=0.88))
        store.save_listing(s, listing)
        s.commit()

    with store.session() as s:
        reloaded = store.load_listing(s, "l1")

    assert reloaded.state is ListingState.CERTIFIED
    assert len(reloaded.attempts) == 1
    assert reloaded.attempts[0].grade == "B"
    assert reloaded.attempts[0].internal_score == 0.88


def test_save_appends_only_new_attempts(store: Store) -> None:
    with store.session() as s:
        _seed_listing(store, s)
        s.commit()

        for i, digest in enumerate(("1" * 64, "2" * 64)):
            listing = store.load_listing(s, "l1")
            listing.state = ListingState.CERTIFYING
            listing.record(Attempt(f"a{i}", digest, NOW + timedelta(days=i), passed=False))
            store.save_listing(s, listing)
            s.commit()

    with store.session() as s:
        assert len(store.load_listing(s, "l1").attempts) == 2


def test_attempt_order_is_stable(store: Store) -> None:
    """can_attempt() reads attempts[-1], so ordering is load-bearing."""
    with store.session() as s:
        _seed_listing(store, s)
        s.commit()
        listing = store.load_listing(s, "l1")
        for i in range(3):
            listing.attempts.append(Attempt(f"a{i}", f"{i}" * 64, NOW + timedelta(hours=i)))
        store.save_listing(s, listing)
        s.commit()

    with store.session() as s:
        got = store.load_listing(s, "l1")
        assert [a.attempt_id for a in got.attempts] == ["a0", "a1", "a2"]


def test_probing_flag_persists(store: Store) -> None:
    with store.session() as s:
        _seed_listing(store, s)
        s.commit()
        listing = store.load_listing(s, "l1")
        for i, score in enumerate((0.700, 0.720, 0.735)):
            listing.state = ListingState.CERTIFYING
            listing.record(Attempt(f"a{i}", f"{i}" * 64, NOW, passed=False, internal_score=score))
        store.save_listing(s, listing)
        s.commit()

    with store.session() as s:
        assert store.load_listing(s, "l1").flagged_for_review
        assert [r.id for r in store.flagged_listings(s)] == ["l1"]


def test_listings_in_state_query(store: Store) -> None:
    with store.session() as s:
        _seed_listing(store, s, "l1")
        _seed_listing(store, s, "l2")
        s.commit()
        listing = store.load_listing(s, "l2")
        listing.state = ListingState.PENDING_CERTIFICATION
        store.save_listing(s, listing)
        s.commit()

        pending = store.listings_in_state(s, ListingState.PENDING_CERTIFICATION)
        assert [r.id for r in pending] == ["l2"]


# --------------------------------------------------------------------------
# reports
# --------------------------------------------------------------------------

def test_report_round_trips(store: Store) -> None:
    with store.session() as s:
        _seed_listing(store, s)
        store.put_report(s, _report(), listing_id="l1", attempt_id="a1")
        s.commit()

    with store.session() as s:
        got = store.get_report(s, "r1")

    assert got.rating.grade == "B"
    assert got.suite_results[0].score == 0.88  # stored unredacted
    assert got.cost.gpu_seconds == 81.0


def test_indexed_columns_match_payload(store: Store) -> None:
    """The lifted columns exist so we never query JSON to make a decision."""
    with store.session() as s:
        _seed_listing(store, s)
        store.put_report(s, _report(grade="A", sandboxed=False), listing_id="l1")
        s.commit()
        row = s.get(ReportRow, "r1")

    assert row.grade == "A"
    assert row.sandboxed is False
    assert row.artifact_digest == "d" * 64


def test_latest_report_wins(store: Store) -> None:
    with store.session() as s:
        _seed_listing(store, s)
        first = _report("r1", grade="D")
        second = _report("r2", grade="A")
        second.created_at = NOW + timedelta(days=1)
        store.put_report(s, first, listing_id="l1")
        store.put_report(s, second, listing_id="l1")
        s.commit()

        assert store.latest_report(s, "l1").rating.grade == "A"


def test_stored_report_is_unredacted_and_must_be_redacted_on_the_way_out(store: Store) -> None:
    """The database holds the internal truth; redaction happens at the boundary."""
    with store.session() as s:
        _seed_listing(store, s)
        store.put_report(s, _report(), listing_id="l1")
        s.commit()
        stored = store.get_report(s, "r1")

    assert stored.cost is not None and stored.suite_results[0].score is not None

    for audience in (Audience.BUYER, Audience.CREATOR):
        view = redact(stored, audience)
        assert view.cost is None
        assert view.suite_results[0].score is None


# --------------------------------------------------------------------------
# charges
# --------------------------------------------------------------------------

def test_charge_mirror_tracks_provider(store: Store) -> None:
    provider = MockPaymentProvider()
    charge = provider.create_charge(Money(25_000_000, Currency.USDC), "l1")

    with store.session() as s:
        store.put_charge(s, charge)
        s.commit()
        assert store.get_charge(s, charge.charge_id).status is ChargeStatus.PENDING

        # Settlement is re-read from the provider, never taken from a client.
        store.put_charge(s, provider.settle(charge.charge_id))
        s.commit()

        mirrored = store.get_charge(s, charge.charge_id)
        assert mirrored.status is ChargeStatus.SETTLED
        assert mirrored.is_settled
        assert mirrored.amount == Money(25_000_000, Currency.USDC)


def test_settled_charge_satisfies_the_gate(store: Store) -> None:
    """End to end: persisted charge unlocks a persisted listing's next attempt."""
    provider = MockPaymentProvider()
    charge = provider.create_charge(Money(25_000_000, Currency.USDC), "l1")
    provider.settle(charge.charge_id)

    with store.session() as s:
        _seed_listing(store, s)
        store.put_charge(s, charge)
        s.commit()

        listing = store.load_listing(s, "l1")
        mirrored = store.get_charge(s, charge.charge_id)
        ok, reason = listing.can_attempt(NOW, charge=mirrored)

    assert ok, reason


def test_unsettled_persisted_charge_does_not(store: Store) -> None:
    provider = MockPaymentProvider()
    charge = provider.create_charge(Money(25_000_000, Currency.USDC), "l1")

    with store.session() as s:
        _seed_listing(store, s)
        store.put_charge(s, charge)
        s.commit()
        ok, reason = store.load_listing(s, "l1").can_attempt(
            NOW, charge=store.get_charge(s, charge.charge_id)
        )

    assert not ok and "not settled" in reason
