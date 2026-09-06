"""Export the staged eval sets as a versioned, checkable benchmark release.

The runtime sets live in ``suites/*/assets`` and are gitignored, because a set
that is in the repository is a set a rejected creator can read before they
resubmit. This module produces a *separate*, deliberate copy for publication.

The distinction matters and is not bookkeeping. Once a set is exported here it
is in git history permanently, which retires it as a gating instrument even if
the export is later deleted. That is a fine trade for a published artifact --
it is how HarmBench and JailbreakBench are useful at all -- but it means the
gating sets must be rotated afterwards. ``keystone stage`` regenerates them,
and selection is by content hash rather than RNG, so a rotation is reproducible
rather than merely different.

Everything emitted carries its provenance. The items are ours; the seeds are
not, and "derived from" is the only accurate description of them.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from keystone.registry import SUITES_ROOT, discover

RELEASE_VERSION = "1.0.0"

# Where each domain's material comes from. The probe seed sets the capability
# scale; the elicitation seeds supply behaviours that are reproduced verbatim
# and only re-framed, so their harm is the harm somebody else already assessed.
SEEDS: dict[str, dict[str, object]] = {
    "bio": {
        "probe": {
            "name": "WMDP-bio",
            "url": "https://huggingface.co/datasets/cais/wmdp",
            "note": "Hazardous-knowledge proxy. Four-way; chance floor 0.25.",
        },
        "elicitation": [
            {
                "name": "HarmBench (chemical_biological, standard)",
                "url": "https://github.com/centerforaisafety/HarmBench",
                "revision": "8e1604d1171fe8a48d8febecd22f600e462bdcdd",
            }
        ],
    },
    "coding": {
        "probe": {
            "name": "SWE-bench Verified",
            "url": "https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified",
            "note": (
                "Distractors are mutations of the gold patch, so the item cannot "
                "be answered by topic-matching the issue text."
            ),
        },
        "elicitation": [
            {
                "name": "HarmBench (cybercrime_intrusion, standard)",
                "url": "https://github.com/centerforaisafety/HarmBench",
                "revision": "8e1604d1171fe8a48d8febecd22f600e462bdcdd",
            },
            {
                "name": "AdvBench (harmful_behaviors)",
                "url": "https://github.com/llm-attacks/llm-attacks",
            },
        ],
    },
    "legal": {
        "probe": {
            "name": "LegalBench",
            "url": "https://huggingface.co/datasets/nguha/legalbench",
            "note": "Largely binary; chance floor 0.50, which is why raw scores are corrected.",
        },
        "elicitation": [
            {
                "name": "HarmBench (illegal, standard)",
                "url": "https://github.com/centerforaisafety/HarmBench",
                "revision": "8e1604d1171fe8a48d8febecd22f600e462bdcdd",
            },
            {
                "name": "StrongREJECT",
                "url": "https://github.com/alexandrasouly/strongreject",
            },
        ],
    },
}


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _describe(rows: list[dict], role: str) -> dict:
    """Shape facts a reader needs before trusting a count.

    Elicitation items are reported by *behaviour* as well as by item, because
    the two differ by more than an order of magnitude and only the first is a
    count of independent observations.
    """
    if role == "probe":
        arities = sorted({len(row.get("choices", [])) for row in rows})
        return {
            "items": len(rows),
            "options_per_item": arities,
            "chance_floor": round(1 / arities[0], 4) if arities and arities[0] else None,
        }

    behaviours = {row.get("seed_fingerprint") for row in rows}
    families: dict[str, int] = {}
    sources: dict[str, int] = {}
    for row in rows:
        families[row.get("framing_family", "?")] = families.get(row.get("framing_family", "?"), 0) + 1
        sources[row.get("source", "?")] = sources.get(row.get("source", "?"), 0) + 1
    return {
        "items": len(rows),
        # The number that actually bounds precision. Framings multiply items and
        # leave this untouched, so a large set is not a large sample.
        "seed_behaviours": len(behaviours),
        "framings": len({row.get("framing") for row in rows}),
        "by_technique_family": dict(sorted(families.items())),
        "by_seed_source": dict(sorted(sources.items())),
    }


def export(out_dir: Path, suites_root: Path = SUITES_ROOT) -> dict:
    """Copy every staged domain set into `out_dir` and return the manifest."""
    paired = [s.manifest for s in discover(suites_root) if s.manifest.domain]
    if not paired:
        raise RuntimeError("no domain suites are installed")

    entries: list[dict] = []
    for manifest in sorted(paired, key=lambda m: (m.domain or "", m.role or "")):
        source = suites_root / manifest.id / "assets" / "items.json"
        if not source.exists():
            raise RuntimeError(
                f"{manifest.id} has no staged items; run `keystone stage` first"
            )
        rows = json.loads(source.read_text(encoding="utf-8"))
        payload = (json.dumps(rows, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

        target = out_dir / (manifest.domain or "") / f"{manifest.role}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

        entries.append({
            "path": f"{manifest.domain}/{manifest.role}.json",
            "suite_id": manifest.id,
            "domain": manifest.domain,
            "role": manifest.role,
            "conditioned_by": manifest.conditioned_by,
            "sha256": digest(payload),
            "bytes": len(payload),
            **_describe(rows, manifest.role or ""),
            "derived_from": (SEEDS.get(manifest.domain or "", {}) or {}).get(
                manifest.role or ""
            ),
        })

    manifest_doc = {
        "release_version": RELEASE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "description": (
            "Capability-conditioned safety assurance sets. Each domain is a pair: "
            "a capability probe that sets the tolerated-harm ceiling, and a "
            "domain-matched elicitation set measured against it."
        ),
        "provenance": (
            "Items are generated by transforming seed corpora, never invented. "
            "Ground truth is inherited from the seed rather than asserted, which "
            "is what makes a generated item safe to set a safety threshold from. "
            "Seeds are third-party and used under their own terms."
        ),
        "datasets": entries,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest_doc


def verify(out_dir: Path) -> list[str]:
    """Re-hash every file against the manifest. Returns the mismatches."""
    doc = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    broken: list[str] = []
    for entry in doc["datasets"]:
        path = out_dir / entry["path"]
        if not path.exists():
            broken.append(f"{entry['path']}: missing")
        elif digest(path.read_bytes()) != entry["sha256"]:
            broken.append(f"{entry['path']}: digest mismatch")
    return broken
