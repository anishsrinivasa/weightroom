from __future__ import annotations

import asyncio
import base64
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

from keystone.runner.inspect_benchmarks import (
    PendingRubricRun,
    _json_object,
    _modal_compose_for_dockerfile,
    _swe_modal_sandbox_spec,
    result_from_inspect_logs,
    run_swe_bench_verified,
    score_rubric_run,
)
from keystone.schema import Status


def _sample(value=None, *, scorer="swe_bench_scorer"):
    scores = {} if value is None else {scorer: SimpleNamespace(value=value)}
    return SimpleNamespace(scores=scores)


def test_inspect_results_average_all_required_samples() -> None:
    log = SimpleNamespace(samples=[_sample(1), _sample(0), _sample(1)])

    result = result_from_inspect_logs(
        "swe_bench_verified",
        [log],
        scorer_name="swe_bench_scorer",
        expected_items=3,
        duration_s=1.234,
    )

    assert result.status is Status.PASS
    assert result.score == 2 / 3
    assert result.metrics["resolved_items"] == 2
    assert result.n_items == 3


def test_inspect_results_fail_closed_on_missing_score() -> None:
    log = SimpleNamespace(samples=[_sample(1), _sample(None)])

    result = result_from_inspect_logs(
        "swe_bench_verified",
        [log],
        scorer_name="swe_bench_scorer",
        expected_items=2,
        duration_s=2,
    )

    assert result.status is Status.ERROR
    assert result.score is None
    assert result.metrics["completed_items"] == 1
    assert "1 of 2" in result.error


def test_inspect_results_fail_closed_when_sample_never_returns() -> None:
    result = result_from_inspect_logs(
        "swe_bench_verified",
        [SimpleNamespace(samples=[_sample(1)])],
        scorer_name="swe_bench_scorer",
        expected_items=2,
        duration_s=2,
    )

    assert result.status is Status.ERROR
    assert result.metrics["errored_items"] == 1


def test_json_object_accepts_fenced_judge_output() -> None:
    assert _json_object('analysis\n```json\n{"items": []}\n```')["items"] == []


def test_swe_modal_spec_uses_current_networkless_extension(tmp_path, monkeypatch) -> None:
    from inspect_ai.util import is_compose_yaml

    monkeypatch.chdir(tmp_path)
    sample = SimpleNamespace(
        id="django__django-11039",
        metadata={"image_name": "example/swe-image:latest"},
    )

    spec = _swe_modal_sandbox_spec("modal", sample)
    content = Path(spec.config).read_text(encoding="utf-8")

    assert "x-modal:" in content
    assert "x-inspect_modal_sandbox" not in content
    assert "network_mode: none" in content
    assert "block_network: true" in content
    assert is_compose_yaml(spec.config)


def test_dockerfile_compose_keeps_sandbox_alive_and_networkless(tmp_path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.11-slim\nCMD [\"/bin/bash\"]\n")

    compose = _modal_compose_for_dockerfile(
        dockerfile, tmp_path / "probe-compose.yaml"
    )
    content = compose.read_text()

    assert "command: sleep infinity" in content
    assert "network_mode: none" in content
    assert "block_network: true" in content


def test_swe_adapter_invokes_inspect_and_returns_result(tmp_path, monkeypatch) -> None:
    import inspect_ai
    import inspect_evals.swe_bench as swe_module
    import keystone.runner.inspect_benchmarks as adapter

    task = SimpleNamespace(
        dataset=[
            SimpleNamespace(id="task-1"),
            SimpleNamespace(id="task-2"),
        ]
    )
    captured = {}
    sandbox_package = ModuleType("inspect_sandboxes")
    sandbox_package.__path__ = []
    monkeypatch.setitem(sys.modules, "inspect_sandboxes", sandbox_package)
    monkeypatch.setitem(
        sys.modules, "inspect_sandboxes.modal", ModuleType("inspect_sandboxes.modal")
    )

    monkeypatch.setattr(swe_module, "swe_bench", lambda **kwargs: task)
    monkeypatch.setattr(
        adapter,
        "sample_task_ids",
        lambda available_ids, **kwargs: list(available_ids),
    )

    def fake_eval(actual_task, **kwargs):
        captured["task"] = actual_task
        captured["kwargs"] = kwargs
        return [SimpleNamespace(samples=[_sample(1), _sample(0)])]

    monkeypatch.setattr(inspect_ai, "eval", fake_eval)

    result = run_swe_bench_verified(
        served_model_name="uploaded-model",
        artifact_digest="a" * 64,
        dataset_file=tmp_path / "swe.jsonl",
        log_dir=tmp_path,
        max_samples=2,
    )

    assert captured["task"] is task
    assert captured["kwargs"]["max_samples"] == 2
    assert captured["kwargs"]["sample_id"] == ["task-1", "task-2"]
    assert captured["kwargs"]["sandbox_cleanup"] is True
    assert result.status is Status.PASS
    assert result.score == 0.5


def test_rubric_proxy_scores_all_items(tmp_path) -> None:
    reference = tmp_path / "gold.txt"
    reference.write_text("correct work product")
    encoded = base64.b64encode(b"correct work product").decode()
    tasks = [
        {
            "id": f"task-{index}",
            "prompt": "Produce the requested work product.",
            "rubric": [
                {"rubric_item_id": "a", "score": 2, "criterion": "Correct"},
                {"rubric_item_id": "b", "score": 1, "criterion": "Complete"},
            ],
            "candidate_files": {"answer.txt": encoded},
            "reference_files": [str(reference)],
        }
        for index in range(2)
    ]

    class Judge:
        async def chat(self, messages, **kwargs):
            return '{"items":[{"id":"a","pass":true},{"id":"b","pass":false}]}'

    result = asyncio.run(
        score_rubric_run(
            PendingRubricRun("gdpval", tasks, 2, time.monotonic()),
            Judge(),
        )
    )

    assert result.status is Status.PASS
    assert result.score == 2 / 3
    assert result.metrics["automated_rubric_proxy"] == 1


def test_rubric_proxy_fails_closed_on_incomplete_generation() -> None:
    result = asyncio.run(
        score_rubric_run(
            PendingRubricRun("gdpval", [], 100, time.monotonic(), generation_errors=1),
            object(),
        )
    )
    assert result.status is Status.ERROR
    assert result.score is None


def test_harvey_uses_all_pass_aggregation() -> None:
    encoded = base64.b64encode(b"substantive legal memo").decode()
    task = {
        "id": "legal-task",
        "prompt": "Write the requested legal memo.",
        "rubric": [
            {"id": "C-001", "title": "Correct", "match_criteria": "PASS if correct."},
            {"id": "C-002", "title": "Complete", "match_criteria": "PASS if complete."},
        ],
        "candidate_files": {"memo.txt": encoded},
        "reference_files": [],
    }

    class Judge:
        async def chat(self, messages, **kwargs):
            return '{"items":[{"id":"C-001","pass":true},{"id":"C-002","pass":false}]}'

    result = asyncio.run(
        score_rubric_run(
            PendingRubricRun("harvey_lab", [task], 1, time.monotonic()),
            Judge(),
        )
    )

    assert result.status is Status.PASS
    assert result.score == 0
    assert result.metrics["all_pass_rate"] == 0
    assert result.metrics["criterion_pass_rate"] == 0.5
    assert result.metrics["open_weight_judge_proxy"] == 1
