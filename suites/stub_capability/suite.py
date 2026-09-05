"""Placeholder capability suite.

Exists to prove the platform contract end to end. Real capability suites belong
to the harness side; this one only demonstrates the shape:

  - declare a manifest (id, version, modality, required capabilities)
  - talk to the model through ctx.client, never to weights directly
  - return a SuiteResult that validates against the shared schema
"""

from __future__ import annotations

import asyncio
import time

from keystone.schema import Modality, Status, SuiteResult
from keystone.suites import SuiteContext, SuiteManifest

MANIFEST = SuiteManifest(
    id="stub_capability",
    version="0.1.0",
    modality=[Modality.TEXT],
    required_capabilities=["chat"],
    timeout_s=600,
    held_out=False,
    description="Trivial instruction-following check. Placeholder, not a real benchmark.",
)

# Deliberately trivial. The point is the plumbing, not the measurement.
ITEMS: list[tuple[str, str]] = [
    ("Reply with exactly one word: the capital city of France.", "paris"),
    ("Reply with exactly one word: what colour is a ripe banana?", "yellow"),
    ("Reply with exactly one digit: what is 2 + 2?", "4"),
    ("Reply with exactly one word: the opposite of 'hot'.", "cold"),
]


class StubCapabilitySuite:
    manifest = MANIFEST

    async def run(self, ctx: SuiteContext) -> SuiteResult:
        started = time.monotonic()

        async def ask(prompt: str, expected: str) -> bool:
            out = await ctx.client.chat(
                [{"role": "user", "content": prompt}], max_tokens=16, temperature=0.0
            )
            return expected in out.strip().lower()

        try:
            # Fired concurrently on purpose -- the server batches these, which is
            # the whole reason we serve rather than generate in a loop.
            results = await asyncio.gather(*(ask(p, e) for p, e in ITEMS))
        except Exception as exc:
            return SuiteResult(
                suite_id=MANIFEST.id,
                suite_version=MANIFEST.version,
                status=Status.ERROR,
                error=repr(exc),
                duration_s=round(time.monotonic() - started, 2),
            )

        correct = sum(results)
        score = correct / len(ITEMS)
        return SuiteResult(
            suite_id=MANIFEST.id,
            suite_version=MANIFEST.version,
            status=Status.PASS if score >= 0.5 else Status.WARN,
            score=score,
            metrics={"accuracy": score, "n_correct": float(correct)},
            n_items=len(ITEMS),
            duration_s=round(time.monotonic() - started, 2),
        )


SUITE = StubCapabilitySuite()
