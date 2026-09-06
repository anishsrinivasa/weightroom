"""Tests for the publish gate: redaction, storage, and attempt policy.

The redaction tests matter most. Certification gates listing, so these are what
stand between a determined creator and our held-out eval set.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from keystone.ingest import ingest_upload
from keystone.listing import (
    Attempt,
    AttemptPolicy,
    Listing,
    ListingState,
    TransitionError,
    can_transition,
    detect_probing,
    transition,
)
from keystone.schema import (
    Audience,
    CertificationReport,
    Cost,
    Finding,
    Rating,
    ScanResult,
    Severity,
    SourceKind,
    Status,
    Subject,
    SuiteResult,
)
from keystone.payments import (
    ChargeStatus,
    Currency,
    Money,
    MockPaymentProvider,
    PaymentProvider,
    RefundNotSupported,
)
from keystone.storage import LocalStore, artifact_key
from keystone.visibility import assert_no_leak, band, redact

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def uploaded(tmp_path: Path) -> Path:
    root = tmp_path / "upload"
    root.mkdir()
    (root / "config.json").write_text('{"architectures": ["LlamaForCausalLM"]}')
    (root / "model.safetensors").write_bytes(b"\x00" * 2048)
    return root


def _report(**overrides) -> CertificationReport:
    held_out = SuiteResult(
        suite_id="heldout_harm",
        suite_version="1.0.0",
        status=Status.FAIL,
        held_out=True,
        score=0.61,
        metrics={"refusal_rate": 0.61, "n_failed": 14.0},
        n_items=200,
        duration_s=412.0,
        categories=["harmful_content_refusal"],
        remediation="public/harm_practice_v1",
        findings=[
            Finding(
                id="probe_0042",
                severity=Severity.HIGH,
                summary="Complied with probe 42",
                detail="verbatim held-out prompt text",
                visibility=Audience.INTERNAL,
            )
        ],
    )
    public = SuiteResult(
        suite_id="public_practice",
        suite_version="1.0.0",
        status=Status.PASS,
        held_out=False,
        score=0.93,
        metrics={"accuracy": 0.93},
        n_items=50,
        findings=[
            Finding(
                id="minor",
                severity=Severity.LOW,
                summary="Occasional verbosity",
                visibility=Audience.CREATOR,
            )
        ],
    )
    base = dict(
        report_id="r1",
        created_at=NOW,
        status=Status.FAIL,
        subject=Subject(
            source={"kind": SourceKind.UPLOAD, "ref": "rcpt_1"},
            artifact_digest="d" * 64,
            files=[],
            total_bytes=0,
        ),
        scans=[
            ScanResult(
                scanner="picklescan",
                status=Status.WARN,
                findings=[{"file": "x.bin", "globals": ["posix.system"]}],
            )
        ],
        suite_results=[held_out, public],
        cost=Cost(gpu_seconds=900.0, usd_estimate=0.62),
        rating=Rating(grade="D", as_tested_at=NOW),
    )
    base.update(overrides)
    return CertificationReport(**base)


# --------------------------------------------------------------------------
# redaction  -- the eval-set leak boundary
# --------------------------------------------------------------------------

def test_internal_view_is_untouched() -> None:
    out = redact(_report(), Audience.INTERNAL)
    held = out.suite_results[0]
    assert held.score == 0.61
    assert held.metrics and held.findings
    assert out.cost is not None


@pytest.mark.parametrize("audience", [Audience.CREATOR, Audience.BUYER])
def test_held_out_detail_never_escapes(audience: Audience) -> None:
    held = redact(_report(), audience).suite_results[0]
    assert held.score is None
    assert held.metrics == {}
    assert held.findings == []
    assert held.n_items is None
    assert held.score_band == "moderate"  # coarse band survives, exact value does not


@pytest.mark.parametrize("audience", [Audience.CREATOR, Audience.BUYER])
def test_cost_is_internal_only(audience: Audience) -> None:
    assert redact(_report(), audience).cost is None


def test_creator_gets_category_and_remediation() -> None:
    held = redact(_report(), Audience.CREATOR).suite_results[0]
    assert held.categories == ["harmful_content_refusal"]
    assert held.remediation == "public/harm_practice_v1"


def test_buyer_gets_neither() -> None:
    out = redact(_report(), Audience.BUYER)
    assert out.suite_results[0].categories == []
    assert out.suite_results[0].remediation is None
    assert out.scans[0].findings == []  # verdict yes, recipe no


def test_public_suite_keeps_its_detail() -> None:
    """A published practice suite leaks nothing, and detail is what makes it useful."""
    public = redact(_report(), Audience.CREATOR).suite_results[1]
    assert public.score == 0.93
    assert public.metrics == {"accuracy": 0.93}
    assert len(public.findings) == 1


def test_finding_visibility_is_respected() -> None:
    public = redact(_report(), Audience.BUYER).suite_results[1]
    assert public.findings == []  # the finding was CREATOR-level


@pytest.mark.parametrize("audience", [Audience.CREATOR, Audience.BUYER])
def test_assert_no_leak_accepts_redacted(audience: Audience) -> None:
    assert_no_leak(redact(_report(), audience), audience)


@pytest.mark.parametrize("audience", [Audience.CREATOR, Audience.BUYER])
def test_assert_no_leak_rejects_raw(audience: Audience) -> None:
    with pytest.raises(AssertionError):
        assert_no_leak(_report(), audience)


@pytest.mark.parametrize(
    "score,expected",
    [(1.0, "high"), (0.9, "high"), (0.8, "good"), (0.61, "moderate"), (0.4, "low"), (0.0, "poor")],
)
def test_bands(score: float, expected: str) -> None:
    assert band(score) == expected


def test_bands_are_coarse_enough_to_resist_search() -> None:
    """Two scores either side of a real threshold must be indistinguishable."""
    assert band(0.61) == band(0.74)


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def test_upload_is_content_addressed_and_dedupes(tmp_path: Path, uploaded: Path) -> None:
    store = LocalStore(tmp_path / "store")
    subject, written = ingest_upload(uploaded, "rcpt_1", store)
    assert subject.source.kind is SourceKind.UPLOAD
    assert written == subject.total_bytes

    again, written_again = ingest_upload(uploaded, "rcpt_2", store)
    assert again.artifact_digest == subject.artifact_digest
    assert written_again == 0  # identical weights cost one copy


def test_materialize_verifies_integrity(tmp_path: Path, uploaded: Path) -> None:
    store = LocalStore(tmp_path / "store")
    subject, _ = ingest_upload(uploaded, "rcpt_1", store)

    dest = tmp_path / "out"
    store.materialize(subject.artifact_digest, subject.files, dest)
    assert (dest / "model.safetensors").read_bytes() == b"\x00" * 2048


def test_corrupted_blob_is_a_hard_error(tmp_path: Path, uploaded: Path) -> None:
    store = LocalStore(tmp_path / "store")
    subject, _ = ingest_upload(uploaded, "rcpt_1", store)

    key = artifact_key(subject.artifact_digest, "model.safetensors")
    (store.root / key).write_bytes(b"\xff" * 2048)

    with pytest.raises(ValueError, match="integrity failure"):
        store.materialize(subject.artifact_digest, subject.files, tmp_path / "out")


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------

def test_publish_path() -> None:
    s = ListingState.DRAFT
    for nxt in (
        ListingState.PENDING_CERTIFICATION,
        ListingState.CERTIFYING,
        ListingState.CERTIFIED,
        ListingState.LISTED,
    ):
        s = transition(s, nxt)
    assert s is ListingState.LISTED


def test_cannot_list_without_certifying() -> None:
    assert not can_transition(ListingState.DRAFT, ListingState.LISTED)
    with pytest.raises(TransitionError):
        transition(ListingState.DRAFT, ListingState.LISTED)


def test_listed_can_be_pulled_by_recertification() -> None:
    assert can_transition(ListingState.LISTED, ListingState.PENDING_CERTIFICATION)
    assert can_transition(ListingState.LISTED, ListingState.DELISTED)
    assert can_transition(ListingState.LISTED, ListingState.CERTIFIED)


def test_withdrawn_is_terminal() -> None:
    assert not can_transition(ListingState.WITHDRAWN, ListingState.DRAFT)


# --------------------------------------------------------------------------
# attempt policy  -- the anti-probing layer
# --------------------------------------------------------------------------

def _listing(**kw) -> Listing:
    return Listing(listing_id="l1", creator_id="c1", artifact_digest="a" * 64, **kw)


def _paid(provider: MockPaymentProvider, listing_id: str = "l1"):
    """A settled charge for the default fee."""
    charge = provider.create_charge(AttemptPolicy().fee, listing_id)
    return provider.settle(charge.charge_id)


@pytest.fixture
def payments() -> MockPaymentProvider:
    return MockPaymentProvider()


def test_first_attempt_allowed(payments: MockPaymentProvider) -> None:
    assert _listing().can_attempt(NOW, charge=_paid(payments))[0]


def test_attempt_requires_payment() -> None:
    ok, reason = _listing().can_attempt(NOW)
    assert not ok and "payment required" in reason


def test_unsettled_charge_is_refused(payments: MockPaymentProvider) -> None:
    charge = payments.create_charge(AttemptPolicy().fee, "l1")
    ok, reason = _listing().can_attempt(NOW, charge=charge)
    assert not ok and "not settled" in reason


def test_partially_confirmed_charge_is_refused() -> None:
    provider = MockPaymentProvider(required_confirmations=3)
    charge = provider.create_charge(AttemptPolicy().fee, "l1")
    provider.confirm(charge.charge_id, confirmations=1)
    ok, reason = _listing().can_attempt(NOW, charge=provider.get_charge(charge.charge_id))
    assert not ok and "confirming" in reason


def test_underpayment_is_refused(payments: MockPaymentProvider) -> None:
    charge = payments.create_charge(Money(1, Currency.USDC), "l1")
    payments.settle(charge.charge_id)
    ok, reason = _listing().can_attempt(NOW, charge=charge)
    assert not ok and "underpaid" in reason


def test_charge_for_another_listing_is_refused(payments: MockPaymentProvider) -> None:
    ok, reason = _listing().can_attempt(NOW, charge=_paid(payments, "someone-elses-listing"))
    assert not ok and "does not reference" in reason


def test_a_charge_cannot_be_spent_twice(payments: MockPaymentProvider) -> None:
    charge = _paid(payments)
    listing = _listing(state=ListingState.REJECTED)
    listing.attempts = [
        Attempt("a1", "b" * 64, NOW - timedelta(days=1), charge_id=charge.charge_id)
    ]
    ok, reason = listing.can_attempt(NOW, charge=charge)
    assert not ok and "already spent" in reason


def test_rate_limit_is_checked_before_payment() -> None:
    """Never take money from someone we are about to reject on cooldown."""
    listing = _listing(state=ListingState.REJECTED)
    listing.attempts = [Attempt("a1", "b" * 64, NOW - timedelta(hours=1))]
    ok, reason = listing.can_attempt(NOW)  # no charge supplied at all
    assert not ok and "cooldown" in reason  # not "payment required"


def test_cooldown_blocks_rapid_resubmission() -> None:
    listing = _listing(state=ListingState.REJECTED)
    listing.attempts = [Attempt("a1", "b" * 64, NOW - timedelta(hours=1))]
    ok, reason = listing.can_attempt(NOW)
    assert not ok and "cooldown" in reason


def test_cooldown_accepts_naive_database_timestamp() -> None:
    listing = _listing(state=ListingState.REJECTED)
    stored = (NOW - timedelta(hours=1)).replace(tzinfo=None)
    listing.attempts = [Attempt("a1", "b" * 64, stored)]
    ok, reason = listing.can_attempt(NOW)
    assert not ok and "cooldown" in reason


def test_cooldown_expires(payments: MockPaymentProvider) -> None:
    listing = _listing(state=ListingState.REJECTED)
    listing.attempts = [Attempt("a1", "b" * 64, NOW - timedelta(hours=7))]
    assert listing.can_attempt(NOW, charge=_paid(payments))[0]


def test_unchanged_artifact_is_refused() -> None:
    """Resubmitting the same bytes samples our eval set and fixes nothing."""
    listing = _listing(state=ListingState.REJECTED)
    listing.attempts = [Attempt("a1", "a" * 64, NOW - timedelta(days=1))]
    ok, reason = listing.can_attempt(NOW)
    assert not ok and "unchanged" in reason


def test_attempt_cap() -> None:
    listing = _listing(state=ListingState.REJECTED)
    listing.attempts = [
        Attempt(f"a{i}", f"{i}" * 64, NOW - timedelta(days=10 - i)) for i in range(5)
    ]
    ok, reason = listing.can_attempt(NOW)
    assert not ok and "attempt limit" in reason


def test_rotation_advances_with_attempts() -> None:
    policy = AttemptPolicy(rotate_held_out_after=2)
    listing = _listing()
    seen = []
    for i in range(6):
        seen.append(listing.rotation_index(policy))
        listing.attempts.append(Attempt(f"a{i}", f"{i}" * 64, NOW))
    assert seen == [0, 0, 1, 1, 2, 2]


def test_probing_signature_is_flagged() -> None:
    """Small monotonic score creep is hill-climbing, not fixing."""
    attempts = [
        Attempt("a1", "1" * 64, NOW, internal_score=0.700),
        Attempt("a2", "2" * 64, NOW, internal_score=0.720),
        Attempt("a3", "3" * 64, NOW, internal_score=0.735),
    ]
    assert detect_probing(attempts)


def test_genuine_fix_is_not_flagged() -> None:
    attempts = [
        Attempt("a1", "1" * 64, NOW, internal_score=0.40),
        Attempt("a2", "2" * 64, NOW, internal_score=0.55),
        Attempt("a3", "3" * 64, NOW, internal_score=0.91),
    ]
    assert not detect_probing(attempts)


def test_recording_a_failure_rejects_and_can_flag() -> None:
    listing = _listing(state=ListingState.CERTIFYING)
    for i, score in enumerate((0.700, 0.720, 0.735)):
        listing.state = ListingState.CERTIFYING
        listing.record(Attempt(f"a{i}", f"{i}" * 64, NOW, passed=False, internal_score=score))
    assert listing.state is ListingState.REJECTED
    assert listing.flagged_for_review


def test_recording_a_pass_certifies() -> None:
    listing = _listing(state=ListingState.CERTIFYING)
    listing.record(Attempt("a1", "b" * 64, NOW, grade="A", passed=True, internal_score=0.95))
    assert listing.state is ListingState.CERTIFIED
    assert not listing.flagged_for_review


# --------------------------------------------------------------------------
# money  -- USDC has six decimals, and getting that wrong is a 10,000x error
# --------------------------------------------------------------------------

def test_usdc_and_usd_have_different_precision() -> None:
    assert Currency.USDC.decimals == 6
    assert Currency.USD.decimals == 2


def test_from_decimal_scales_by_currency() -> None:
    assert Money.from_decimal("25.00", Currency.USDC).amount_minor == 25_000_000
    assert Money.from_decimal("25.00", Currency.USD).amount_minor == 2_500


def test_round_trip() -> None:
    money = Money.from_decimal("1234.567890", Currency.USDC)
    assert money.amount_minor == 1_234_567_890
    assert money.to_decimal() == Decimal("1234.56789")


def test_floats_are_rejected() -> None:
    with pytest.raises(TypeError):
        Money(25.0, Currency.USDC)  # type: ignore[arg-type]


def test_negative_amounts_are_rejected() -> None:
    with pytest.raises(ValueError):
        Money(-1, Currency.USDC)


def test_convert_between_pegged_units() -> None:
    assert Money(2_500, Currency.USD).convert_to(Currency.USDC).amount_minor == 25_000_000
    assert Money(25_000_000, Currency.USDC).convert_to(Currency.USD).amount_minor == 2_500


def test_formatting() -> None:
    assert str(Money(25_000_000, Currency.USDC)) == "25.000000 USDC"


# --------------------------------------------------------------------------
# payment provider
# --------------------------------------------------------------------------

def test_mock_is_a_payment_provider(payments: MockPaymentProvider) -> None:
    assert isinstance(payments, PaymentProvider)


def test_charge_starts_pending(payments: MockPaymentProvider) -> None:
    charge = payments.create_charge(Money(25_000_000), "l1")
    assert charge.status is ChargeStatus.PENDING
    assert not charge.is_settled
    assert charge.chain == "base"  # stablecoin charges carry chain + address
    assert charge.address


def test_confirmations_gate_settlement() -> None:
    provider = MockPaymentProvider(required_confirmations=3)
    charge = provider.create_charge(Money(25_000_000), "l1")
    for n, expected in ((1, ChargeStatus.CONFIRMING), (2, ChargeStatus.CONFIRMING), (3, ChargeStatus.SETTLED)):
        assert provider.confirm(charge.charge_id, n).status is expected


def test_expiry(payments: MockPaymentProvider) -> None:
    charge = payments.create_charge(Money(25_000_000), "l1", ttl=timedelta(hours=1))
    assert not charge.is_expired(charge.created_at)
    assert charge.is_expired(charge.created_at + timedelta(hours=2))


def test_refund_is_not_supported_on_chain(payments: MockPaymentProvider) -> None:
    """On-chain transfers are irreversible; a refund is a fresh outbound payout."""
    charge = _paid(payments)
    with pytest.raises(RefundNotSupported):
        payments.refund(charge.charge_id)


def test_payout_settles(payments: MockPaymentProvider) -> None:
    payout = payments.create_payout("0xcreator", Money(10_000_000), "l1")
    assert payout.status is ChargeStatus.SETTLED
    assert payout.tx_hash
