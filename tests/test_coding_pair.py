"""The coding pair, and the SWE-bench questions it is built from.

No real elicitation prompt appears here. Fixtures are benign placeholders.
"""

from __future__ import annotations

import collections

from keystone.mcq import FOUR_WAY, chance_floor, well_formed
from keystone.registry import SUITES_ROOT, discover
from keystone.staging import _PATCH_CHARS, swebench_questions

PROBE_ID = "coding_probe"
ELICIT_ID = "coding_elicitation"


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
    rows = instances(12, repos=1)
    by_id = {r["instance_id"]: r for r in rows}

    for item in swebench_questions(rows):
        gold = by_id[item["instance_id"]]["patch"]
        assert item["choices"][item["answer"]].startswith(gold[:_PATCH_CHARS][:40])
        assert well_formed(item)


def test_distractors_come_from_the_same_repository() -> None:
    """A patch from an unrelated project is identifiable from its import paths
    alone, and an item answerable that way measures nothing."""
    rows = instances(20, repos=2)
    patches = {r["repo"]: {r2["patch"] for r2 in rows if r2["repo"] == r["repo"]}
               for r in rows}
    by_id = {r["instance_id"]: r for r in rows}

    for item in swebench_questions(rows):
        repo = by_id[item["instance_id"]]["repo"]
        for choice in item["choices"]:
            assert any(choice.startswith(p[:40]) for p in patches[repo])


def test_an_instance_without_enough_siblings_is_dropped_not_padded() -> None:
    """Three same-repo distractors or nothing. Padding from another project
    would make the item answerable without reading the diff."""
    lonely = instances(1)
    assert swebench_questions(lonely) == []

    # 500 upstream instances yield slightly fewer questions for this reason.
    mixed = instances(4, repos=2)  # two repos of two -> only one sibling each
    assert swebench_questions(mixed) == []


def test_answer_positions_are_spread(rounds: int = 200) -> None:
    """A constant answer position would be learnable without reading anything."""
    items = swebench_questions(instances(rounds, repos=1))
    spread = collections.Counter(i["answer"] for i in items)
    assert set(spread) == set(range(FOUR_WAY))
    assert min(spread.values()) > len(items) / 10


def test_questions_are_deterministic() -> None:
    rows = instances(10, repos=1)
    assert swebench_questions(rows) == swebench_questions(rows)


def test_long_patches_are_truncated_not_dropped() -> None:
    rows = instances(6, repos=1)
    rows[0]["patch"] = "diff --git a/x b/x\n" + ("+ line\n" * 5000)
    item = next(i for i in swebench_questions(rows) if i["instance_id"] == "inst-0")
    assert all(len(c) <= _PATCH_CHARS + 40 for c in item["choices"])
    assert "truncated" in item["choices"][item["answer"]]


# --------------------------------------------------------------------------
# the pair is wired
# --------------------------------------------------------------------------

def test_the_coding_pair_is_registered_and_conditioned() -> None:
    manifests = {s.manifest.id: s.manifest for s in discover(SUITES_ROOT)}

    probe = manifests[PROBE_ID]
    assert probe.domain == "coding" and probe.role == "probe"
    assert probe.internal and not probe.gate
    assert probe.chance_floor == chance_floor(FOUR_WAY)

    elicit = manifests[ELICIT_ID]
    assert elicit.domain == "coding" and elicit.role == "elicitation"
    assert elicit.conditioned_by == PROBE_ID
    assert elicit.internal and elicit.gate and elicit.held_out and elicit.judged


def test_neither_coding_suite_appears_on_the_creator_menu() -> None:
    from keystone.benchmarks import menu

    offered = {i.suite_id for i in menu(discover(SUITES_ROOT))}
    assert PROBE_ID not in offered and ELICIT_ID not in offered
