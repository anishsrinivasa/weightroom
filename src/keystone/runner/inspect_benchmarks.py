"""Inspect-backed public benchmark adapters.

Agent benchmarks cannot be reduced to a prompt and a string comparison.  This
module runs their maintained Inspect Evals implementations and uses Modal
Sandboxes for the per-task workspaces.  Imports stay inside the entry points so
the API and local unit tests do not need the heavyweight evaluation packages.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable

from keystone.public_benchmarks import BY_ID, sample_task_ids
from keystone.schema import Status, SuiteResult

SWE_BENCH_VERIFIED_REVISION = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
INSPECT_AI_VERSION = "0.3.263"
INSPECT_EVALS_VERSION = "0.19.0"
INSPECT_SANDBOXES_VERSION = "0.5.0"


def _numeric_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if 0.0 <= numeric <= 1.0 else None
    if isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError:
            return None
        return numeric if 0.0 <= numeric <= 1.0 else None
    return None


def _sample_score(sample: Any, scorer_name: str) -> float | None:
    scores = getattr(sample, "scores", None) or {}
    score = scores.get(scorer_name)
    if score is None and len(scores) == 1:
        score = next(iter(scores.values()))
    return _numeric_score(getattr(score, "value", None)) if score is not None else None


def result_from_inspect_logs(
    suite_id: str,
    logs: Iterable[Any],
    *,
    scorer_name: str,
    expected_items: int,
    duration_s: float,
) -> SuiteResult:
    """Convert Inspect's logs into the marketplace's fail-closed result."""
    samples = [sample for log in logs for sample in (getattr(log, "samples", None) or [])]
    values = [
        value
        for sample in samples
        if (value := _sample_score(sample, scorer_name)) is not None
    ]
    errors = len(samples) - len(values)
    benchmark = BY_ID[suite_id]
    if len(samples) != expected_items or errors:
        return SuiteResult(
            suite_id=suite_id,
            suite_version=benchmark.version,
            display_name=benchmark.display_name,
            status=Status.ERROR,
            diagnostic=True,
            n_items=len(values),
            duration_s=round(duration_s, 2),
            metrics={
                "completed_items": float(len(values)),
                "errored_items": float(errors + max(0, expected_items - len(samples))),
            },
            error=(
                f"Inspect completed {len(values)} of {expected_items} required tasks; "
                "the benchmark is invalid until every sampled task is scored"
            ),
        )

    mean = sum(values) / len(values)
    return SuiteResult(
        suite_id=suite_id,
        suite_version=benchmark.version,
        display_name=benchmark.display_name,
        status=Status.PASS,
        score=mean,
        n_items=len(values),
        duration_s=round(duration_s, 2),
        metrics={
            "resolved_rate": mean,
            "resolved_items": float(sum(value == 1.0 for value in values)),
            "errored_items": 0.0,
        },
    )


def run_swe_bench_verified(
    *,
    served_model_name: str,
    artifact_digest: str,
    log_dir: Path,
    max_samples: int = 8,
) -> SuiteResult:
    """Run a deterministic 100-task SWE-bench Verified sample.

    Inspect controls the agent loop.  Every task gets an isolated, networkless
    Modal Sandbox built from the published SWE-bench image, and the official
    repository tests determine whether the patch resolves the issue.
    """
    # Importing the provider registers ``sandbox="modal"`` with Inspect.
    import inspect_sandboxes.modal  # noqa: F401
    from inspect_ai import eval as inspect_eval
    from inspect_evals.swe_bench import swe_bench

    started = time.monotonic()
    benchmark = BY_ID["swe_bench_verified"]
    task = swe_bench(
        sandbox_type="modal",
        allow_internet=False,
        revision=SWE_BENCH_VERIFIED_REVISION,
    )
    available_ids = [str(sample.id) for sample in task.dataset]
    chosen = sample_task_ids(
        available_ids,
        artifact_digest=artifact_digest,
        suite_id=benchmark.suite_id,
    )
    logs = inspect_eval(
        task,
        model=f"openai-api/keystone/{served_model_name}",
        model_base_url="http://127.0.0.1:8000/v1",
        model_args={
            "api_key": "not-used",
            # Many uploaded chat models do not expose native function calling.
            # Inspect's emulation keeps the same ReAct tool contract portable.
            "emulate_tools": True,
            "responses_api": False,
        },
        sample_id=chosen,
        max_samples=max_samples,
        max_sandboxes=max_samples,
        fail_on_error=False,
        continue_on_fail=True,
        retry_on_error=1,
        sandbox_cleanup=True,
        log_dir=str(log_dir),
        display="plain",
    )
    return result_from_inspect_logs(
        benchmark.suite_id,
        logs,
        scorer_name="swe_bench_scorer",
        expected_items=len(chosen),
        duration_s=time.monotonic() - started,
    )
