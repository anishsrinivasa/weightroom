"""Pinned 100-task MATH-500 evaluation with symbolic answer checking."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from math_verify import parse, verify

from keystone.public_benchmarks import BY_ID, sample_task_ids
from keystone.schema import Modality, Status, SuiteResult
from keystone.suites import SuiteContext, SuiteManifest

BENCHMARK = BY_ID["math_500"]
MANIFEST = SuiteManifest(
    id=BENCHMARK.suite_id,
    version=BENCHMARK.version,
    display_name=BENCHMARK.display_name,
    modality=[Modality.TEXT],
    required_capabilities=["chat"],
    timeout_s=2 * 60 * 60,
    description=BENCHMARK.description,
)


def answers_match(gold: str, response: str) -> bool:
    return bool(verify(parse(gold), parse(response)))


def _load_tasks(path: Path) -> list[dict]:
    return json.loads((path / "tasks.json").read_text(encoding="utf-8"))


class Math500Suite:
    manifest = MANIFEST

    async def run(self, ctx: SuiteContext) -> SuiteResult:
        started = time.monotonic()
        rows = _load_tasks(ctx.assets_dir)
        chosen = sample_task_ids(
            [row["id"] for row in rows],
            artifact_digest=ctx.model_name,
            suite_id=MANIFEST.id,
        )
        by_id = {row["id"]: row for row in rows}

        async def ask(task_id: str) -> tuple[str, str]:
            row = by_id[task_id]
            response = await ctx.client.chat(
                [
                    {
                        "role": "user",
                        "content": (
                            f"{row['problem']}\n\n"
                            "Solve the problem carefully and put only the final answer "
                            "inside \\boxed{}."
                        ),
                    }
                ],
                max_tokens=4096,
                temperature=0.0,
            )
            return response, row["answer"]

        responses = await asyncio.gather(*(ask(task_id) for task_id in chosen))
        correct = sum(answers_match(gold, response) for response, gold in responses)
        score = correct / len(responses)
        return SuiteResult(
            suite_id=MANIFEST.id,
            suite_version=MANIFEST.version,
            status=Status.PASS,
            score=score,
            metrics={"accuracy": score, "n_correct": float(correct)},
            n_items=len(responses),
            duration_s=round(time.monotonic() - started, 2),
        )


SUITE = Math500Suite()
