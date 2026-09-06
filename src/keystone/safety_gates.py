"""Buyer-safe summaries of the checks that can block certification.

This module consumes an already-redacted report. It never reaches back to the
raw report, which keeps the UI convenience layer on the safe side of the
visibility boundary.
"""

from __future__ import annotations

from keystone.conditioning import NOT_REQUIRED
from keystone.public_safety import BY_ID as ACTIVE_PUBLIC_GATES
from keystone.schema import CertificationReport, Status


def _rollup(statuses: list[Status], *, empty: str = "pending") -> str:
    if not statuses:
        return empty
    if Status.FAIL in statuses:
        return "fail"
    if Status.ERROR in statuses or Status.SKIPPED in statuses:
        return "pending"
    if Status.WARN in statuses:
        return "warn"
    return "pass"


def summarize(report: CertificationReport) -> dict:
    """Return the gate panel shown by the API and frontend.

    Exact held-out scores and item-level evidence are absent because callers
    must pass the redacted report returned by ``visibility.redact``.
    """
    scan_status = _rollup([scan.status for scan in report.scans])
    gates = [
        {
            "gate_id": "artifact_integrity",
            "display_name": "Artifact integrity",
            "status": scan_status,
            "blocking": scan_status in {"fail", "pending"},
            "evidence": (
                f"{len(report.scans)} scanner(s) completed."
                if report.scans
                else "No scanner result is attached to this report."
            ),
        }
    ]

    chain_ok = report.subject.license.chain_ok
    # `None` means the chain could not be assessed -- no declared licence, or a
    # parent nobody can resolve. That is a settled answer, not an unfinished
    # one, and it never becomes anything else. Calling it "pending" told a
    # seller to wait for work that had already finished, and dragged the whole
    # panel to pending behind it.
    licence_status = (
        "pass" if chain_ok is True else "fail" if chain_ok is False else "not_assessed"
    )
    gates.append(
        {
            "gate_id": "license_chain",
            "display_name": "Licence chain",
            "status": licence_status,
            "blocking": licence_status == "fail",
            "evidence": (
                "Declared terms are compatible with the verified derivation chain."
                if chain_ok is True
                else "Declared terms conflict with the derivation chain."
                if chain_ok is False
                else "The licence chain was not conclusively assessed."
            ),
        }
    )

    behavioral = [
        result
        for result in report.suite_results
        if result.gate and (result.held_out or result.suite_id in ACTIVE_PUBLIC_GATES)
    ]
    if not behavioral:
        gates.append(
            {
                "gate_id": "behavioral_safety",
                "display_name": "Harmful-output resistance",
                "status": "pending",
                "blocking": True,
                "evidence": "No behavioral safety gate is attached to this report.",
            }
        )
    else:
        for result in behavioral:
            not_required = result.conditioned_verdict == NOT_REQUIRED
            if result.status in {Status.ERROR, Status.SKIPPED}:
                status = "pending"
            elif not_required:
                # PASS is how the pipeline records "did not need to run". Shown
                # as a pass it reads as a cleared bar, which is the one claim
                # this verdict does not make.
                status = "not_required"
            else:
                status = result.status.value
            automatically_passed = result.metrics.get("evaluation_skipped") == 1.0
            gates.append(
                {
                    "gate_id": result.suite_id,
                    "display_name": result.display_name or result.suite_id,
                    "status": status,
                    "blocking": status in {"fail", "pending"},
                    "held_out": result.held_out,
                    "score_band": result.score_band,
                    **(
                        {"score": result.score, "n_items": result.n_items}
                        if not result.held_out
                        else {}
                    ),
                    "categories": result.categories,
                    "remediation": result.remediation,
                    "evidence": (
                        "Automatically passed because safety evaluation is disabled; no prompts were run."
                        if automatically_passed
                        # Ordered before the held-out and public branches on
                        # purpose: both of those describe a measurement, and
                        # this gate does not have one. "Held-out result" on a
                        # suite that never ran is the overclaim the verdict
                        # exists to prevent.
                        else (
                            "Not required: the capability probe found too little "
                            "capability for this domain to produce meaningful "
                            "uplift, so no prompts were run."
                            if result.conditioned_by
                            else "Not required: no capability probe cleared the "
                            "assessment floor, so no prompts were run."
                        )
                        if not_required
                        else "Held-out result; exact prompts and scores are redacted."
                        if result.held_out
                        else (
                            f"Public evaluation result from {result.n_items} items; "
                            "higher is safer."
                            if result.n_items is not None
                            else "Public evaluation result; higher is safer."
                        )
                    ),
                }
            )

    # `not_required` and `not_assessed` are settled outcomes and are excluded
    # here: a panel of them plus one pass is finished, not pending. If every
    # gate came back that way the panel says so rather than claiming a pass
    # nothing measured.
    statuses = [gate["status"] for gate in gates]
    decided = [s for s in statuses if s not in {"not_required", "not_assessed"}]
    if not decided:
        overall = "not_required"
    else:
        overall = (
            "fail"
            if "fail" in decided
            else "pending"
            if "pending" in decided
            else "warn"
            if "warn" in decided
            else "pass"
        )
    return {"overall": overall, "gates": gates}
