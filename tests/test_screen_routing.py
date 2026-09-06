"""The probes route the absolute screens, not just the domain pairs.

`resolve` already declined to gate on them for a model below the assessment
floor, but only at report assembly -- after 348 model calls and 348 judge calls
had been paid for on a model the probes had just shown cannot act on any of it.
Routing is the point of a cheap probe; a footnote on completed work is not.
"""

from __future__ import annotations

from keystone.conditioning import NOT_REQUIRED, resolve
from keystone.pipeline import grade
from keystone.public_safety import SCREENS, not_required_results
from keystone.schema import Status, SuiteResult

SCREEN_IDS = tuple(s.id for s in SCREENS)


def probe(domain: str, score: float) -> SuiteResult:
    chance = 0.50 if domain == "legal" else 0.25
    return SuiteResult(
        suite_id=f"{domain}_probe", suite_version="1", status=Status.PASS,
        internal=True, role="probe", domain=domain, score=score,
        n_items=100, chance_floor=chance,
    )


NEAR_CHANCE = [probe("bio", 0.28), probe("coding", 0.26), probe("legal", 0.52)]


def test_skipped_screens_are_recorded_not_omitted() -> None:
    """A gate missing from a report reads as a gate that was forgotten, and
    `grade` refuses to certify when no safety gate ran at all."""
    rows = not_required_results(SCREEN_IDS)
    assert {r.suite_id for r in rows} == set(SCREEN_IDS)
    for r in rows:
        assert r.gate is True
        assert r.conditioned_verdict == NOT_REQUIRED
        assert r.n_items == 0
        assert r.score is None  # nothing was measured, so nothing is claimed


def test_a_routed_run_still_certifies_and_says_what_was_skipped() -> None:
    results = resolve([*NEAR_CHANCE, *not_required_results(SCREEN_IDS)])
    _, certified, rationale = grade([], results, license_chain_ok=True)
    assert certified
    assert "assessment floor" in rationale
    assert "not that the model was found safe" in rationale


def test_routing_does_not_fabricate_a_score() -> None:
    """`automatic_pass_results` claims 1.0 because the bypass switch asserts a
    pass. Routing asserts the opposite -- that nothing was measured -- so a
    score here would be a number nobody produced."""
    for r in not_required_results(SCREEN_IDS):
        assert r.score is None
        assert r.metrics == {}


def test_only_the_named_screens_are_produced() -> None:
    rows = not_required_results(("harmbench",))
    assert [r.suite_id for r in rows] == ["harmbench"]


def test_a_capable_model_is_not_routed_around_the_screens() -> None:
    """The floor is not a way out of the absolute gate."""
    from keystone.conditioning import below_assessment_floor

    capable = [probe("bio", 0.30), probe("coding", 0.71), probe("legal", 0.55)]
    assert not below_assessment_floor(capable)
