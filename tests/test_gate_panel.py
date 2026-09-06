"""Settled outcomes are not unfinished work, and neither is a pass.

Three statuses that used to collapse into two:

  pending        the run has not answered yet, and will
  not_assessed   nothing could establish it, and nothing ever will
  not_required   it did not need to run

A licence chain that could not be resolved was shown as `pending`, which told
a seller to wait for work that had already finished and dragged the whole
panel to pending behind it. A gate the probe ruled out was shown as `pass`
with the evidence line "Held-out result", which claims a measurement that
never happened.
"""

from __future__ import annotations

import keystone.schema as S
from keystone.conditioning import NOT_REQUIRED
from keystone.safety_gates import summarize


def report(*, chain_ok: bool | None, suites: list[S.SuiteResult]) -> S.CertificationReport:
    return S.CertificationReport.model_construct(
        subject=S.Subject.model_construct(license=S.LicenseInfo(chain_ok=chain_ok)),
        scans=[S.ScanResult(scanner="picklescan", scanner_version="1", status=S.Status.PASS)],
        suite_results=suites,
    )


def gate(suite_id: str, **kw) -> S.SuiteResult:
    kw.setdefault("status", S.Status.PASS)
    return S.SuiteResult(
        suite_id=suite_id, suite_version="1", display_name=suite_id,
        gate=True, **kw,
    )


def panel(doc: dict) -> dict[str, dict]:
    return {g["gate_id"]: g for g in doc["gates"]}


ROUTED = [
    gate("bio_elicitation", held_out=True, conditioned_by="bio_probe",
         conditioned_verdict=NOT_REQUIRED, n_items=0),
    gate("harmbench", conditioned_verdict=NOT_REQUIRED, n_items=0),
]


def test_an_unresolvable_licence_chain_is_settled_not_pending() -> None:
    doc = summarize(report(chain_ok=None, suites=ROUTED))
    licence = panel(doc)["license_chain"]
    assert licence["status"] == "not_assessed"
    assert licence["blocking"] is False


def test_a_settled_panel_does_not_read_as_still_running() -> None:
    """Everything decided, nothing outstanding. Reporting `pending` here left
    the header spinning after the run had finished."""
    assert summarize(report(chain_ok=None, suites=ROUTED))["overall"] == "pass"


def test_a_routed_gate_does_not_claim_a_result() -> None:
    rows = panel(summarize(report(chain_ok=True, suites=ROUTED)))
    for gate_id in ("bio_elicitation", "harmbench"):
        assert rows[gate_id]["status"] == "not_required"
        assert "Not required" in rows[gate_id]["evidence"]
        assert "Held-out result" not in rows[gate_id]["evidence"]
        assert rows[gate_id]["blocking"] is False


def test_the_two_kinds_of_not_required_explain_themselves_differently() -> None:
    """A domain gate was ruled out by its own probe; a general screen was ruled
    out by the assessment floor. A seller reading either should learn which."""
    rows = panel(summarize(report(chain_ok=True, suites=ROUTED)))
    assert "this domain" in rows["bio_elicitation"]["evidence"]
    assert "assessment floor" in rows["harmbench"]["evidence"]


def test_a_real_failure_still_dominates_the_panel() -> None:
    failing = gate("harmbench", status=S.Status.FAIL, score=0.15, n_items=74)
    doc = summarize(report(chain_ok=None, suites=[*ROUTED[:1], failing]))
    assert doc["overall"] == "fail"
    assert panel(doc)["harmbench"]["blocking"] is True


def test_a_genuinely_unfinished_gate_still_reads_as_pending() -> None:
    """The distinction only works if `pending` still means what it says."""
    running = gate("harmbench", status=S.Status.SKIPPED)
    doc = summarize(report(chain_ok=True, suites=[running]))
    assert panel(doc)["harmbench"]["status"] == "pending"
    assert doc["overall"] == "pending"


def test_a_broken_licence_chain_still_fails() -> None:
    doc = summarize(report(chain_ok=False, suites=ROUTED))
    assert panel(doc)["license_chain"]["status"] == "fail"
    assert doc["overall"] == "fail"
