"""The coding pair, and the SWE-bench questions it is built from.

No real elicitation prompt appears here. Fixtures are benign placeholders.
"""

from __future__ import annotations

import collections
import difflib

from keystone.mcq import FOUR_WAY, chance_floor, well_formed
from keystone.registry import SUITES_ROOT, discover
from keystone.staging import _PATCH_CHARS, swebench_questions

PROBE_ID = "coding_probe"
ELICIT_ID = "coding_elicitation"


def mutable_instances(n: int) -> list[dict]:
    """Patches with something to get wrong: a comparison, a name, an index."""
    return [
        {
            "instance_id": f"inst-{i}",
            "repo": "org/repo",
            "problem_statement": f"Issue number {i} does the wrong thing.",
            "patch": (
                f"diff --git a/f{i}.py b/f{i}.py" + chr(10)
                + "@@ -1,3 +1,5 @@" + chr(10)
                + "         if width == limit:" + chr(10)
                + "             buffer[offset] = value" + chr(10)
                + "             return True"
            ).replace(chr(10) + "  ", chr(10) + "+ "),
            "difficulty": ["<15 min fix", "15 min - 1 hour", "1-4 hours"][i % 3],
        }
        for i in range(n)
    ]


def instances(n: int, *, repos: int = 2) -> list[dict]:
    return [
        {
            "instance_id": f"inst-{i}",
            "repo": f"org/repo{i % repos}",
            "problem_statement": f"Issue number {i} does the wrong thing.",
            "patch": f"diff --git a/f{i}.py b/f{i}.py\n+ fix {i}",
            "difficulty": ["<15 min fix", "15 min - 1 hour", "1-4 hours"][i % 3],
        }
        for i in range(n)
    ]


# --------------------------------------------------------------------------
# question construction
# --------------------------------------------------------------------------

def test_the_gold_patch_is_the_answer() -> None:
    """Correctness is inherited from SWE-bench, not asserted by anything here.

    That is what makes a generated item safe to set a safety threshold from: a
    wrong answer key would move somebody's bar and nothing downstream would
    notice.
    """
    rows = mutable_instances(12)
    by_id = {r["instance_id"]: r for r in rows}

    for item in swebench_questions(rows):
        gold = by_id[item["instance_id"]]["patch"]
        assert item["choices"][item["answer"]].startswith(gold[:_PATCH_CHARS][:40])
        assert well_formed(item)


def test_every_option_is_the_same_patch_with_one_thing_wrong() -> None:
    """The fix for what the pilot exposed.

    Distractors were drawn from *other issues* in the same repository, so a
    wrong option addressed a different problem and could be eliminated by
    noticing which files and symbols the issue mentioned. Qwen2.5-7B scored 92%
    on that -- topic matching, not patch comprehension -- and the inflated
    capability estimate set the strictest safety bar of the three domains.

    Now every option patches the same file for the same issue and three have
    one decision flipped, so the shortcut pays nothing.
    """
    rows = mutable_instances(8)
    for item in swebench_questions(rows):
        gold = item["choices"][item["answer"]]
        for choice in item["choices"]:
            if choice == gold:
                continue
            differing = [
                line
                for line in difflib.unified_diff(
                    gold.splitlines(), choice.splitlines(), lineterm="", n=0
                )
                if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
            ]
            # one line replaced: one removed, one added
            assert len(differing) <= 4


def test_a_patch_with_nothing_to_get_wrong_is_dropped() -> None:
    """Three distinct mutations or nothing. Falling back to another issue's
    patch would reintroduce the shortcut exactly where it bites hardest."""
    barren = [{
        "instance_id": "inst-0", "repo": "org/repo",
        "problem_statement": "Something is broken.",
        "patch": "diff --git a/f.py b/f.py" + chr(10) + "+x",
        "difficulty": "<15 min fix",
    }]
    assert swebench_questions(barren) == []


def test_wrong_options_are_not_systematically_shorter() -> None:
    """A length difference would let a model pick without reading. Deleting a
    line was the obvious mutation and this is why it is not used."""
    items = swebench_questions(mutable_instances(12))
    assert items
    for item in items:
        lengths = [len(c) for c in item["choices"]]
        assert max(lengths) - min(lengths) <= 8


def test_answer_positions_are_spread(rounds: int = 200) -> None:
    """A constant answer position would be learnable without reading anything."""
    items = swebench_questions(mutable_instances(rounds))
    spread = collections.Counter(i["answer"] for i in items)
    assert set(spread) == set(range(FOUR_WAY))
    assert min(spread.values()) > len(items) / 10


def test_questions_are_deterministic() -> None:
    rows = mutable_instances(10)
    assert swebench_questions(rows) == swebench_questions(rows)


def test_long_patches_are_truncated_before_mutation_not_after() -> None:
    """Order matters. Mutating first and clipping after can put the flipped
    decision past the cut, leaving four options that read identically -- an
    unanswerable item that still counts toward the score.
    """
    rows = mutable_instances(6)
    tail = (chr(10) + "+    filler = 1") * 400
    rows[0]["patch"] = rows[0]["patch"] + tail

    item = next(i for i in swebench_questions(rows) if i["instance_id"] == "inst-0")
    assert all(len(c) <= _PATCH_CHARS + 40 for c in item["choices"])
    assert len(set(item["choices"])) == 4  # the mutation is inside the visible part
