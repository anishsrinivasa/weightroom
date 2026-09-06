"""Legal capability probe. Sets the safety threshold; is never shown to anyone.

Measures how well a model reasons about contracts, citations, and legal
questions with determinate answers, so the paired elicitation suite can be
judged against a bar that matches. Legal harm is knowledge-gated in the way
that matters here: drafting an instrument that would actually survive scrutiny,
or structuring something to evade a reporting threshold, takes real legal
knowledge. A model without it produces confident nonsense.

Seeded from LegalBench -- consumer contract QA, contract QA, and citation
prediction -- pooled so the probe measures legal reasoning broadly rather than
one narrow skill. Harvey's BigLaw Bench is largely proprietary and cannot be a
probe we run ourselves.

**Binary, not four-way.** Guessing pays 50% here rather than 25%, and the
chance correction accounts for it. A coin-flipper scored against a four-way
floor would be credited with a quarter of a capability band it does not have,
and then held to a stricter safety bar for knowledge it lacks.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from keystone.mcq import BINARY, chance_floor, is_correct, render, well_formed
from keystone.schema import Modality, Status, SuiteResult
from keystone.suites import SuiteContext, SuiteManifest

MANIFEST = SuiteManifest(
    id="legal_probe",
    version="0.1.0",
    display_name="Legal reasoning capability probe",
    mandatory=True,
    price_minor=0,
    internal=True,
    domain="legal",
    role="probe",
    chance_floor=chance_floor(BINARY),
    held_out=False,
    modality=[Modality.TEXT],
    required_capabilities=["chat"],
    timeout_s=1800,
    description="Legal reasoning on determinate questions. Sets the legal safety threshold.",
)

INSTRUCTION = "Answer with a single letter."


def load_items(assets_dir: Path) -> list[dict]:
    path = assets_dir / "items.json"
    if not path.is_file():
        return []
    return [i for i in json.loads(path.read_text(encoding="utf-8")) if well_formed(i, options=BINARY)]


def _skeleton(**overrides) -> dict:
    base = dict(
        suite_id=MANIFEST.id,
        suite_version=MANIFEST.version,
        display_name=MANIFEST.display_name,
        domain=MANIFEST.domain,
        role=MANIFEST.role,
        internal=True,
        chance_floor=MANIFEST.chance_floor,
    )
    base.update(overrides)
    return base


class LegalProbeSuite:
    manifest = MANIFEST

    async def run(self, ctx: SuiteContext) -> SuiteResult:
        started = time.monotonic()
        items = load_items(ctx.assets_dir)

        if not items:
            # Skipped, not failed. Conditioning already fails closed on a probe
            # it cannot read, so an absent probe tightens the bar rather than
            # removing it.
            return SuiteResult(**_skeleton(
                status=Status.SKIPPED,
                error="no items staged at assets/items.json",
                duration_s=round(time.monotonic() - started, 2),
            ))

        async def ask(item: dict) -> bool:
            reply = await ctx.client.chat(
                [{"role": "user", "content": render(
                    item, stem_key="question", instruction=INSTRUCTION
                )}],
                max_tokens=8,
                temperature=0.0,
            )
            return is_correct(reply, item)

        try:
            outcomes = await asyncio.gather(*(ask(i) for i in items))
        except Exception as exc:
            return SuiteResult(**_skeleton(
                status=Status.ERROR,
                error=repr(exc),
                duration_s=round(time.monotonic() - started, 2),
            ))

        correct = sum(outcomes)
        accuracy = correct / len(items)

        # Accuracy per source task. A model strong on consumer contracts and
        # hopeless on citation validity is a different proposition from one
        # that is uniformly mediocre, and the aggregate hides which.
        by_tier: dict[str, list[bool]] = {}
        for item, hit in zip(items, outcomes):
            by_tier.setdefault(item.get("task", "unknown"), []).append(hit)

        return SuiteResult(**_skeleton(
            # A probe has no pass mark of its own; conditioning decides what
            # its number implies.
            status=Status.PASS,
            score=accuracy,
            metrics={
                "accuracy": accuracy,
                "n_correct": float(correct),
                **{
                    f"accuracy.{tier}": sum(hits) / len(hits)
                    for tier, hits in sorted(by_tier.items())
                },
            },
            n_items=len(items),
            duration_s=round(time.monotonic() - started, 2),
        ))


SUITE = LegalProbeSuite()
