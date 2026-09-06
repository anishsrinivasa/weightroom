from __future__ import annotations

import pytest

from keystone.public_benchmarks import (
    BENCHMARKS,
    PublicBenchmarkSelectionError,
    normalise_selection,
    quote,
    sample_task_ids,
)


def test_catalogue_contains_only_requested_benchmarks() -> None:
    assert {benchmark.suite_id for benchmark in BENCHMARKS} == {
        "swe_bench_verified",
        "gdpval",
        "harvey_lab",
        "mmlu_pro",
        "frontiermath",
    }


def test_every_public_benchmark_is_optional() -> None:
    assert normalise_selection([]) == []
    assert quote([]).amount_minor == 0


def test_cost_estimate_scales_with_server_owned_weight_bytes() -> None:
    small = quote(["swe_bench_verified"], 7_000_000_000)
    reference = quote(["swe_bench_verified"], 14_000_000_000)
    large = quote(["swe_bench_verified"], 28_000_000_000)
    assert small.amount_minor < reference.amount_minor < large.amount_minor


def test_fixed_harness_cost_prevents_linear_underpricing() -> None:
    tiny = quote(["harvey_lab"], 1)
    assert tiny.amount_minor >= 10_000_000


def test_each_benchmark_prices_exactly_100_tasks() -> None:
    assert all(benchmark.as_dict()["sample_size"] == 100 for benchmark in BENCHMARKS)


def test_task_sample_is_unique_random_and_reproducible() -> None:
    task_ids = [f"task-{index}" for index in range(500)]
    first = sample_task_ids(task_ids, artifact_digest="a" * 64, suite_id="gdpval")
    retry = sample_task_ids(task_ids, artifact_digest="a" * 64, suite_id="gdpval")
    other_model = sample_task_ids(task_ids, artifact_digest="b" * 64, suite_id="gdpval")

    assert len(first) == len(set(first)) == 100
    assert first == retry
    assert first != other_model


def test_task_sample_refuses_an_undersized_pool() -> None:
    with pytest.raises(ValueError, match="100 are required"):
        sample_task_ids(
            [f"task-{index}" for index in range(99)],
            artifact_digest="a" * 64,
            suite_id="frontiermath",
        )


def test_unknown_benchmark_is_rejected() -> None:
    with pytest.raises(PublicBenchmarkSelectionError):
        normalise_selection(["not-real"])
