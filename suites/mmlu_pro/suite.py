"""Pinned 100-task MMLU-Pro evaluation."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from keystone.public_benchmarks import BY_ID, sample_task_ids
from keystone.schema import Modality, Status, SuiteResult
from keystone.suites import SuiteContext, SuiteManifest

BENCHMARK = BY_ID["mmlu_pro"]
MANIFEST = SuiteManifest(
    id=BENCHMARK.suite_id,
    version=BENCHMARK.version,
    display_name=BENCHMARK.display_name,
    modality=[Modality.TEXT],
    required_capabilities=["chat"],
    timeout_s=60 * 60,
    description=BENCHMARK.description,
)


def extract_answer(text: str) -> str | None:
    explicit = re.findall(
        r"(?:final\s+)?answer\s*(?:is|:)?\s*[\(\[]?([A-J])\b",
        text,
        flags=re.IGNORECASE,
    )
    if explicit:
        return explicit[-1].upper()
    boxed = re.findall(r"\\boxed\s*\{?\s*([A-J])\s*\}?", text, re.IGNORECASE)
    if boxed:
        return boxed[-1].upper()
    trailing = re.search(r"\b([A-J])\s*[\).]?\s*$", text.strip(), re.IGNORECASE)
    return trailing.group(1).upper() if trailing else None


def _load_tasks(path: Path) -> list[dict]:
    return json.loads((path / "tasks.json").read_text(encoding="utf-8"))


class MMLUProSuite:
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

        async def ask(task_id: str) -> tuple[str | None, str]:
            row = by_id[task_id]
            options = "\n".join(
                f"{chr(65 + index)}. {option}"
                for index, option in enumerate(row["options"])
            )
            response = await ctx.client.chat(
                [
                    {
                        "role": "user",
                        "content": (
                            f"{row['question']}\n\n{options}\n\n"
                            "Reason carefully, then end with `Answer: <letter>`."
                        ),
                    }
                ],
                max_tokens=2048,
                temperature=0.0,
            )
            return extract_answer(response), row["answer"].upper()

        answers = []
        for pending in asyncio.as_completed([ask(task_id) for task_id in chosen]):
            answers.append(await pending)
            ctx.on_progress(len(answers), len(chosen))
        correct = sum(predicted == expected for predicted, expected in answers)
        parsed = sum(predicted is not None for predicted, _ in answers)
        score = correct / len(answers)
        return SuiteResult(
            suite_id=MANIFEST.id,
            suite_version=MANIFEST.version,
            status=Status.PASS,
            score=score,
            metrics={
                "accuracy": score,
                "n_correct": float(correct),
                "answer_parse_rate": parsed / len(answers),
            },
            n_items=len(answers),
            duration_s=round(time.monotonic() - started, 2),
        )


SUITE = MMLUProSuite()
