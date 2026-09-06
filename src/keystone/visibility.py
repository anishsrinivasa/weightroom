"""Report redaction: one report, three audiences.

Certification gates listing, so a rejected creator resubmits. Every bit of
detail returned is a bit of the held-out eval set leaked, and enough bits make
our private measurement a public one -- at which point the rating is worthless
and the moat is gone.

The rule this module enforces:

  buyer     grade, provenance, methodology. Coarse bands, no metrics.
  creator   the above, plus which *dimension* failed and where to practice.
            Never which item, never an exact score.
  internal  everything.

Capability-conditioned suites are stricter still, because under conditioning
the *threshold itself* is information: a creator who learns the rate they had
to clear has learned their capability band, and one who also learns the rate
they achieved can binary-search the band edges across resubmissions. So a
probe result never leaves the platform in any form, and a conditioned gate
leaves as a name and a verdict with no numbers attached -- not even the coarse
band an ordinary held-out suite is allowed.

Creators are meant to iterate -- against the PUBLIC practice suites, whose
results are safe to hand back in full because they are already published. Only
held-out suites get strict treatment. That split is what turns an information
leak into a product feature.
"""

from __future__ import annotations

from keystone.schema import (
    Audience,
    CertificationReport,
    ScanResult,
    SuiteResult,
)

# Coarse bands. Deliberately few: a creator who resubmits repeatedly should not
# be able to binary-search their way to an exact pass threshold.
_BANDS: list[tuple[float, str]] = [
    (0.90, "high"),
    (0.75, "good"),
    (0.60, "moderate"),
    (0.40, "low"),
]


def band(score: float | None) -> str | None:
    if score is None:
        return None
    return next((label for cutoff, label in _BANDS if score >= cutoff), "poor")


def _redact_internal(result: SuiteResult, audience: Audience) -> SuiteResult | None:
    """A conditioning instrument, on its way out of the platform.

    `internal` means the numbers never escape. Whether the *row* escapes is a
    separate question, answered by role:

    A probe row is nothing but the number a creator must not see. Its verdict
    is not about them -- it measures what they can do, not whether they passed
    -- so there is nothing to keep once the score is gone. It is dropped.

    An elicitation row does carry a verdict they need. A rejection has to name
    the domain or it is unactionable, and an unactionable publish-gate turns
    every rejection into a support ticket. So it survives as a name, a domain,
    and pass/fail -- and for a buyer, not at all, since a listed model has
    passed every gate by definition and the row would only invite questions
    about numbers we will not give.
    """
    if result.role == "probe" or audience is Audience.BUYER:
        return None

    out = result.model_copy(deep=True)
    out.score = None
    out.score_band = None  # stricter than an ordinary held-out suite
    out.baseline_score = None
    out.delta = None
    out.metrics = {}
    out.findings = []
    out.n_items = None
    out.duration_s = None
    out.threshold_required = None
    out.threshold_basis = None
    # `status` collapses "not gated" and "cleared the bar" to PASS.
    # `conditioned_verdict` distinguishes them, which is a band-boundary
    # signal: two submissions either side of the negligible/low edge would
    # locate it. The creator needs pass or fail, and gets it from `status`.
    out.conditioned_verdict = None
    out.error = "error" if out.error else None
    return out


def _redact_suite(result: SuiteResult, audience: Audience) -> SuiteResult | None:
    if audience is Audience.INTERNAL:
        return result.model_copy(deep=True)

    if result.internal:
        return _redact_internal(result, audience)

    out = result.model_copy(deep=True)
    out.score_band = band(out.score)
    # The bar a suite was held to names the capability band that produced it,
    # and the verdict distinguishes "not gated" from "passed". Both are
    # internal-only regardless of which suite carries them.
    out.threshold_required = None
    out.threshold_basis = None
    out.conditioned_verdict = None

    if not out.held_out:
        # A public practice suite. Its contents are already published, so full
        # detail leaks nothing -- and detail is exactly what makes it useful to
        # iterate against.
        out.findings = [f for f in out.findings if audience.can_see(f.visibility)]
        if audience is Audience.BUYER:
            out.categories = []
            out.remediation = None
        return out

    # Held-out suite: strict. Nothing item-level survives.
    out.score = None
    # A delta plus a public baseline reveals the held-out score by subtraction,
    # so it has to go the same way the score does.
    out.baseline_score = None
    out.delta = None
    out.metrics = {}
    out.findings = []
    out.n_items = None
    out.duration_s = None
    out.error = "error" if out.error else None  # fact of failure, not its content

    if audience is Audience.BUYER:
        out.categories = []
        out.remediation = None

    return out


def _redact_scan(scan: ScanResult, audience: Audience) -> ScanResult:
    out = scan.model_copy(deep=True)
    if audience is Audience.BUYER:
        # Scan findings describe how an artifact is malformed or malicious.
        # A buyer needs the verdict, not the recipe.
        out.findings = []
    return out


def redact(report: CertificationReport, audience: Audience) -> CertificationReport:
    """Return a copy of `report` safe to show `audience`.

    Always call this at the boundary. Never serialise a raw report to anyone
    outside the platform.
    """
    if audience is Audience.INTERNAL:
        return report.model_copy(deep=True)

    out = report.model_copy(deep=True)
    out.cost = None  # our unit economics are nobody else's business
    out.scans = [_redact_scan(s, audience) for s in out.scans]
    out.suite_results = [
        redacted
        for redacted in (_redact_suite(s, audience) for s in out.suite_results)
        if redacted is not None
    ]
    return out


def assert_no_leak(report: CertificationReport, audience: Audience) -> None:
    """Belt-and-braces check for tests and for the API boundary.

    Raises if anything item-level from a held-out suite survived redaction.
    """
    if audience is Audience.INTERNAL:
        return
    if report.cost is not None:
        raise AssertionError("cost is INTERNAL only")
    for r in report.suite_results:
        if (
            r.threshold_required is not None
            or r.threshold_basis is not None
            or r.conditioned_verdict is not None
        ):
            raise AssertionError(
                f"suite {r.suite_id} leaked the threshold it was held to; a "
                "required rate names the capability band behind it, and the "
                "verdict separates 'not gated' from 'passed'"
            )
        if r.internal:
            if r.role == "probe":
                raise AssertionError(
                    f"internal probe {r.suite_id} escaped the platform; its "
                    "score is the number a creator must not see"
                )
            if audience is Audience.BUYER:
                raise AssertionError(
                    f"internal suite {r.suite_id} is not buyer-visible"
                )
            if r.score is not None or r.score_band is not None or r.metrics or r.findings:
                raise AssertionError(
                    f"internal suite {r.suite_id} leaked a score; conditioned "
                    "gates surface a verdict and nothing measurable"
                )
        if not r.held_out:
            continue
        if (
            r.score is not None
            or r.baseline_score is not None
            or r.delta is not None
            or r.metrics
            or r.findings
            or r.n_items is not None
        ):
            raise AssertionError(f"held-out suite {r.suite_id} leaked item-level detail")
    if audience is Audience.BUYER:
        if any(s.findings for s in report.scans):
            raise AssertionError("scan findings are not buyer-visible")
        if any(r.categories or r.remediation for r in report.suite_results):
            raise AssertionError("failure categories are not buyer-visible")
