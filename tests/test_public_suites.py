from __future__ import annotations

import asyncio
import json
from pathlib import Path

from keystone.registry import discover
from keystone.schema import Capabilities
from keystone.suites import ModelClient, SuiteContext


class FixedClient(ModelClient):
    def __init__(self, response: str) -> None:
        self.response = response

    async def chat(self, messages, *, max_tokens=512, temperature=0.0, **kwargs):
        return self.response

    async def complete(self, prompt, *, max_tokens=512, temperature=0.0, **kwargs):
        return self.response


def _suite(suite_id: str):
    return next(suite for suite in discover() if suite.manifest.id == suite_id)


def _context(tmp_path: Path, suite_id: str, response: str) -> SuiteContext:
    assets = tmp_path / suite_id
    assets.mkdir()
    return SuiteContext(
        client=FixedClient(response),
        model_name="upload@" + "a" * 64,
        capabilities=Capabilities(chat=True),
        scratch_dir=tmp_path / "scratch",
        assets_dir=assets,
    )


def test_mmlu_pro_runs_deterministic_hundred_task_sample(tmp_path: Path) -> None:
    ctx = _context(tmp_path, "mmlu_pro", "Reasoning. Answer: A")
    progress = []
    ctx.on_progress = lambda completed, total: progress.append((completed, total))
    rows = [
        {
            "id": str(index),
            "question": f"Question {index}",
            "options": ["correct", "wrong"],
            "answer": "A",
            "category": "test",
        }
        for index in range(120)
    ]
    (ctx.assets_dir / "tasks.json").write_text(json.dumps(rows))

    result = asyncio.run(_suite("mmlu_pro").run(ctx))

    assert result.n_items == 100
    assert result.score == 1.0
    assert result.metrics["answer_parse_rate"] == 1.0
    assert progress[0] == (1, 100)
    assert progress[-1] == (100, 100)
    assert len(progress) == 100


def test_math_500_uses_symbolic_equivalence_on_hundred_tasks(tmp_path: Path) -> None:
    ctx = _context(tmp_path, "math_500", r"The result is \boxed{1/2}.")
    progress = []
    ctx.on_progress = lambda completed, total: progress.append((completed, total))
    rows = [
        {
            "id": str(index),
            "problem": f"Problem {index}",
            "answer": r"\frac{1}{2}",
            "subject": "Algebra",
            "level": 2,
        }
        for index in range(120)
    ]
    (ctx.assets_dir / "tasks.json").write_text(json.dumps(rows))

    result = asyncio.run(_suite("math_500").run(ctx))

    assert result.n_items == 100
    assert result.score == 1.0
    assert progress[0] == (1, 100)
    assert progress[-1] == (100, 100)
    assert len(progress) == 100
