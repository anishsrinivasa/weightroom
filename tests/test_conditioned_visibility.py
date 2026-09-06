"""Redaction for conditioning instruments.

Under conditioning the *threshold* is information. A creator who learns the
rate they had to clear has learned their capability band; one who also learns
the rate they achieved can binary-search the band edges across resubmissions,
and from there infer the probe's contents by watching which changes move the
number. So these suites are held stricter than ordinary held-out ones.
"""

from __future__ import annotations

import pytest

from datetime import datetime, timezone

from keystone.conditioning import resolve
from keystone.schema import (
    Audience,
    CertificationReport,
    Environment,
    Rating,
    Source,
    SourceKind,
    Status,
    Subject,
    SuiteResult,
)
from keystone.visibility import assert_no_leak, redact

PROBE_ID = "bio_probe"
ELICIT_ID = "bio_elicitation"

CREATOR_AND_BUYER = [Audience.CREATOR, Audience.BUYER]


def probe(score: float = 0.85) -> SuiteResult:
    return SuiteResult(
        suite_id=PROBE_ID, suite_version="1.0.0", status=Status.PASS, score=score,
        domain="bio", role="probe", internal=True, chance_floor=0.25,
        metrics={"accuracy": score}, n_items=100,
    )


def elicitation(score: float = 0.96) -> SuiteResult:
    return SuiteResult(
        suite_id=ELICIT_ID, suite_version="1.0.0",
        display_name="Bio elicitation resistance", status=Status.PASS, score=score,
        domain="bio", role="elicitation", internal=True, gate=True, held_out=True,
        conditioned_by=PROBE_ID, categories=["harmful_content_refusal"],
        remediation="public/bio_practice_v1", metrics={"refusal_rate": score},
        n_items=800,
    )


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def report_with(results: list[SuiteResult], *, digest_char: str = "a") -> CertificationReport:
    return CertificationReport(
        report_id="rep_test",
        created_at=NOW,
        status=Status.PASS,
        subject=Subject(
            source=Source(kind=SourceKind.UPLOAD, ref="rcpt"),
            artifact_digest=digest_char * 64,
            files=[],
            total_bytes=0,
        ),
        environment=Environment(sandboxed=True, seed=0),
        rating=Rating(as_tested_at=NOW, certified=False, grade="B"),
        suite_results=results,
    )


def report_for(probe_score: float, refusal: float) -> CertificationReport:
    return report_with(resolve([probe(probe_score), elicitation(refusal)]))


def find(report: CertificationReport, suite_id: str) -> SuiteResult | None:
    return next((r for r in report.suite_results if r.suite_id == suite_id), None)


# --------------------------------------------------------------------------
# the probe never leaves
# --------------------------------------------------------------------------

@pytest.mark.parametrize("audience", CREATOR_AND_BUYER)
def test_the_probe_row_does_not_survive_redaction(audience: Audience) -> None:
    """A probe row is nothing but the number a creator must not see.

    Redacting its score would leave a row whose only remaining content is that
    a probe ran -- so it is dropped rather than blanked.
    """
    view = redact(report_for(0.85, 0.80), audience)
    assert find(view, PROBE_ID) is None
    assert_no_leak(view, audience)


def test_internal_view_keeps_everything() -> None:
    view = redact(report_for(0.85, 0.80), Audience.INTERNAL)
    kept = find(view, PROBE_ID)
    assert kept is not None and kept.score == 0.85
    gate = find(view, ELICIT_ID)
    assert gate.threshold_required is not None
    assert "band 'high'" in gate.threshold_basis
    assert "tolerated" in gate.threshold_basis


# --------------------------------------------------------------------------
# the creator gets a domain and nothing measurable
# --------------------------------------------------------------------------

def test_creator_learns_which_domain_failed_but_no_numbers() -> None:
    """A rejection has to name the domain or it is unactionable. It must not
    name anything a resubmission could triangulate on."""
    view = redact(report_for(0.85, 0.80), Audience.CREATOR)
    gate = find(view, ELICIT_ID)

    assert gate is not None
    assert gate.domain == "bio"
    assert gate.display_name == "Bio elicitation resistance"
    assert gate.status is Status.FAIL
    assert gate.remediation == "public/bio_practice_v1"

    assert gate.score is None
    assert gate.threshold_required is None
    assert gate.threshold_basis is None
    assert gate.metrics == {}
    assert gate.n_items is None
    assert_no_leak(view, Audience.CREATOR)


def test_conditioned_gate_gets_no_coarse_band_either() -> None:
    """Stricter than an ordinary held-out suite.

    A held-out suite is allowed a coarse band because its bar is fixed and
    public. A conditioned one is not: band plus a published threshold table
    brackets both the score and the capability behind it.
    """
    view = redact(report_for(0.85, 0.80), Audience.CREATOR)
    assert find(view, ELICIT_ID).score_band is None

    ordinary = SuiteResult(
        suite_id="harmbench", suite_version="1.0.0", status=Status.PASS,
        gate=True, held_out=True, score=0.94,
    )
    plain = report_with([ordinary], digest_char="b")
    assert redact(plain, Audience.CREATOR).suite_results[0].score_band == "high"


def test_buyer_sees_no_internal_row_at_all() -> None:
    view = redact(report_for(0.85, 0.80), Audience.BUYER)
    assert find(view, ELICIT_ID) is None
    assert find(view, PROBE_ID) is None
    assert_no_leak(view, Audience.BUYER)


# --------------------------------------------------------------------------
# the attack this exists to stop
# --------------------------------------------------------------------------

def test_repeated_submissions_cannot_bracket_the_threshold() -> None:
    """Walk a model up through every band and confirm nothing observable moves.

    This is the binary search the redaction exists to defeat: submit, read the
    feedback, adjust, resubmit. If any field varied with the probe score, the
    band edges would fall out in a handful of attempts.
    """
    observed = set()
    for probe_score in (0.25, 0.45, 0.55, 0.70, 0.85, 0.95, 1.0):
        view = redact(report_for(probe_score, 0.86), Audience.CREATOR)
        gate = find(view, ELICIT_ID)
        observed.add((
            gate.status,
            gate.score,
            gate.score_band,
            gate.threshold_required,
            gate.threshold_basis,
            gate.conditioned_verdict,
            tuple(gate.categories),
        ))

    # Exactly two observations: passed, and failed. The verdict has to differ
    # -- that is the product working -- and nothing else may, so a creator can
    # learn "bio failed" and cannot learn where the bar sat.
    #
    # This caught a real leak. `conditioned_verdict` separates "not gated at
    # all" from "gated and cleared it" where `status` shows PASS for both, so
    # two submissions either side of the negligible/low edge would have located
    # that boundary. It is now internal-only.
    assert len(observed) == 2
    assert {row[0] for row in observed} == {Status.FAIL, Status.PASS}
    assert all(all(field is None for field in row[1:6]) for row in observed)


def test_threshold_is_internal_even_on_a_public_suite() -> None:
    """The rule follows the field, not the suite.

    If a public benchmark were ever conditioned, its required rate would name a
    capability band just as loudly.
    """
    public = SuiteResult(
        suite_id="public_capability", suite_version="1.0.0", status=Status.PASS,
        score=0.80, threshold_required=0.98, threshold_basis="probe: band 'high'",
    )
    report = report_with([public], digest_char="c")
    view = redact(report, Audience.CREATOR)
    assert view.suite_results[0].threshold_required is None
    assert view.suite_results[0].threshold_basis is None
    assert_no_leak(view, Audience.CREATOR)


# --------------------------------------------------------------------------
# the guard catches a hand-built leak
# --------------------------------------------------------------------------

def _report_with(result: SuiteResult) -> CertificationReport:
    return report_with([result], digest_char="d")


@pytest.mark.parametrize("audience", CREATOR_AND_BUYER)
def test_assert_no_leak_catches_a_surviving_probe(audience: Audience) -> None:
    with pytest.raises(AssertionError, match="probe"):
        assert_no_leak(_report_with(probe()), audience)


@pytest.mark.parametrize("audience", CREATOR_AND_BUYER)
def test_assert_no_leak_catches_a_threshold(audience: Audience) -> None:
    leaky = elicitation().model_copy(update={
        "score": None, "score_band": None, "metrics": {}, "n_items": None,
        "threshold_required": 0.985,
    })
    with pytest.raises(AssertionError, match="threshold"):
        assert_no_leak(_report_with(leaky), audience)


def test_assert_no_leak_catches_a_scored_internal_suite() -> None:
    leaky = elicitation().model_copy(update={"threshold_required": None, "metrics": {}})
    with pytest.raises(AssertionError, match="leaked a score"):
        assert_no_leak(_report_with(leaky), Audience.CREATOR)


def test_assert_no_leak_catches_an_internal_row_shown_to_a_buyer() -> None:
    hidden = elicitation().model_copy(update={
        "score": None, "score_band": None, "metrics": {}, "n_items": None,
        "threshold_required": None, "threshold_basis": None,
    })
    with pytest.raises(AssertionError, match="not buyer-visible"):
        assert_no_leak(_report_with(hidden), Audience.BUYER)
