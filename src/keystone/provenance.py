"""Lineage extraction and license-chain evaluation.

Two of the four things a certificate claims to establish. Security says the file
is safe to load and behaviour says the model does what it claims; this says
where the weights came from and whether the seller is allowed to sell them.

That second question is not paperwork. "Open" does not mean redistributable: a
Llama derivative cannot be relicensed however its author likes, a
non-commercial base makes a paid listing unsellable no matter what the seller
wrote on the card, and RAIL use-restrictions travel through to the buyer's
contract. A marketplace that ignores this sells its customers a liability.

Scope, stated plainly: this **detects and flags**. It is not legal advice and
it does not clear anything. Anything it cannot determine comes back as unknown
rather than as a pass, because a confident wrong answer here is worse than no
answer. Real legal review sits behind it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from keystone.schema import LicenseInfo, ParentEdge


class Family(str, Enum):
    PERMISSIVE = "permissive"        # Apache, MIT, BSD
    COPYLEFT = "copyleft"            # GPL-ish; rare for weights but real
    COMMUNITY = "community"          # Llama, Gemma: open-ish, terms travel
    RAIL = "rail"                    # use-restriction clauses travel
    NON_COMMERCIAL = "non_commercial"
    PROPRIETARY = "proprietary"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class License:
    spdx: str
    family: Family
    commercial_use: bool = True
    # Whether a derivative may be released under different terms.
    relicensable: bool = True
    # Whether the original notices/terms must travel with a derivative.
    notices_travel: bool = False
    note: str = ""


# Deliberately small and explicit. An unrecognised identifier is UNKNOWN, never
# assumed permissive -- guessing in the seller's favour is how a marketplace
# ends up distributing something it had no right to.
_LICENSES: dict[str, License] = {
    "apache-2.0": License("Apache-2.0", Family.PERMISSIVE),
    "mit": License("MIT", Family.PERMISSIVE),
    "bsd-3-clause": License("BSD-3-Clause", Family.PERMISSIVE),
    "bsd-2-clause": License("BSD-2-Clause", Family.PERMISSIVE),
    "cc-by-4.0": License("CC-BY-4.0", Family.PERMISSIVE),
    "cc0-1.0": License("CC0-1.0", Family.PERMISSIVE),
    "gpl-3.0": License("GPL-3.0", Family.COPYLEFT, relicensable=False, notices_travel=True),
    "agpl-3.0": License("AGPL-3.0", Family.COPYLEFT, relicensable=False, notices_travel=True),
    "cc-by-nc-4.0": License(
        "CC-BY-NC-4.0", Family.NON_COMMERCIAL, commercial_use=False,
        relicensable=False, notices_travel=True,
        note="Non-commercial terms: derivatives cannot be sold.",
    ),
    "cc-by-nc-sa-4.0": License(
        "CC-BY-NC-SA-4.0", Family.NON_COMMERCIAL, commercial_use=False,
        relicensable=False, notices_travel=True,
        note="Non-commercial share-alike: derivatives cannot be sold.",
    ),
    "openrail": License(
        "OpenRAIL", Family.RAIL, relicensable=False, notices_travel=True,
        note="Use restrictions must be carried through to the buyer.",
    ),
    "bigscience-openrail-m": License(
        "BigScience-OpenRAIL-M", Family.RAIL, relicensable=False, notices_travel=True,
        note="Use restrictions must be carried through to the buyer.",
    ),
    "creativeml-openrail-m": License(
        "CreativeML-OpenRAIL-M", Family.RAIL, relicensable=False, notices_travel=True,
        note="Use restrictions must be carried through to the buyer.",
    ),
    "llama2": License(
        "LLAMA-2-Community", Family.COMMUNITY, relicensable=False, notices_travel=True,
        note="Meta community licence: notices and acceptable-use terms travel with derivatives.",
    ),
    "llama3": License(
        "LLAMA-3-Community", Family.COMMUNITY, relicensable=False, notices_travel=True,
        note="Meta community licence: notices and acceptable-use terms travel with derivatives.",
    ),
    "llama3.1": License(
        "LLAMA-3.1-Community", Family.COMMUNITY, relicensable=False, notices_travel=True,
        note="Meta community licence: notices and acceptable-use terms travel with derivatives.",
    ),
    "llama3.2": License(
        "LLAMA-3.2-Community", Family.COMMUNITY, relicensable=False, notices_travel=True,
        note="Meta community licence: notices and acceptable-use terms travel with derivatives.",
    ),
    "gemma": License(
        "Gemma-Terms", Family.COMMUNITY, relicensable=False, notices_travel=True,
        note="Google Gemma terms: use policy travels with derivatives.",
    ),
    "other": License("Other", Family.UNKNOWN, relicensable=False),
    "unknown": License("Unknown", Family.UNKNOWN, relicensable=False),
}

UNKNOWN_LICENSE = License("Unknown", Family.UNKNOWN, relicensable=False)


def lookup(identifier: str | None) -> License:
    if not identifier:
        return UNKNOWN_LICENSE
    key = identifier.strip().lower().replace(" ", "-")
    if key in _LICENSES:
        return _LICENSES[key]
    # Llama identifiers appear in many spellings across cards.
    if key.startswith("llama"):
        for known in ("llama3.2", "llama3.1", "llama3", "llama2"):
            if known.replace(".", "") in key.replace(".", "").replace("-", ""):
                return _LICENSES[known]
    if "openrail" in key:
        return _LICENSES["openrail"]
    if key.startswith("cc-by-nc"):
        return _LICENSES["cc-by-nc-4.0"]
    return UNKNOWN_LICENSE


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_card_frontmatter(text: str) -> dict:
    """Read a model card's YAML frontmatter.

    Hand-rolled for the handful of scalar and list keys we care about, so an
    exotic card cannot fail ingest -- and so a malformed card degrades to
    "unknown" instead of raising.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return {}

    data: dict = {}
    key: str | None = None
    for raw in match.group(1).splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and key:
            data.setdefault(key, [])
            if isinstance(data[key], list):
                data[key].append(line.lstrip()[2:].strip().strip("\"'"))
            continue
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("\"'")
            data[key] = value if value else []
    return data


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str) and v]
    return []


def _is_repo_ref(ref: str) -> bool:
    """A parent must be a hub reference, not a path on the trainer's disk.

    `_name_or_path` frequently holds something like "./checkpoint-500", which
    names nothing anyone else can resolve and is not provenance.
    """
    if not ref or ref.startswith((".", "/", "~")) or "\\" in ref:
        return False
    parts = ref.split("/")
    return len(parts) == 2 and all(parts)


def extract_lineage(root: Path) -> list[ParentEdge]:
    """Derive parent edges from whatever the artifact actually declares.

    Sources, in descending reliability: the PEFT adapter config (unambiguous),
    the card's `base_model` field (the ecosystem convention), then the config's
    own `_name_or_path`. Nothing here is verified -- `verified` stays False
    until we confirm the parent independently, and the report says so.
    """
    edges: list[ParentEdge] = []
    seen: set[tuple[str, str]] = set()

    def add(role: str, ref: str) -> None:
        ref = ref.strip()
        if not _is_repo_ref(ref) or (role, ref) in seen:
            return
        seen.add((role, ref))
        edges.append(ParentEdge(role=role, ref=ref, verified=False))

    adapter = _read(root / "adapter_config.json")
    if adapter:
        add("base", str(adapter.get("base_model_name_or_path", "")))

    card = root / "README.md"
    if card.is_file():
        try:
            meta = parse_card_frontmatter(card.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            meta = {}
        for ref in _as_list(meta.get("base_model")):
            add("base", ref)
        for ref in _as_list(meta.get("merged_models")):
            add("merged_from", ref)

    config = _read(root / "config.json")
    if not edges:
        add("base", str(config.get("_name_or_path", "")))

    return edges


def declared_license(root: Path) -> str | None:
    card = root / "README.md"
    if card.is_file():
        try:
            meta = parse_card_frontmatter(card.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None
        value = meta.get("license")
        if isinstance(value, str) and value:
            return value
        listed = _as_list(value)
        if listed:
            return listed[0]
    return None


# --------------------------------------------------------------------------
# chain evaluation
# --------------------------------------------------------------------------

@dataclass
class ChainVerdict:
    """Whether the declared terms survive the derivation chain."""

    ok: bool | None  # None = cannot determine; never optimistic
    commercial_use: bool | None
    notes: list[str] = field(default_factory=list)

    @property
    def sellable(self) -> bool:
        """A paid listing requires an affirmative yes, not an absence of no.

        Both conditions, not either: the chain has to clear *and* commercial
        use has to be permitted. A Llama derivative mislabelled as Apache
        permits commercial use and still fails, because the terms it is being
        sold under are not the terms it actually carries.
        """
        return self.ok is True and self.commercial_use is True


def evaluate_chain(declared: str | None, parents: list[ParentEdge]) -> ChainVerdict:
    """Check the seller's declared terms against what the parents permit.

    The asymmetry is deliberate. A pass requires every link to be known and
    compatible; a single unknown parent yields `ok=None`, because "we could not
    tell" and "it is fine" are different answers and only one of them is safe
    to print on a certificate.
    """
    notes: list[str] = []
    child = lookup(declared)

    if child is UNKNOWN_LICENSE:
        notes.append(
            f"Declared licence {declared!r} is not recognised."
            if declared
            else "No licence declared on the model card."
        )

    if not parents:
        # Nothing to contradict, but also nothing established. A model with no
        # stated ancestry is not thereby original.
        notes.append("No parent models declared; derivation chain unverified.")
        return ChainVerdict(
            ok=None if child is UNKNOWN_LICENSE else None,
            commercial_use=child.commercial_use if child is not UNKNOWN_LICENSE else None,
            notes=notes,
        )

    unknown_parents = [p for p in parents if lookup(p.license) is UNKNOWN_LICENSE]
    known = [(p, lookup(p.license)) for p in parents if lookup(p.license) is not UNKNOWN_LICENSE]

    commercial: bool | None = child.commercial_use
    ok: bool | None = True

    for parent, lic in known:
        if not lic.commercial_use:
            commercial = False
            ok = False
            notes.append(
                f"{parent.ref} is {lic.spdx}: non-commercial terms travel to derivatives, "
                "so this cannot be sold."
            )
        if not lic.relicensable and child is not UNKNOWN_LICENSE and child.spdx != lic.spdx:
            ok = False
            notes.append(
                f"{parent.ref} is {lic.spdx}, which cannot be relicensed as "
                f"{child.spdx}. The original terms must be retained."
            )
        if lic.notices_travel:
            notes.append(f"{parent.ref}: {lic.note or 'original notices must travel with the derivative.'}")

    for parent in unknown_parents:
        notes.append(
            f"{parent.ref} has an unrecognised or missing licence; the chain cannot be cleared."
        )

    if unknown_parents or child is UNKNOWN_LICENSE:
        # Only downgrade -- an established failure stays a failure.
        if ok is not False:
            ok = None
        if commercial is not False:
            commercial = None

    return ChainVerdict(ok=ok, commercial_use=commercial, notes=notes)


def build_license_info(root: Path, parents: list[ParentEdge]) -> tuple[LicenseInfo, ChainVerdict]:
    declared = declared_license(root)
    child = lookup(declared)
    verdict = evaluate_chain(declared, parents)
    return (
        LicenseInfo(
            declared=declared,
            spdx=child.spdx if child is not UNKNOWN_LICENSE else None,
            chain_ok=verdict.ok,
            notes=verdict.notes,
        ),
        verdict,
    )
