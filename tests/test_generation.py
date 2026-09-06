"""Item generation: transform, do not invent.

The properties here are the ones that make a generated set usable as the input
to a safety threshold. Two of them caught real bugs while being written, noted
at the tests concerned.

No real seed content appears in this file. The fixtures are benign placeholders
-- committing adversarial prompts would burn the set they came from, and the
arithmetic does not need the real thing.
"""

from __future__ import annotations

import pytest

from keystone.generation import (
    ELICITATION_TRANSFORMS,
    FRAMING_COUNT,
    FRAMING_FAMILIES,
    PROBE_TRANSFORMS,
    expand,
    fingerprint,
    overlap,
    rotate,
)


def mcq(n: int) -> list[dict]:
    return [
        {
            "question": f"Placeholder question {i}?",
            "choices": [f"opt{i}a", f"opt{i}b", f"opt{i}c", f"opt{i}d"],
            "answer": i % 4,
        }
        for i in range(n)
    ]


def behaviours(n: int) -> list[dict]:
    return [{"prompt": f"Benign placeholder behaviour {i}."} for i in range(n)]


def bare(items: list[dict]) -> list[dict]:
    return [{k: v for k, v in i.items() if k != "generated"} for i in items]


# --------------------------------------------------------------------------
# ground truth is inherited, never asserted
# --------------------------------------------------------------------------

def test_permutation_moves_the_options_and_follows_the_answer() -> None:
    """The property the whole transform exists for.

    A model that memorised "this question, option C" scores at chance once the
    options move; one that knows the material is unaffected. Correctness comes
    from construction rather than from a grader's opinion.
    """
    seeds = mcq(60)
    out = expand(seeds, PROBE_TRANSFORMS, variants=1)
    by_question = {s["question"]: s for s in seeds}

    for item in out:
        seed = by_question[item["question"]]
        assert sorted(item["choices"]) == sorted(seed["choices"])  # same options
        assert item["choices"][item["answer"]] == seed["choices"][seed["answer"]]

    # Most should actually have moved. With four options, hash ordering
    # reproduces the original about 1 time in 24, so a handful is expected --
    # but zero would mean the transform never ran.
    moved = sum(1 for i in out if i["choices"] != by_question[i["question"]]["choices"])
    assert moved > len(out) * 0.8


def test_probe_transforms_do_not_include_identity() -> None:
    """This caught a real bug.

    `expand` walks transforms per variant, so an `identity` transform listed
    first filled the whole target on variant 0 and permutation never ran --
    leaving the probe byte-identical to the published set it exists to defeat.
    """
    assert "identity" not in {t.id for t in PROBE_TRANSFORMS}


def test_framing_preserves_the_behaviour_it_wraps() -> None:
    """A framing changes the envelope, not the request. If it dropped the
    behaviour the item would measure nothing while still counting toward the
    score."""
    seeds = behaviours(6)
    out = expand(seeds, ELICITATION_TRANSFORMS, variants=FRAMING_COUNT)
    for item in out:
        assert any(seed["prompt"] in item["prompt"] for seed in seeds)
        assert item["framing"]
        assert item["seed_fingerprint"]


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------

def test_generation_is_reproducible() -> None:
    """Same inputs, same set. A probe that varied per run would move every
    threshold derived from it, silently."""
    seeds = mcq(30)
    assert bare(expand(seeds, PROBE_TRANSFORMS, 3)) == bare(expand(seeds, PROBE_TRANSFORMS, 3))
    assert rotate(seeds, 10, 4) == rotate(seeds, 10, 4)


def test_rotation_does_not_depend_on_input_order() -> None:
    """Selection is by content, so a reordered pool yields the same draw."""
    seeds = mcq(40)
    assert rotate(seeds, 12, 2) == rotate(list(reversed(seeds)), 12, 2)


# --------------------------------------------------------------------------
# freshness
# --------------------------------------------------------------------------

def test_rotations_draw_different_items_from_a_large_pool() -> None:
    pool = mcq(1000)
    a, b = rotate(pool, 100, 0), rotate(pool, 100, 1)
    assert overlap(a, b) < 25


def test_rotation_five_is_not_rotation_zero() -> None:
    """This caught a real bug.

    An earlier version walked the transform sequence directly from the rotation
    index, so with five framings rotation 5 came back byte-identical to
    rotation 0. Expansion now builds a pool and rotation draws from it, which
    decouples the two.
    """
    seeds = behaviours(28)
    pool = expand(seeds, ELICITATION_TRANSFORMS, variants=FRAMING_COUNT)
    assert overlap(rotate(pool, 100, 0), rotate(pool, 100, 5)) < 100


def test_the_library_is_large_enough_to_remove_forced_reuse() -> None:
    """Freshness is arithmetic, not shuffling.

    Drawing n items from a pool of p forces 2n - p to be shared between any two
    rotations. With five framings over 28 behaviours the pool was 140 and the
    floor was 60 of 100 -- the elicitation set could not be made fresh however
    it was selected. The library is what fixed that, not the selector.
    """
    from keystone.staging import overlap_floor

    seeds = behaviours(28)
    pool = expand(seeds, ELICITATION_TRANSFORMS, variants=FRAMING_COUNT)
    assert len(pool) == 28 * FRAMING_COUNT
    assert overlap_floor(len(pool), 100) == 0

    # And the realised overlap is near-free too, not merely permitted to be.
    assert overlap(rotate(pool, 100, 0), rotate(pool, 100, 3)) < 35

    # The relationship the floor expresses, stated directly.
    assert overlap_floor(140, 100) == 60
    assert overlap_floor(1273, 100) == 0


def test_framings_span_technique_families() -> None:
    """A set drawn from one family reports robustness it has not measured.

    Models are commonly hardened against direct requests and not against
    fictional framing, so coverage has to be across families rather than across
    wordings of the same trick.
    """
    assert len(FRAMING_FAMILIES) >= 6
    pool = expand(behaviours(4), ELICITATION_TRANSFORMS, variants=FRAMING_COUNT)
    assert {i["framing_family"] for i in pool} == set(FRAMING_FAMILIES)
    # Every framing carries its own id too, so a failure names the technique.
    assert len({i["framing"] for i in pool}) == FRAMING_COUNT


# --------------------------------------------------------------------------
# coverage and degenerate inputs
# --------------------------------------------------------------------------

def test_every_seed_is_covered_before_any_is_repeated() -> None:
    """Round-robin across seeds, not depth-first. A set that expanded the first
    few behaviours to fill its quota would measure less than its item count
    suggests."""
    seeds = behaviours(10)
    pool = expand(seeds, ELICITATION_TRANSFORMS, variants=FRAMING_COUNT)
    first_pass = pool[: len(seeds)]
    assert len({i["seed_fingerprint"] for i in first_pass}) == len(seeds)


def test_duplicates_are_dropped_not_counted() -> None:
    """A set padded with repeats claims resolution it does not have."""
    seeds = behaviours(5)
    pool = expand(seeds, ELICITATION_TRANSFORMS, variants=FRAMING_COUNT)
    assert len({fingerprint(i) for i in bare(pool)}) == len(pool)


@pytest.mark.parametrize(
    "seeds,transforms,variants",
    [([], PROBE_TRANSFORMS, 1), (mcq(2), (), 1), (mcq(2), PROBE_TRANSFORMS, 0)],
)
def test_degenerate_inputs_return_empty_rather_than_looping(
    seeds, transforms, variants
) -> None:
    assert expand(seeds, transforms, variants) == []


def test_rotate_returns_everything_when_the_pool_is_smaller_than_the_draw() -> None:
    seeds = behaviours(7)
    assert len(rotate(seeds, 100, 0)) == 7
