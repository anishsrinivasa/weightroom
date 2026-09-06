"""Suite discovery has to work where the code is installed, not just checked out.

`SUITES_ROOT` was `parents[2] / "suites"`, which is the repository root from
`src/keystone/registry.py` and `/usr/local/lib/python3.12` from
`site-packages/keystone/registry.py`. The image installs the package with pip
and copies the tree next to the working directory, so in production the
computed path did not exist.

Nothing raised. `discover()` walked a missing directory, returned an empty
list, and every caller read that as "this deployment has no suites" -- which
surfaced as a seller's progress panel showing two general screens and no sign
the domain gates existed at all.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from keystone.registry import _find_suites_root, discover


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("KEYSTONE_SUITES_ROOT", raising=False)


def test_a_source_checkout_finds_its_own_tree(clean_env) -> None:
    root = _find_suites_root()
    assert root.is_dir()
    assert (root / "bio_probe" / "suite.py").exists()


def test_the_installed_layout_falls_back_to_the_working_directory(
    tmp_path: Path, monkeypatch, clean_env
) -> None:
    """The container case: the package is in site-packages and the tree sits
    beside the working directory. Nothing above the package resolves, so the
    working directory is what has to answer."""
    beside = tmp_path / "suites"
    (beside / "stub_x").mkdir(parents=True)
    (beside / "stub_x" / "suite.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # Simulate installation by pointing the package-relative guess at a path
    # that does not exist, which is exactly what site-packages produces.
    monkeypatch.setattr(
        "keystone.registry.__file__",
        str(tmp_path / "site-packages" / "keystone" / "registry.py"),
    )
    assert _find_suites_root() == beside


def test_an_explicit_override_wins(tmp_path: Path, monkeypatch) -> None:
    """A deployment that puts the tree somewhere of its own choosing needs a
    way to say so without patching the package."""
    chosen = tmp_path / "elsewhere"
    chosen.mkdir()
    monkeypatch.setenv("KEYSTONE_SUITES_ROOT", str(chosen))
    assert _find_suites_root() == chosen


def test_an_override_pointing_nowhere_does_not_win(tmp_path: Path, monkeypatch) -> None:
    """A typo in the variable must not silently empty the registry."""
    monkeypatch.setenv("KEYSTONE_SUITES_ROOT", str(tmp_path / "does-not-exist"))
    assert _find_suites_root().is_dir()


def test_discovery_actually_returns_the_domain_pairs(clean_env) -> None:
    """The symptom that started this: the conditioned gates must be findable,
    because the seller's progress panel is built by discovering them."""
    conditioned = {
        s.manifest.id for s in discover()
        if s.manifest.gate and s.manifest.conditioned_by
    }
    assert conditioned == {
        "bio_elicitation", "coding_elicitation", "legal_elicitation",
    }


def test_the_env_override_is_read_at_call_time(tmp_path: Path, monkeypatch) -> None:
    """Import-time-only resolution is how this class of bug survives a deploy:
    the value is baked before the environment is complete."""
    chosen = tmp_path / "late"
    chosen.mkdir()
    assert os.environ.get("KEYSTONE_SUITES_ROOT") is None
    monkeypatch.setenv("KEYSTONE_SUITES_ROOT", str(chosen))
    assert _find_suites_root() == chosen
