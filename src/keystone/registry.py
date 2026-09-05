"""Suite discovery.

Suites live outside the platform package on purpose -- they are the harness
side's property. The platform discovers them by manifest, gates them on
capability and modality, and runs them. It never inspects their contents.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from keystone.schema import Capabilities, Modality
from keystone.suites import Suite

SUITES_ROOT = Path(__file__).resolve().parents[2] / "suites"


def _load_module(path: Path):
    name = f"keystone_suite_{path.parent.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load suite at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def discover(root: Path | None = None) -> list[Suite]:
    """Every `suites/*/suite.py` exposing a module-level `SUITE`."""
    root = root or SUITES_ROOT
    found: list[Suite] = []
    if not root.is_dir():
        return found
    for entry in sorted(root.iterdir()):
        suite_file = entry / "suite.py"
        if not suite_file.is_file():
            continue
        module = _load_module(suite_file)
        suite = getattr(module, "SUITE", None)
        if suite is None:
            raise ImportError(f"{suite_file} defines no SUITE")
        found.append(suite)
    return found


def select(
    suites: list[Suite],
    caps: Capabilities,
    modality: list[Modality],
    only: list[str] | None = None,
) -> tuple[list[Suite], list[tuple[str, str]]]:
    """Split into (eligible, [(suite_id, skip_reason)]).

    Gating happens before the model is loaded, so an ineligible suite costs no
    GPU time.
    """
    eligible: list[Suite] = []
    skipped: list[tuple[str, str]] = []
    for suite in suites:
        m = suite.manifest
        if only and m.id not in only:
            skipped.append((m.id, "not selected"))
            continue
        ok, reason = m.is_eligible(caps, modality)
        if ok:
            eligible.append(suite)
        else:
            skipped.append((m.id, reason))
    return eligible, skipped
