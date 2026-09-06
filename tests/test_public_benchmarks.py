from __future__ import annotations

import pytest

from keystone.public_benchmarks import (
    BENCHMARKS,
    PublicBenchmarkSelectionError,
    normalise_selection,
    quote,
)


def test_catalogue_contains_only_requested_benchmarks() -> None:
    assert {benchmark.suite_id for benchmark in BENCHMARKS} == {
        "swe_bench_verified",
        "gdpval",
        "harvey_lab",
        "mmlu_pro",
        "frontiermath",
    }


def test_mmlu_pro_is_the_required_general_baseline() -> None:
    assert normalise_selection([]) == ["mmlu_pro"]


def test_cost_estimate_scales_with_server_owned_weight_bytes() -> None:
    small = quote(["swe_bench_verified"], 7_000_000_000)
    reference = quote(["swe_bench_verified"], 14_000_000_000)
    large = quote(["swe_bench_verified"], 28_000_000_000)
    assert small.amount_minor < reference.amount_minor < large.amount_minor


def test_fixed_harness_cost_prevents_linear_underpricing() -> None:
    tiny = quote(["harvey_lab"], 1)
    assert tiny.amount_minor >= 650_000_000


def test_unknown_benchmark_is_rejected() -> None:
    with pytest.raises(PublicBenchmarkSelectionError):
        normalise_selection(["not-real"])
