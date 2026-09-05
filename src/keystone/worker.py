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

from keystone.db import Store
from keystone.listing import Attempt, AttemptPolicy, DEFAULT_POLICY, Listing, ListingState
from keystone.pipeline import Outcome
from keystone.schema import CertificationReport, Status


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
        passed = bool(
            report is not None
            and report.rating.grade not in ("F", "unrated")
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
            # Sign before storing, so the stored bytes are the signed bytes and
            # nothing can diverge between what we keep and what we attest to.
            if signer is not None:
                report = signer.sign_report(report)
            store.put_report(
                s, report, listing_id=listing_id, attempt_id=listing.attempts[-1].attempt_id
            )
        s.commit()
        return listing.state


def process_pending(
    store: Store,
    *,
    limit: int = 10,
    policy: AttemptPolicy = DEFAULT_POLICY,
    certify=None,
    signer=None,
    on_step=lambda msg: None,
) -> list[tuple[str, ListingState]]:
    """Certify every queued listing. Returns (listing_id, resulting state).

    `certify` is injectable so this is testable without Modal or a GPU.
    """
    from keystone.pipeline import certify_one

    certify = certify or certify_one

    with store.session() as s:
        queued = [
            (r.id, r.artifact_digest)
            for r in store.listings_in_state(s, ListingState.PENDING_CERTIFICATION)[:limit]
        ]

    results: list[tuple[str, ListingState]] = []
    for listing_id, digest in queued:
        on_step(f"certifying {listing_id} ({digest[:12]})")
        outcome = certify(digest)
        state = record_outcome(store, listing_id, outcome, policy=policy, signer=signer)
        on_step(f"  -> {state.value}")
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
