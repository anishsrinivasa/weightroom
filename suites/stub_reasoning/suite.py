"""Placeholder reasoning benchmark — an optional menu item.

Exists so the benchmark menu has more than one thing on it and the pricing
maths has something to add up. The real benchmarks (SWE-Bench, IFEval, and
whatever else the roadmap names) belong to the evaluation side; this only
demonstrates the shape they plug into.
"""

from __future__ import annotations

import asyncio
import re
import time

from keystone.schema import Modality, Status, SuiteResult
from keystone.suites import SuiteContext, SuiteManifest

MANIFEST = SuiteManifest(
    id="stub_reasoning",
    version="0.1.0",
    display_name="Multi-step reasoning",
    mandatory=False,
    price_minor=20_000_000,  # 20 USDC — more items, more GPU time
    modality=[Modality.TEXT],
    required_capabilities=["chat"],
    timeout_s=900,
    held_out=False,
    description="Small arithmetic word problems. Placeholder, not a real benchmark.",
)

ITEMS: list[tuple[str, int]] = [
    ("A shelf holds 4 boxes. Each box holds 6 cups. How many cups? Reply with the number only.", 24),
    ("You have 17 apples and give away 5, then buy 8 more. How many now? Number only.", 20),
    ("A train travels 60 km in 2 hours. How many km in 5 hours at the same speed? Number only.", 150),
    ("Three friends split a 91 pound bill evenly, rounded down. What does each pay? Number only.", 30),
]


def _first_int(text: str) -> int | None:
    match = re.search(r"-?\d+", text.replace(",", ""))
    return int(match.group()) if match else None


class StubReasoningSuite:
    manifest = MANIFEST

    async def run(self, ctx: SuiteContext) -> SuiteResult:
        started = time.monotonic()

        async def ask(prompt: str, expected: int) -> bool:
            out = await ctx.client.chat(
                [{"role": "user", "content": prompt}], max_tokens=24, temperature=0.0
            )
            return _first_int(out) == expected

        try:
            results = await asyncio.gather(*(ask(p, e) for p, e in ITEMS))
        except Exception as exc:
            return SuiteResult(
                suite_id=MANIFEST.id,
                suite_version=MANIFEST.version,
                display_name=MANIFEST.name,
                status=Status.ERROR,
                error=repr(exc),
                duration_s=round(time.monotonic() - started, 2),
            )

        correct = sum(results)
        score = correct / len(ITEMS)
        return SuiteResult(
            suite_id=MANIFEST.id,
            suite_version=MANIFEST.version,
            display_name=MANIFEST.name,
            status=Status.PASS if score >= 0.5 else Status.WARN,
            score=score,
            metrics={"accuracy": score, "n_correct": float(correct)},
            n_items=len(ITEMS),
            duration_s=round(time.monotonic() - started, 2),
        )


SUITE = StubReasoningSuite()
