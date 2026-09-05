"""Suite discovery.

Suites live outside the platform package on purpose -- they are the harness
side's property. The platform discovers them by manifest, gates them on
capability and modality, and runs them. It never inspects their contents.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
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


@dataclass
class Skipped:
    """A suite that did not run, and whether that was the creator's choice.

    The distinction matters to a buyer. "This model cannot do vision" and "the
    seller chose not to run the coding benchmark" are very different facts.
    """

    suite_id: str
    display_name: str
    reason: str
    declined: bool = False


def select(
    suites: list[Suite],
    caps: Capabilities,
    modality: list[Modality],
    only: list[str] | None = None,
) -> tuple[list[Suite], list[Skipped]]:
    """Split into (eligible, skipped).

    Gating happens before the model is loaded, so a suite that will not run
    costs no GPU time. Mandatory suites ignore `only` entirely -- they are not
    on the menu.
    """
    eligible: list[Suite] = []
    skipped: list[Skipped] = []
    for suite in suites:
        m = suite.manifest

        # Eligibility is checked first, deliberately. A benchmark this model
        # could never have run is not something the seller "declined" -- saying
        # so would credit them with a choice they never had, and hide the more
        # useful fact that the model cannot do it.
        ok, reason = m.is_eligible(caps, modality)
        if not ok:
            skipped.append(Skipped(m.id, m.name, reason))
            continue

        if only is not None and not m.mandatory and m.id not in only:
            skipped.append(Skipped(m.id, m.name, "declined by the creator", declined=True))
            continue

        eligible.append(suite)
    return eligible, skipped
