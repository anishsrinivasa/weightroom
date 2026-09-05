"""Listing lifecycle. Certification is the gate, not a product.

A creator uploads a model, works on the listing, then asks to publish. That
request triggers certification. Passing lists it; failing sends it back with
coarse feedback.

The interesting part is what happens on failure. A rejected creator resubmits,
and a determined one resubmits many times -- each attempt sampling our held-out
eval set. The policy in this module is the defence: attempts cost money, they
are rate-limited, they are capped, held-out items rotate, and a creator whose
submissions look like a search rather than a fix gets flagged for a human.

None of that stops legitimate iteration, because legitimate iteration happens
against the PUBLIC practice suites, which are free, local, and unlimited.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class ListingState(str, Enum):
    DRAFT = "draft"
    PENDING_CERTIFICATION = "pending_certification"
    CERTIFYING = "certifying"
    REJECTED = "rejected"
    CERTIFIED = "certified"
    LISTED = "listed"
    DELISTED = "delisted"
    WITHDRAWN = "withdrawn"


_TRANSITIONS: dict[ListingState, set[ListingState]] = {
    ListingState.DRAFT: {ListingState.PENDING_CERTIFICATION, ListingState.WITHDRAWN},
    ListingState.PENDING_CERTIFICATION: {ListingState.CERTIFYING, ListingState.WITHDRAWN},
    ListingState.CERTIFYING: {ListingState.CERTIFIED, ListingState.REJECTED},
    # A rejection returns to DRAFT: the creator changes the artifact, which
    # means a new digest, which means a genuinely new subject.
    ListingState.REJECTED: {ListingState.DRAFT, ListingState.WITHDRAWN},
    ListingState.CERTIFIED: {ListingState.LISTED, ListingState.WITHDRAWN},
    # Re-certification of a live listing can pull it down. This is the teeth
    # behind the gate: gaming it buys a temporary listing, not a permanent one.
    ListingState.LISTED: {ListingState.DELISTED, ListingState.PENDING_CERTIFICATION},
    ListingState.DELISTED: {ListingState.DRAFT, ListingState.WITHDRAWN},
    ListingState.WITHDRAWN: set(),
}


class TransitionError(RuntimeError):
    pass


def can_transition(src: ListingState, dst: ListingState) -> bool:
    return dst in _TRANSITIONS[src]


def transition(src: ListingState, dst: ListingState) -> ListingState:
    if not can_transition(src, dst):
        raise TransitionError(f"{src.value} -> {dst.value} is not a legal transition")
    return dst


# --------------------------------------------------------------------------
# Attempt policy
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AttemptPolicy:
    """Tunable, but every default here exists to slow down eval-set probing."""

    cooldown: timedelta = timedelta(hours=6)
    max_attempts_per_listing: int = 5
    rotate_held_out_after: int = 2
    fee_cents_per_attempt: int = 2500
    # Consecutive near-identical submissions before a human is asked to look.
    probing_similarity_threshold: int = 3


DEFAULT_POLICY = AttemptPolicy()


@dataclass
class Attempt:
    attempt_id: str
    artifact_digest: str
    created_at: datetime
    grade: str | None = None
    passed: bool = False
    # Mean held-out score, retained INTERNALLY only. Never surfaced -- it is
    # precisely the signal a prober wants.
    internal_score: float | None = None


@dataclass
class Listing:
    listing_id: str
    creator_id: str
    artifact_digest: str
    state: ListingState = ListingState.DRAFT
    attempts: list[Attempt] = field(default_factory=list)
    flagged_for_review: bool = False

    # -- gating ---------------------------------------------------------

    def can_attempt(
        self, now: datetime, policy: AttemptPolicy = DEFAULT_POLICY
    ) -> tuple[bool, str]:
        if self.state not in (ListingState.DRAFT, ListingState.REJECTED, ListingState.LISTED):
            return False, f"listing is {self.state.value}"

        if len(self.attempts) >= policy.max_attempts_per_listing:
            return False, (
                f"attempt limit reached ({policy.max_attempts_per_listing}); "
                "contact support to continue"
            )

        if self.attempts:
            last = self.attempts[-1]
            ready_at = last.created_at + policy.cooldown
            if now < ready_at:
                remaining = ready_at - now
                hours = remaining.total_seconds() / 3600
                return False, f"cooldown active, {hours:.1f}h remaining"

            if last.artifact_digest == self.artifact_digest:
                return False, "artifact unchanged since the last attempt"

        return True, ""

    def fee_cents(self, policy: AttemptPolicy = DEFAULT_POLICY) -> int:
        return policy.fee_cents_per_attempt

    def rotation_index(self, policy: AttemptPolicy = DEFAULT_POLICY) -> int:
        """Which held-out slice this attempt should draw.

        Rotating means passing last time does not mean facing the same items
        this time. The harness side owns what each slice contains.
        """
        return len(self.attempts) // max(policy.rotate_held_out_after, 1)

    # -- recording ------------------------------------------------------

    def record(
        self, attempt: Attempt, policy: AttemptPolicy = DEFAULT_POLICY
    ) -> ListingState:
        self.attempts.append(attempt)
        self.state = transition(
            ListingState.CERTIFYING,
            ListingState.CERTIFIED if attempt.passed else ListingState.REJECTED,
        )
        if detect_probing(self.attempts, policy):
            self.flagged_for_review = True
        return self.state


def detect_probing(attempts: list[Attempt], policy: AttemptPolicy = DEFAULT_POLICY) -> bool:
    """Flag submissions that look like a search rather than a fix.

    The signature is a run of attempts whose internal score creeps upward in
    small increments -- someone hill-climbing the threshold rather than fixing
    a real problem. Cheap heuristic, deliberately not a hard block: it routes
    to a human, who is much better at telling the two apart.
    """
    scored = [a.internal_score for a in attempts if a.internal_score is not None]
    if len(scored) < policy.probing_similarity_threshold:
        return False

    window = scored[-policy.probing_similarity_threshold :]
    deltas = [b - a for a, b in zip(window, window[1:])]
    return all(0 < d < 0.05 for d in deltas)
