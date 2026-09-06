"""Bio capability probe. Sets the safety threshold; is never shown to anyone.

This measures how much hazardous-adjacent biology a model actually knows, so
the paired elicitation suite can be judged against a bar that matches. A model
that cannot answer these cannot be talked into meaningful bio harm however
compliant it is; one that answers them all is dangerous at a refusal rate that
would look fine on a scoreboard.

Multiple choice, deliberately. WMDP is built as a *proxy* for hazardous
knowledge rather than a test of it -- the questions sit next to the dangerous
material without stating it -- and a closed-form answer key means the probe
needs no judge model, so a threshold never depends on a grader's mood. The
same choice keeps this from becoming a hazardous artifact in its own right:
nothing here elicits a protocol.

Items are staged into `assets/items.json` rather than committed. In this first
pass they are the public WMDP-bio set, which is honest but weak -- a public
probe measures memorisation as much as knowledge, and a creator can run it
themselves to read off their own band. Generated items replace them; see the
`chance_floor` note below for what stays true either way.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from keystone.mcq import CHANCE_FLOOR, is_correct, render, well_formed
from keystone.schema import Modality, Status, SuiteResult
from keystone.suites import SuiteContext, SuiteManifest

MANIFEST = SuiteManifest(
    id="bio_probe",
    version="0.1.0",
    display_name="Biosecurity capability probe",
    mandatory=True,
    # Never priced and never on the menu: this is an instrument, not a product.
    price_minor=0,
    # Excluded from the capability letter. A creator who could read their probe
    # score off their own listing could sandbag it for an easier safety bar.
    internal=True,
    domain="bio",
    role="probe",
    # Four-way multiple choice pays 25% for answering at random, so bands are
    # read on the chance-corrected scale and a guessing model lands in
    # `negligible` rather than being credited with partial knowledge.
    chance_floor=CHANCE_FLOOR,
    # The seed items are public, so this pass leaks nothing an external
    # endpoint could not already fetch. Flips to True when generated items
    # replace them.
    held_out=False,
    modality=[Modality.TEXT],
    required_capabilities=["chat"],
    timeout_s=1800,
    description="Hazardous-adjacent biology knowledge. Sets the bio safety threshold.",
)


def load_items(assets_dir: Path) -> list[dict]:
    path = assets_dir / "items.json"
    if not path.is_file():
        return []
    return [i for i in json.loads(path.read_text(encoding="utf-8")) if well_formed(i)]


class BioProbeSuite:
    manifest = MANIFEST

    async def run(self, ctx: SuiteContext) -> SuiteResult:
        started = time.monotonic()
        items = load_items(ctx.assets_dir)

        if not items:
            # Skipped, not failed. The model did nothing wrong, and conditioning
            # already fails closed on a probe it cannot read -- so an absent
            # probe tightens the safety bar rather than removing it.
            return SuiteResult(
                suite_id=MANIFEST.id,
                suite_version=MANIFEST.version,
                status=Status.SKIPPED,
                domain=MANIFEST.domain,
                role=MANIFEST.role,
                internal=True,
                chance_floor=MANIFEST.chance_floor,
                error="no items staged at assets/items.json",
                duration_s=round(time.monotonic() - started, 2),
            )

        async def ask(item: dict) -> bool:
            reply = await ctx.client.chat(
                [{"role": "user", "content": render(item)}],
                max_tokens=8,  # a letter, and room to be wordy about it
                temperature=0.0,
            )
            return is_correct(reply, item)

        try:
            outcomes = await asyncio.gather(*(ask(i) for i in items))
        except Exception as exc:
            return SuiteResult(
                suite_id=MANIFEST.id,
                suite_version=MANIFEST.version,
                status=Status.ERROR,
                domain=MANIFEST.domain,
                role=MANIFEST.role,
                internal=True,
                chance_floor=MANIFEST.chance_floor,
                error=repr(exc),
                duration_s=round(time.monotonic() - started, 2),
            )

        correct = sum(outcomes)
        accuracy = correct / len(items)

        return SuiteResult(
            suite_id=MANIFEST.id,
            suite_version=MANIFEST.version,
            # A probe has no pass mark of its own. It reports what it measured
            # and the conditioning layer decides what that implies.
            status=Status.PASS,
            score=accuracy,
            domain=MANIFEST.domain,
            role=MANIFEST.role,
            internal=True,
            chance_floor=MANIFEST.chance_floor,
            metrics={"accuracy": accuracy, "n_correct": float(correct)},
            n_items=len(items),
            duration_s=round(time.monotonic() - started, 2),
        )


SUITE = BioProbeSuite()
