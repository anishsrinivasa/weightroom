"""The published benchmark release must describe itself accurately.

A release nobody can check is a claim rather than an artifact, and the numbers
in it are the ones a reader will quote.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from keystone.registry import SUITES_ROOT
from keystone.release import digest, export, install, verify

REPO = Path(__file__).resolve().parents[1]
RELEASE = REPO / "benchmarks"

pytestmark = pytest.mark.skipif(
    not (RELEASE / "manifest.json").exists(),
    reason="no benchmark release exported in this checkout",
)


def _manifest() -> dict:
    return json.loads((RELEASE / "manifest.json").read_text(encoding="utf-8"))


def test_every_published_file_matches_its_digest() -> None:
    """The whole point of shipping hashes: a silent edit has to be loud."""
    assert verify(RELEASE) == []


def test_the_release_covers_all_three_pairs() -> None:
    entries = {(e["domain"], e["role"]) for e in _manifest()["datasets"]}
    assert entries == {
        (domain, role)
        for domain in ("bio", "coding", "legal")
        for role in ("probe", "elicitation")
    }


def test_elicitation_sets_report_behaviours_not_just_items() -> None:
    """Item count is not sample size, and a release that reported only the
    larger number would overstate its own precision by an order of magnitude."""
    for entry in _manifest()["datasets"]:
        if entry["role"] != "elicitation":
            continue
        assert entry["seed_behaviours"] < entry["items"]
        assert entry["framings"] > 1


def test_probes_declare_the_chance_floor_their_arity_implies() -> None:
    """A four-way probe scored against a binary floor lands the model in the
    wrong capability band, and therefore under the wrong safety bar."""
    for entry in _manifest()["datasets"]:
        if entry["role"] != "probe":
            continue
        arities = entry["options_per_item"]
        assert len(arities) == 1, f"{entry['path']} mixes arities"
        assert entry["chance_floor"] == pytest.approx(1 / arities[0])


def test_every_dataset_names_what_it_was_derived_from() -> None:
    """The items are ours and the seeds are not. A release that does not say so
    is making a provenance claim it cannot support."""
    for entry in _manifest()["datasets"]:
        assert entry["derived_from"], entry["path"]


def _staged_tree(tmp_path: Path) -> Path:
    """A suites tree with items in it, built without touching the checkout.

    `suites/*/assets` is gitignored, so a clean clone has no staged items and
    an export test that used the real tree would fail there -- which is the
    exact failure the container build hits. Installing the published release
    into a copy makes these tests self-sufficient.
    """
    import shutil

    root = tmp_path / "suites"
    shutil.copytree(SUITES_ROOT, root)
    install(RELEASE, root)
    return root


def test_export_is_reproducible(tmp_path: Path) -> None:
    """Selection is by content hash rather than RNG, so a re-export of an
    unchanged staging directory has to be byte-identical -- otherwise the
    published digests mean nothing."""
    staged = _staged_tree(tmp_path)
    first = export(tmp_path / "a", suites_root=staged)
    second = export(tmp_path / "b", suites_root=staged)
    assert [e["sha256"] for e in first["datasets"]] == [
        e["sha256"] for e in second["datasets"]
    ]


def test_verify_catches_a_tampered_file(tmp_path: Path) -> None:
    out = tmp_path / "release"
    export(out, suites_root=_staged_tree(tmp_path))
    target = out / "bio" / "probe.json"
    rows = json.loads(target.read_text(encoding="utf-8"))
    rows[0]["answer"] = (rows[0]["answer"] + 1) % len(rows[0]["choices"])
    target.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

    broken = verify(out)
    assert broken and "bio/probe.json" in broken[0]


def test_digest_is_content_addressed() -> None:
    assert digest(b"a") != digest(b"b")
    assert digest(b"a") == digest(b"a")
