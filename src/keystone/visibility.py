"""Report redaction: one report, three audiences.

Certification gates listing, so a rejected creator resubmits. Every bit of
detail returned is a bit of the held-out eval set leaked, and enough bits make
our private measurement a public one -- at which point the rating is worthless
and the moat is gone.

The rule this module enforces:

  buyer     benchmark results, provenance, methodology. Coarse bands, no metrics.
  creator   the above, plus which *dimension* failed and where to practice.
            Never which item, never an exact score.
  internal  everything.

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


def _redact_suite(result: SuiteResult, audience: Audience) -> SuiteResult:
    if audience is Audience.INTERNAL:
        return result.model_copy(deep=True)

    out = result.model_copy(deep=True)
    out.score_band = band(out.score)

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
    out.suite_results = [_redact_suite(s, audience) for s in out.suite_results]
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
