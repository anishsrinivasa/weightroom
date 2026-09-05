"""Differential evaluation: what did this fine-tune actually change?

An absolute score answers the wrong question. A buyer can download the base
model for free, so the thing worth paying for is the difference -- and the
thing worth knowing is what the difference cost.

    Benchmark           Base    Fine-tune
    Legal reasoning      64%      82%   ↑ what they are selling
    Harmful refusal      97%      71%   ↓ what they broke getting there

The second row is the one nobody else reports. Fine-tuning is known to erode
alignment even when the training data is benign, and it happens silently: the
seller measures the capability they were optimising and never looks at what
regressed.

Producing this needs three things at once -- a verified derivation chain, a
serving plane that can stand up the parent, and a content-addressed cache that
makes the parent nearly free after the first fetch. A benchmark house sees one
model; a marketplace sees the lineage.

Suites stay single-model. The platform runs an eligible suite twice and diffs
the results, so nothing about authoring a benchmark changes.
"""

from __future__ import annotations

from keystone.schema import ParentEdge, Status, SuiteResult


def baseline_ref(lineage: list[ParentEdge]) -> str | None:
    """The model to compare against, or None if there is nothing to compare to.

    Only a `base` parent counts. A vision encoder or a merge component is part
    of how the artifact was assembled, not the thing it was derived from, and
    diffing against one would produce a meaningless number.
    """
    bases = [p for p in lineage if p.role == "base" and p.ref]
    if len(bases) != 1:
        # No declared base, or a merge of several. Neither has a single
        # meaningful "before".
        return None
    return bases[0].ref


def attach_baseline(
    candidate: list[SuiteResult], baseline: list[SuiteResult]
) -> list[SuiteResult]:
    """Fold baseline scores into the candidate's results.

    Returns new objects; the inputs are left alone so a caller can still report
    the raw runs. A suite that errored or was skipped on either side gets no
    delta rather than a misleading one.
    """
    by_id = {r.suite_id: r for r in baseline}
    merged: list[SuiteResult] = []

    for result in candidate:
        base = by_id.get(result.suite_id)
        out = result.model_copy(deep=True)
        if (
            base is not None
            and base.score is not None
            and result.score is not None
            and base.status not in (Status.ERROR, Status.SKIPPED)
            and result.status not in (Status.ERROR, Status.SKIPPED)
        ):
            out.baseline_score = base.score
            out.delta = round(result.score - base.score, 4)
        merged.append(out)
    return merged


def regressions(results: list[SuiteResult], threshold: float = 0.05) -> list[SuiteResult]:
    """Suites where the fine-tune is materially worse than its base.

    A safety gate that regressed is the headline finding of the whole run: the
    seller improved the thing they were optimising and degraded something they
    were not watching.
    """
    return [
        r for r in results
        if r.delta is not None and r.delta <= -abs(threshold)
    ]


def summarize(results: list[SuiteResult]) -> dict:
    """Compact comparison for a model page."""
    compared = [r for r in results if r.delta is not None]
    lost = regressions(results)
    return {
        "compared": len(compared),
        "improved": sum(1 for r in compared if r.delta > 0),
        "regressed": len(lost),
        "safety_regressed": any(r.gate for r in lost),
        "rows": [
            {
                "suite_id": r.suite_id,
                "display_name": r.display_name or r.suite_id,
                "gate": r.gate,
                "baseline_score": r.baseline_score,
                "score": r.score,
                "delta": r.delta,
            }
            for r in compared
        ],
    }
