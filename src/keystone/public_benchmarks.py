"""Public benchmark catalogue and model-size-aware evaluation cost estimates.

This is deliberately separate from suite discovery. Several public benchmarks
use their own agent, container, file-generation, or judge harnesses rather than
the single-turn ``Suite`` interface. The catalogue is the product contract;
official harness adapters can execute behind it without changing API ids.

Prices are estimated direct run costs, not claims about third-party fees. They
combine workload overhead (CPU containers, storage, and external judges) with
model inference scaled by uploaded weight bytes. The 14 GB reference is roughly
a 7B BF16 checkpoint. Actual cost is recorded after every certification and
should be used to recalibrate these coefficients.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from keystone.payments import Currency, Money

REFERENCE_WEIGHT_BYTES = 14_000_000_000
MIN_MODEL_SCALE = 0.20
MAX_MODEL_SCALE = 8.0
ROUNDING_MINOR = 10_000  # one cent for six-decimal USDC

ScoreDirection = Literal["higher", "lower"]
HarnessKind = Literal["agent", "multiple_choice", "expert_math"]


@dataclass(frozen=True)
class PublicBenchmark:
    suite_id: str
    display_name: str
    version: str
    description: str
    score_direction: ScoreDirection
    harness_kind: HarnessKind
    fixed_cost_minor: int
    inference_cost_minor_at_reference: int
    source_url: str
    mandatory: bool = False

    def estimated_price_minor(self, model_weight_bytes: int | None = None) -> int:
        """Estimated direct cost for this workload and uploaded checkpoint.

        Weight bytes are a better early pricing input than a seller-entered
        parameter count: they are already present in the signed upload manifest
        and approximate the memory bandwidth paid for during local inference.
        """
        if model_weight_bytes is None:
            scale = 1.0
        else:
            scale = min(
                MAX_MODEL_SCALE,
                max(MIN_MODEL_SCALE, model_weight_bytes / REFERENCE_WEIGHT_BYTES),
            )
        raw = self.fixed_cost_minor + self.inference_cost_minor_at_reference * scale
        return int(math.ceil(raw / ROUNDING_MINOR) * ROUNDING_MINOR)

    def as_dict(self, model_weight_bytes: int | None = None) -> dict:
        price = Money(self.estimated_price_minor(model_weight_bytes), Currency.USDC)
        return {
            "suite_id": self.suite_id,
            "display_name": self.display_name,
            "version": self.version,
            "mandatory": self.mandatory,
            "gate": False,
            "diagnostic": False,
            "held_out": False,
            "price": str(price),
            "price_minor": price.amount_minor,
            "price_is_estimate": True,
            "score_direction": self.score_direction,
            "harness_kind": self.harness_kind,
            "source_url": self.source_url,
            "description": self.description,
        }


BENCHMARKS: tuple[PublicBenchmark, ...] = (
    PublicBenchmark(
        suite_id="swe_bench_verified",
        display_name="SWE-bench Verified",
        version="2026-08",
        description="500 engineer-verified GitHub issues, scored by repository tests in isolated containers.",
        score_direction="higher",
        harness_kind="agent",
        fixed_cost_minor=80_000_000,
        inference_cost_minor_at_reference=120_000_000,
        source_url="https://github.com/SWE-bench/SWE-bench",
    ),
    PublicBenchmark(
        suite_id="gdpval",
        display_name="GDPval",
        version="2026-02",
        description="Real-world economically valuable work products across 44 occupations, graded against expert rubrics.",
        score_direction="higher",
        harness_kind="agent",
        fixed_cost_minor=50_000_000,
        inference_cost_minor_at_reference=40_000_000,
        source_url="https://huggingface.co/datasets/openai/gdpval",
    ),
    PublicBenchmark(
        suite_id="harvey_lab",
        display_name="Harvey LAB",
        version="1.0",
        description="Long-horizon legal-agent tasks with matter files, required deliverables, and expert-written rubrics.",
        score_direction="higher",
        harness_kind="agent",
        fixed_cost_minor=650_000_000,
        inference_cost_minor_at_reference=350_000_000,
        source_url="https://github.com/harveyai/harvey-labs",
    ),
    PublicBenchmark(
        suite_id="mmlu_pro",
        display_name="MMLU-Pro",
        version="2025-11",
        description="12,000+ reasoning-focused multiple-choice questions across 14 academic and professional domains.",
        score_direction="higher",
        harness_kind="multiple_choice",
        fixed_cost_minor=250_000,
        inference_cost_minor_at_reference=3_750_000,
        source_url="https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro",
        mandatory=True,
    ),
    PublicBenchmark(
        suite_id="frontiermath",
        display_name="FrontierMath",
        version="2025-02-28",
        description="Expert-level mathematics with controlled problem access and independently verifiable answers.",
        score_direction="higher",
        harness_kind="expert_math",
        fixed_cost_minor=10_000_000,
        inference_cost_minor_at_reference=5_000_000,
        source_url="https://epoch.ai/frontiermath/tiers-1-4/about",
    ),
)

BY_ID = {benchmark.suite_id: benchmark for benchmark in BENCHMARKS}


class PublicBenchmarkSelectionError(ValueError):
    pass


def normalise_selection(selected: list[str] | None) -> list[str]:
    chosen = list(dict.fromkeys(selected or []))
    unknown = [suite_id for suite_id in chosen if suite_id not in BY_ID]
    if unknown:
        raise PublicBenchmarkSelectionError(
            f"unknown benchmarks: {', '.join(sorted(unknown))}"
        )
    required = [benchmark.suite_id for benchmark in BENCHMARKS if benchmark.mandatory]
    return list(dict.fromkeys(required + chosen))


def declined_ids(selected: list[str] | None) -> list[str]:
    running = set(normalise_selection(selected))
    return [benchmark.suite_id for benchmark in BENCHMARKS if benchmark.suite_id not in running]


def quote(selected: list[str] | None, model_weight_bytes: int | None = None) -> Money:
    running = set(normalise_selection(selected))
    total = sum(
        benchmark.estimated_price_minor(model_weight_bytes)
        for benchmark in BENCHMARKS
        if benchmark.suite_id in running
    )
    return Money(total, Currency.USDC)


def menu(model_weight_bytes: int | None = None) -> list[dict]:
    ordered = sorted(BENCHMARKS, key=lambda benchmark: (not benchmark.mandatory, benchmark.display_name.lower()))
    return [benchmark.as_dict(model_weight_bytes) for benchmark in ordered]
