from __future__ import annotations

from types import SimpleNamespace

from keystone.runner.inspect_benchmarks import result_from_inspect_logs
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
