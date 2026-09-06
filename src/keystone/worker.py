"""The worker that actually certifies.

Split from the API on purpose: certification takes minutes on a GPU and no HTTP
request thread should ever wait on that. The API queues by moving a listing to
`pending_certification`; this picks those up, runs the pipeline, records the
attempt, stores the report, and lands the listing on certified or rejected.

Failure is a normal outcome, not a crash. A model that will not serve gets a
rejected attempt and a reason, and the worker moves on to the next one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from keystone.db import Store
from keystone.listing import Attempt, AttemptPolicy, DEFAULT_POLICY, Listing, ListingState
from keystone.pipeline import FailureKind, Outcome
from keystone.schema import CertificationReport, Status, SuiteResult
from keystone.tags import model_size_tag

if TYPE_CHECKING:
    from keystone.storage import ArtifactStore


def _mean_held_out(report: CertificationReport) -> float | None:
    """Internal signal used to spot probing. Never leaves the platform."""
    scores = [r.score for r in report.suite_results if r.held_out and r.score is not None]
    if not scores:
        scores = [r.score for r in report.suite_results if r.score is not None]
    return sum(scores) / len(scores) if scores else None


def record_outcome(
    store: Store,
    listing_id: str,
    outcome: Outcome,
    *,
    charge_id: str | None = None,
    policy: AttemptPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
    signer=None,
) -> ListingState:
    """Persist one certification result and advance the listing.

    Passing certifies; anything else rejects. A rejection returns the creator to
    DRAFT, because fixing a model changes its bytes and therefore its digest --
    the next attempt is a genuinely new subject.
    """
    now = now or datetime.now(timezone.utc)

    with store.session() as s:
        listing: Listing | None = store.load_listing(s, listing_id)
        if listing is None:
            raise KeyError(listing_id)

        report = outcome.report
        # Deliberately belt-and-braces. `rating.certified` is the verdict, but
        # this is the last checkpoint before a model becomes publicly listed,
        # so the gate results are re-derived rather than taken on trust. A bug
        # in grading, or a report constructed by hand, must not be able to put
        # an unsafe model in the catalogue.
        gates = [r for r in report.suite_results if r.gate] if report else []
        passed = bool(
            report is not None
            and report.rating.certified
            and gates
            and all(r.status is Status.PASS for r in gates)
            and not any(r.status is Status.ERROR for r in report.suite_results)
        )

        listing.state = ListingState.CERTIFYING
        listing.record(
            Attempt(
                attempt_id=f"att_{uuid.uuid4().hex[:16]}",
                artifact_digest=listing.artifact_digest,
                created_at=now,
                grade=report.rating.grade if report else None,
                passed=passed,
                charge_id=charge_id,
                internal_score=_mean_held_out(report) if report else None,
            ),
            policy,
        )
        store.save_listing(s, listing)

        if report is not None:
            row = store.get_listing(s, listing_id)
            if row is not None:
                row.size_tag = model_size_tag(report.serving_profile.parameter_count)
            # Sign before storing, so the stored bytes are the signed bytes and
            # nothing can diverge between what we keep and what we attest to.
            if signer is not None:
                report = signer.sign_report(report)
            store.put_report(
                s, report, listing_id=listing_id, attempt_id=listing.attempts[-1].attempt_id
            )
        s.commit()
        return listing.state


def _require_selected_benchmark_results(
    outcome: Outcome, selected: list[str]
) -> Outcome:
    """Fail closed if the harness omitted a benchmark the seller paid for.

    The public benchmark catalogue and the executable suite registry are
    intentionally separate.  Until an official adapter exists for every
    catalogue entry, that separation must never turn a missing run into a
    successful certificate.
    """
    if outcome.report is None or not selected:
        return outcome

    report = outcome.report.model_copy(deep=True)
    by_id = {result.suite_id: result for result in report.suite_results}
    missing = [benchmark_id for benchmark_id in selected if benchmark_id not in by_id]
    incomplete = [
        benchmark_id
        for benchmark_id in selected
        if benchmark_id in by_id
        and (
            by_id[benchmark_id].declined
            or by_id[benchmark_id].status in {Status.ERROR, Status.SKIPPED}
            or by_id[benchmark_id].score is None
        )
    ]
    if not missing and not incomplete:
        return outcome

    for benchmark_id in missing:
        report.suite_results.append(
            SuiteResult(
                suite_id=benchmark_id,
                suite_version="unavailable",
                status=Status.ERROR,
                display_name=benchmark_id,
                diagnostic=True,
            )
        )

    invalid = sorted(set(missing + incomplete))
    detail = "selected benchmark did not complete: " + ", ".join(invalid)
    report.status = Status.ERROR
    report.rating.certified = False
    report.rating.rationale = detail
    return Outcome(
        outcome.ref,
        report=report,
        failure=FailureKind.SUITE_ERROR,
        detail=detail,
        wall_s=outcome.wall_s,
    )


def process_pending(
    store: Store,
    *,
    artifacts: ArtifactStore | None = None,
    listing_id: str | None = None,
    limit: int = 10,
    policy: AttemptPolicy = DEFAULT_POLICY,
    certify=None,
    signer=None,
    on_step=lambda msg: None,
) -> list[tuple[str, ListingState]]:
    """Certify every queued listing. Returns (listing_id, resulting state).

    `certify` is injectable so this is testable without Modal or a GPU.
    """
    from keystone.pipeline import certify_uploaded

    if certify is None and artifacts is None:
        raise ValueError("an artifact store is required for uploaded-model certification")

    with store.session() as s:
        pending = store.listings_in_state(s, ListingState.PENDING_CERTIFICATION)
        if listing_id is not None:
            pending = [row for row in pending if row.id == listing_id]
        queued = []
        for r in pending[:limit]:
            # A listing can name an artifact we never received, because nothing
            # forces the two to be created together. Reading `.files()` off the
            # missing row here would raise *outside* the per-listing guard
            # below, killing the whole pass -- and since the listing stays
            # pending, every later pass would die on it too. One bad row must
            # not be able to stall the queue permanently.
            artifact = store.get_artifact(s, r.artifact_digest)
            queued.append((
                r.id,
                r.artifact_digest,
                list(r.selected_benchmarks or []),
                artifact.files() if artifact is not None else None,
            ))

    results: list[tuple[str, ListingState]] = []
    for listing_id, digest, selected, files in queued:
        on_step(f"certifying {listing_id} ({digest[:12]})")
        with store.session() as s:
            if not store.claim_for_certification(s, listing_id):
                continue  # another worker got there first, or the state moved

        from keystone.public_safety import initial_progress_gates

        live_gates = initial_progress_gates()

        def persist_progress(event: dict) -> None:
            nonlocal live_gates
            if event.get("gates"):
                live_gates = event["gates"]
            with store.session() as progress_session:
                store.set_evaluation_progress(
                    progress_session,
                    listing_id,
                    percent=int(event.get("percent", 0)),
                    stage=str(event.get("stage", "Evaluating")),
                    gates=live_gates,
                )
                progress_session.commit()

        persist_progress({"percent": 0, "stage": "Preparing evaluation"})

        # Pass the selection through as `only`; anything not chosen comes back
        # marked declined rather than simply missing.
        try:
            if certify is None:
                if files is None:
                    raise FileNotFoundError(
                        f"no stored artifact for digest {digest[:12]}"
                    )
                outcome = certify_uploaded(
                    digest,
                    files,
                    artifacts,
                    only=selected,
                    on_step=on_step,
                    on_progress=persist_progress,
                )
            else:
                outcome = certify(digest, only=selected or None)
        except FileNotFoundError as exc:
            outcome = Outcome(
                digest,
                failure=FailureKind.MISSING_ARTIFACT,
                detail=str(exc)[:400],
            )
        except Exception as exc:
            # A malformed artifact or an unexpected integration failure must
            # not kill the worker and leave every later job queued forever.
            outcome = Outcome(
                digest,
                failure=FailureKind.UNKNOWN,
                detail=repr(exc)[:400],
            )
        outcome = _require_selected_benchmark_results(outcome, selected)
        state = record_outcome(store, listing_id, outcome, policy=policy, signer=signer)
        final_gates = live_gates
        terminal_stage = "Evaluation complete"
        if outcome.report is not None:
            final_gates = [
                {
                    "gate_id": result.suite_id,
                    "display_name": result.display_name or result.suite_id,
                    "status": result.status.value,
                    "completed": result.n_items or 0,
                    "total": result.n_items or 0,
                    "score": result.score,
                }
                for result in outcome.report.suite_results
                if result.gate
            ]
        elif outcome.failure is not None:
            # A pipeline/infrastructure failure is not evidence that a model
            # failed a safety gate. Mark the unfinished gates as errors and
            # preserve an accurate terminal state for the seller UI.
            final_gates = [
                {**gate, "status": "error"}
                for gate in live_gates
            ]
            terminal_stage = "Evaluation failed"
        persist_progress(
            {
                "percent": 100,
                "stage": terminal_stage,
                "gates": final_gates,
            }
        )
        # Say why. A bare "rejected" sends whoever is watching to a debugger,
        # and the reason is already in hand.
        if outcome.failure is not None:
            on_step(
                f"  -> {state.value}: {outcome.failure.value}"
                f" — {(outcome.detail or 'no detail')[:400]}"
            )
        else:
            grade = outcome.report.rating.grade if outcome.report else "?"
            on_step(f"  -> {state.value} (grade {grade})")
        results.append((listing_id, state))
    return results


def publish_certified(store: Store, listing_id: str) -> ListingState:
    """Move a certified listing live. Separate step so a human can gate it.

    Raises TransitionError if the listing was never certified -- the state
    machine is the guard, so nothing reaches the catalogue by another path.
    """
    from keystone.listing import transition

    with store.session() as s:
        listing = store.load_listing(s, listing_id)
        if listing is None:
            raise KeyError(listing_id)
        listing.state = transition(listing.state, ListingState.LISTED)
        store.save_listing(s, listing)
        s.commit()
        return listing.state
