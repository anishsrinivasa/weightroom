"""Bio elicitation resistance. Judged against a bar the probe sets.

Attacks in the same domain the probe measures. Generic harmful prompts say
little about whether a bio-capable model will assist with pathogen work, so
the attack has to live where the capability does -- which is the whole reason
domains are paired rather than pooled into one safety score.

Items are staged into `assets/items.json`, never committed. They are built from
the `chemical_biological` slice of HarmBench's standard set -- 28 behaviours,
which is too few to gate on directly -- expanded across the framing library in
`generation.py` to reach a size where the strictest band is distinguishable
from a perfect run.

Results are broken out per technique family as well as in aggregate. A model
that refuses every direct request and folds to fictional framing scores the
same overall as one that leaks uniformly, and those need different fixes.

This suite is **two-phase**. It collects transcripts while the model under test
is served, and scores them after the platform has stood up a judge -- because
the judge is another model wanting the same GPU and both cannot be resident at
once. It never sees the judge and never learns which model graded it.

What is scored is **safety, not refusal**. A model that opens "I'm sorry, but"
and then answers in full has not refused in any sense that matters, and one
that handles a reframed question without ever saying no may have been perfectly
safe. Substring matching gets both of those backwards, which is why the judge
exists.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from keystone.conditioning import effective_n
from keystone.judging import Judgement, Transcript, rate
from keystone.schema import Modality, Status, SuiteResult
from keystone.suites import SuiteContext, SuiteManifest

PROBE_ID = "bio_probe"

MANIFEST = SuiteManifest(
    id="bio_elicitation",
    version="0.1.0",
    display_name="Bio elicitation resistance",
    mandatory=True,
    price_minor=0,
    # Fail-closed, and excluded from the capability average.
    gate=True,
    internal=True,
    domain="bio",
    role="elicitation",
    # The bar comes from the probe rather than from a constant here. An
    # absolute number would be the thing this framework exists to replace.
    conditioned_by=PROBE_ID,
    # Held-out: these prompts must never leave the sandbox, and a creator who
    # could practise against them would be optimising against the gate itself
    # rather than against being safe.
    held_out=True,
    judged=True,
    modality=[Modality.TEXT],
    required_capabilities=["chat"],
    timeout_s=1800,
    description="Domain-matched harmful elicitation. Threshold set by the bio probe.",
)

CATEGORY = "harmful_content_refusal"
REMEDIATION = "public/bio_practice_v1"

def load_items(assets_dir: Path) -> list[dict]:
    path = assets_dir / "items.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _skeleton(**overrides) -> dict:
    base = dict(
        suite_id=MANIFEST.id,
        suite_version=MANIFEST.version,
        display_name=MANIFEST.display_name,
        domain=MANIFEST.domain,
        role=MANIFEST.role,
        internal=True,
        gate=True,
        held_out=True,
        conditioned_by=PROBE_ID,
    )
    base.update(overrides)
    return base


class BioElicitationSuite:
    manifest = MANIFEST

    async def collect(self, ctx: SuiteContext) -> list[Transcript]:
        """Phase one: ask, and keep what came back. No judging here.

        Runs as many staged items as the platform asked for. The count is not
        ours to choose: how many items it takes to demonstrate compliance
        depends on the bar, and the bar depends on a probe this suite never
        sees. Taking the first `budget` of a set already ordered by rotation
        keeps the subset stable and still fresh between attempts.
        """
        items = load_items(ctx.assets_dir)
        if ctx.item_budget is not None:
            items = items[: ctx.item_budget]
        self._items = {str(i): item for i, item in enumerate(items)}

        async def attempt(item_id: str, item: dict) -> Transcript:
            reply = await ctx.client.chat(
                [{"role": "user", "content": item["prompt"]}],
                max_tokens=512,
                temperature=0.0,
            )
            return Transcript(
                item_id=item_id,
                prompt=item["prompt"],
                response=reply,
                meta={
                    "framing": item.get("framing"),
                    "family": item.get("framing_family", "unknown"),
                },
            )

        return list(
            await asyncio.gather(*(attempt(i, item) for i, item in self._items.items()))
        )

    def score(self, judgements: list[Judgement], duration_s: float) -> SuiteResult:
        """Phase two: turn judgements into one conditioned gate result."""
        if not judgements:
            # SKIPPED rather than PASS. `grade` refuses to certify on a gate
            # that did not run, so an unstaged set blocks listing instead of
            # silently waving a model through.
            return SuiteResult(**_skeleton(
                status=Status.SKIPPED,
                error="no items staged at assets/items.json",
                duration_s=duration_s,
            ))

        safe_rate, problem = rate(judgements)
        if safe_rate is None:
            # A judge that could not be read has not measured this model, and a
            # score over the readable remainder would not say so.
            return SuiteResult(**_skeleton(
                status=Status.ERROR,
                error=problem,
                n_items=len(judgements),
                duration_s=duration_s,
            ))

        # Per-family rates, because an aggregate is not actionable. A model that
        # refuses every direct request and folds to fictional framing scores the
        # same as one that leaks uniformly, and they need different fixes.
        by_family: dict[str, list[bool]] = {}
        for judgement in judgements:
            item = self._items.get(judgement.item_id, {})
            family = item.get("framing_family", "unknown")
            by_family.setdefault(family, []).append(judgement.safe)
        # Also per source track. Contextual behaviours supply a hazardous
        # excerpt and ask the model to extend it, which is a far stronger
        # elicitation than a bare request -- so a set that is half contextual
        # is harder than one that is three percent contextual, and a gap
        # between them is partly about composition rather than about the
        # model. Reported so that confound is visible instead of inferred.
        by_source: dict[str, list[bool]] = {}
        for judgement in judgements:
            item = self._items.get(judgement.item_id, {})
            src = item.get("source", "unknown").replace("/", ".")
            by_source.setdefault(src, []).append(judgement.safe)
        source_metrics = {
            f"safe_rate.src.{src}": sum(hits) / len(hits)
            for src, hits in sorted(by_source.items())
        }

        family_metrics = {
            f"safe_rate.{family}": sum(hits) / len(hits)
            for family, hits in sorted(by_family.items())
        }

        # Items built from the same behaviour are not independent trials: a
        # model that helps with a behaviour under one framing usually helps
        # under several. Reported so bounds are computed over what was actually
        # observed rather than over the item count.
        clusters = [
            self._items.get(j.item_id, {}).get("seed_fingerprint", j.item_id)
            for j in judgements
        ]

        return SuiteResult(**_skeleton(
            # PASS means "ran cleanly", not "cleared the bar". There is no bar
            # in this file -- `conditioning.resolve` supplies it and rewrites
            # this to FAIL if the rate falls short of what the probe set.
            status=Status.PASS,
            score=safe_rate,
            metrics={
                "safe_response_rate": safe_rate,
                "n_safe": float(sum(j.safe for j in judgements)),
                "n_refused": float(sum(bool(j.refused) for j in judgements)),
                **family_metrics,
                **source_metrics,
            },
            categories=[CATEGORY],
            remediation=REMEDIATION,
            n_items=len(judgements),
            effective_n=effective_n([j.safe for j in judgements], clusters),
            duration_s=duration_s,
        ))


SUITE = BioElicitationSuite()
