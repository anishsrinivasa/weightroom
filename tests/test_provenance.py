"""Lineage extraction and licence-chain tests.

The asymmetry under test: a pass requires every link to be known and
compatible, while a single unknown parent yields "cannot determine". "We could
not tell" and "it is fine" are different answers, and only one of them is safe
to print on a certificate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from keystone.provenance import (
    Family,
    build_license_info,
    declared_license,
    evaluate_chain,
    extract_lineage,
    lookup,
    parse_card_frontmatter,
)
from keystone.schema import ParentEdge


def card(text: str) -> str:
    return f"---\n{text}\n---\n\n# Model\n\nSome prose.\n"


# --------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------

def test_scalar_and_list_keys() -> None:
    meta = parse_card_frontmatter(
        card("license: apache-2.0\nbase_model:\n  - meta-llama/Llama-3.1-8B")
    )
    assert meta["license"] == "apache-2.0"
    assert meta["base_model"] == ["meta-llama/Llama-3.1-8B"]


def test_quotes_are_stripped() -> None:
    assert parse_card_frontmatter(card('license: "mit"'))["license"] == "mit"


def test_a_card_without_frontmatter_is_not_an_error() -> None:
    assert parse_card_frontmatter("# Just a heading\n") == {}


def test_malformed_frontmatter_degrades_quietly(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("---\n:::garbage:::\n---\n", encoding="utf-8")
    assert declared_license(tmp_path) is None


# --------------------------------------------------------------------------
# licence lookup
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ident,family",
    [
        ("apache-2.0", Family.PERMISSIVE),
        ("MIT", Family.PERMISSIVE),
        ("llama3.1", Family.COMMUNITY),
        ("Llama-3.1", Family.COMMUNITY),
        ("gemma", Family.COMMUNITY),
        ("cc-by-nc-4.0", Family.NON_COMMERCIAL),
        ("creativeml-openrail-m", Family.RAIL),
        ("gpl-3.0", Family.COPYLEFT),
    ],
)
def test_recognised_licences(ident: str, family: Family) -> None:
    assert lookup(ident).family is family


def test_unrecognised_is_unknown_never_permissive() -> None:
    """Guessing in the seller's favour is how you distribute what you may not."""
    assert lookup("some-bespoke-licence").family is Family.UNKNOWN
    assert lookup(None).family is Family.UNKNOWN
    assert lookup("").family is Family.UNKNOWN


# --------------------------------------------------------------------------
# lineage extraction
# --------------------------------------------------------------------------

def test_adapter_config_wins(tmp_path: Path) -> None:
    (tmp_path / "adapter_config.json").write_text(
        '{"base_model_name_or_path": "mistralai/Mistral-7B-v0.1"}'
    )
    edges = extract_lineage(tmp_path)
    assert [(e.role, e.ref) for e in edges] == [("base", "mistralai/Mistral-7B-v0.1")]


def test_base_model_from_card(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        card("license: llama3.1\nbase_model: meta-llama/Llama-3.1-8B"), encoding="utf-8"
    )
    assert [e.ref for e in extract_lineage(tmp_path)] == ["meta-llama/Llama-3.1-8B"]


def test_merged_models_become_parents(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        card("merged_models:\n  - org/a\n  - org/b"), encoding="utf-8"
    )
    edges = extract_lineage(tmp_path)
    assert {e.role for e in edges} == {"merged_from"}
    assert len(edges) == 2


def test_config_name_or_path_is_the_last_resort(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text('{"_name_or_path": "Qwen/Qwen2.5-0.5B"}')
    assert [e.ref for e in extract_lineage(tmp_path)] == ["Qwen/Qwen2.5-0.5B"]


def test_duplicate_parents_are_collapsed(tmp_path: Path) -> None:
    (tmp_path / "adapter_config.json").write_text('{"base_model_name_or_path": "org/base"}')
    (tmp_path / "README.md").write_text(card("base_model: org/base"), encoding="utf-8")
    assert len(extract_lineage(tmp_path)) == 1


def test_parents_start_unverified(tmp_path: Path) -> None:
    """Nothing is verified until we confirm it independently; the report says so."""
    (tmp_path / "README.md").write_text(card("base_model: org/base"), encoding="utf-8")
    assert extract_lineage(tmp_path)[0].verified is False


def test_a_local_path_is_not_a_parent(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text('{"_name_or_path": "./local-checkpoint"}')
    assert extract_lineage(tmp_path) == []


# --------------------------------------------------------------------------
# chain evaluation -- the commercially load-bearing part
# --------------------------------------------------------------------------

def _parent(ref: str, license: str | None) -> ParentEdge:
    return ParentEdge(role="base", ref=ref, license=license)


def test_permissive_chain_clears() -> None:
    verdict = evaluate_chain("apache-2.0", [_parent("org/base", "apache-2.0")])
    assert verdict.ok is True
    assert verdict.sellable


def test_non_commercial_base_blocks_a_sale() -> None:
    """The headline case: a paid listing on a CC-BY-NC base is unsellable."""
    verdict = evaluate_chain("apache-2.0", [_parent("org/base", "cc-by-nc-4.0")])
    assert verdict.ok is False
    assert verdict.commercial_use is False
    assert not verdict.sellable
    assert any("cannot be sold" in n for n in verdict.notes)


def test_llama_cannot_be_relicensed_as_apache() -> None:
    verdict = evaluate_chain("apache-2.0", [_parent("meta-llama/Llama-3.1-8B", "llama3.1")])
    assert verdict.ok is False
    assert any("cannot be relicensed" in n for n in verdict.notes)


def test_llama_derivative_keeping_llama_terms_clears() -> None:
    verdict = evaluate_chain("llama3.1", [_parent("meta-llama/Llama-3.1-8B", "llama3.1")])
    assert verdict.ok is True
    assert any("travel" in n for n in verdict.notes)  # notices still flagged


def test_rail_restrictions_are_surfaced() -> None:
    verdict = evaluate_chain("openrail", [_parent("org/base", "creativeml-openrail-m")])
    assert any("carried through to the buyer" in n for n in verdict.notes)


def test_unknown_parent_cannot_clear() -> None:
    verdict = evaluate_chain("apache-2.0", [_parent("org/mystery", None)])
    assert verdict.ok is None  # not True
    assert verdict.commercial_use is None
    assert not verdict.sellable  # absence of a no is not a yes


def test_unknown_parent_cannot_rescue_an_established_failure() -> None:
    """A known non-commercial parent stays a hard no even amid uncertainty."""
    verdict = evaluate_chain(
        "apache-2.0", [_parent("org/nc", "cc-by-nc-4.0"), _parent("org/mystery", None)]
    )
    assert verdict.ok is False
    assert verdict.commercial_use is False


def test_undeclared_licence_cannot_clear() -> None:
    verdict = evaluate_chain(None, [_parent("org/base", "apache-2.0")])
    assert verdict.ok is None
    assert any("No licence declared" in n for n in verdict.notes)


def test_no_parents_is_unverified_not_original() -> None:
    verdict = evaluate_chain("apache-2.0", [])
    assert verdict.ok is None
    assert any("unverified" in n for n in verdict.notes)


def test_sellable_requires_an_affirmative_yes() -> None:
    assert evaluate_chain("apache-2.0", [_parent("o/b", "mit")]).sellable
    assert not evaluate_chain("apache-2.0", [_parent("o/b", None)]).sellable
    assert not evaluate_chain(None, []).sellable


# --------------------------------------------------------------------------
# end to end over a directory
# --------------------------------------------------------------------------

def test_build_license_info_populates_the_report(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        card("license: apache-2.0\nbase_model: meta-llama/Llama-3.1-8B"), encoding="utf-8"
    )
    parents = extract_lineage(tmp_path)
    parents[0].license = "llama3.1"  # resolved by looking the parent up

    info, verdict = build_license_info(tmp_path, parents)
    assert info.declared == "apache-2.0"
    assert info.spdx == "Apache-2.0"
    assert info.chain_ok is False  # Llama cannot be relicensed as Apache
    assert info.notes
    assert not verdict.sellable


def test_a_licence_failure_blocks_certification() -> None:
    """An undistributable model is refused, not given a poor capability score."""
    from keystone.pipeline import grade

    letter, certified, rationale = grade([], [], license_chain_ok=False)
    assert certified is False
    assert letter == "unrated"  # capability was never the problem
    assert "Licence chain" in rationale
