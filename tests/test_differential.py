"""Differential evaluation: what the fine-tune changed relative to its base.

The number a buyer needs is the difference, not the absolute score -- they can
download the base for free. And the row nobody else reports is the one where a
seller improved what they were optimising and degraded what they were not
watching.
"""

from __future__ import annotations

import pytest

from keystone.differential import (
    attach_baseline,
    baseline_ref,
    regressions,
    summarize,
)
from keystone.schema import Audience, ParentEdge, Status, SuiteResult
from keystone.visibility import assert_no_leak, redact


def result(
    suite_id: str = "bench",
    score: float | None = 0.8,
    status: Status = Status.PASS,
    *,
    gate: bool = False,
    held_out: bool = False,
) -> SuiteResult:
    return SuiteResult(
        suite_id=suite_id,
        suite_version="1",
        display_name=suite_id.replace("_", " ").title(),
        status=status,
        score=score,
        gate=gate,
        held_out=held_out,
    )


# --------------------------------------------------------------------------
# picking something to compare against
# --------------------------------------------------------------------------

def test_a_single_declared_base_is_the_comparison() -> None:
    lineage = [ParentEdge(role="base", ref="meta-llama/Llama-3.1-8B")]
    assert baseline_ref(lineage) == "meta-llama/Llama-3.1-8B"


def test_no_lineage_means_no_comparison() -> None:
    assert baseline_ref([]) is None


def test_a_merge_has_no_single_before() -> None:
    """Two bases means no meaningful "the model it came from"."""
    lineage = [
        ParentEdge(role="base", ref="org/a"),
        ParentEdge(role="base", ref="org/b"),
    ]
    assert baseline_ref(lineage) is None


def test_component_parents_are_not_a_baseline() -> None:
    """A vision encoder is part of assembly, not the thing derived from."""
    lineage = [
        ParentEdge(role="vision_encoder", ref="org/clip"),
        ParentEdge(role="projector", ref="org/proj"),
    ]
    assert baseline_ref(lineage) is None


# --------------------------------------------------------------------------
# folding the baseline in
# --------------------------------------------------------------------------

def test_delta_is_computed() -> None:
    merged = attach_baseline([result(score=0.82)], [result(score=0.64)])
    assert merged[0].baseline_score == 0.64
    assert merged[0].delta == pytest.approx(0.18)


def test_a_regression_is_negative() -> None:
    """The headline case: safety got worse while capability got better."""
    merged = attach_baseline(
        [result("harm", score=0.71, gate=True)],
        [result("harm", score=0.97, gate=True)],
    )
    assert merged[0].delta == pytest.approx(-0.26)


def test_inputs_are_not_mutated() -> None:
    candidate = [result(score=0.82)]
    attach_baseline(candidate, [result(score=0.64)])
    assert candidate[0].delta is None


def test_a_suite_the_base_did_not_run_gets_no_delta() -> None:
    merged = attach_baseline([result("only_here", score=0.9)], [])
    assert merged[0].baseline_score is None and merged[0].delta is None


@pytest.mark.parametrize("bad", [Status.ERROR, Status.SKIPPED])
def test_a_failed_baseline_run_produces_no_delta(bad: Status) -> None:
    """Better no comparison than a misleading one."""
    merged = attach_baseline(
        [result(score=0.8)], [result(score=None, status=bad)]
    )
    assert merged[0].delta is None


def test_a_declined_candidate_run_produces_no_delta() -> None:
    merged = attach_baseline(
        [result(score=None, status=Status.SKIPPED)], [result(score=0.9)]
    )
    assert merged[0].delta is None


# --------------------------------------------------------------------------
# reading the comparison
# --------------------------------------------------------------------------

def test_regressions_respect_the_threshold() -> None:
    merged = attach_baseline(
        [result("noise", score=0.79), result("real", score=0.60)],
        [result("noise", score=0.80), result("real", score=0.90)],
    )
    assert [r.suite_id for r in regressions(merged)] == ["real"]


def test_a_safety_regression_is_called_out() -> None:
    merged = attach_baseline(
        [result("capability", score=0.90), result("harm", score=0.60, gate=True)],
        [result("capability", score=0.64), result("harm", score=0.95, gate=True)],
    )
    summary = summarize(merged)
    assert summary["improved"] == 1
    assert summary["regressed"] == 1
    assert summary["safety_regressed"] is True


def test_summary_only_counts_what_was_compared() -> None:
    merged = attach_baseline(
        [result("a", score=0.9), result("b", score=0.5)], [result("a", score=0.8)]
    )
    summary = summarize(merged)
    assert summary["compared"] == 1
    assert [row["suite_id"] for row in summary["rows"]] == ["a"]


# --------------------------------------------------------------------------
# redaction
# --------------------------------------------------------------------------

def test_a_held_out_delta_never_escapes() -> None:
    """A delta plus a public baseline reveals the held-out score by subtraction."""
    from datetime import datetime, timezone

    from keystone.schema import (
        CertificationReport,
        Rating,
        Source,
        SourceKind,
        Subject,
    )

    now = datetime(2026, 9, 5, tzinfo=timezone.utc)
    held = result("heldout", score=0.71, held_out=True)
    held.baseline_score = 0.97
    held.delta = -0.26

    report = CertificationReport(
        report_id="r1",
        created_at=now,
        status=Status.PASS,
        subject=Subject(
            source=Source(kind=SourceKind.UPLOAD, ref="rcpt"),
            artifact_digest="d" * 64,
            files=[],
            total_bytes=0,
        ),
        suite_results=[held],
        rating=Rating(grade="B", certified=True, as_tested_at=now),
    )

    for audience in (Audience.BUYER, Audience.CREATOR):
        view = redact(report, audience)
        entry = view.suite_results[0]
        assert entry.score is None
        assert entry.baseline_score is None
        assert entry.delta is None
        assert_no_leak(view, audience)

    internal = redact(report, Audience.INTERNAL).suite_results[0]
    assert internal.delta == -0.26


def test_a_public_suite_keeps_its_comparison() -> None:
    """Nothing to protect on a published benchmark, and the delta is the point."""
    from datetime import datetime, timezone

    from keystone.schema import (
        CertificationReport,
        Rating,
        Source,
        SourceKind,
        Subject,
    )

    now = datetime(2026, 9, 5, tzinfo=timezone.utc)
    public = result("public_bench", score=0.82)
    public.baseline_score = 0.64
    public.delta = 0.18

    report = CertificationReport(
        report_id="r1",
        created_at=now,
        status=Status.PASS,
        subject=Subject(
            source=Source(kind=SourceKind.UPLOAD, ref="rcpt"),
            artifact_digest="d" * 64,
            files=[],
            total_bytes=0,
        ),
        suite_results=[public],
        rating=Rating(grade="B", certified=True, as_tested_at=now),
    )
    view = redact(report, Audience.BUYER)
    assert view.suite_results[0].delta == 0.18
