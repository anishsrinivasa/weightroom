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

import hashlib
import math
import random
from dataclasses import dataclass
from typing import Literal, Sequence

from keystone.payments import Currency, Money

REFERENCE_WEIGHT_BYTES = 14_000_000_000
MIN_MODEL_SCALE = 0.20
MAX_MODEL_SCALE = 8.0
ROUNDING_MINOR = 10_000  # one cent for six-decimal USDC
TASK_SAMPLE_SIZE = 100
SAMPLING_STRATEGY = "deterministic_random_without_replacement"
SAMPLING_SEED_VERSION = "artifact-digest-v1"

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
    task_count: int
    setup_cost_minor: int
    inference_cost_minor_per_task_at_reference: int
    source_url: str

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
        raw = (
            self.setup_cost_minor
            + self.inference_cost_minor_per_task_at_reference * TASK_SAMPLE_SIZE * scale
        )
        return int(math.ceil(raw / ROUNDING_MINOR) * ROUNDING_MINOR)

    def as_dict(self, model_weight_bytes: int | None = None) -> dict:
        price = Money(self.estimated_price_minor(model_weight_bytes), Currency.USDC)
        return {
            "suite_id": self.suite_id,
            "display_name": self.display_name,
            "version": self.version,
            "gate": False,
            "diagnostic": False,
            "held_out": False,
            "price": str(price),
            "price_minor": price.amount_minor,
            "price_is_estimate": True,
            "score_direction": self.score_direction,
            "harness_kind": self.harness_kind,
            "task_count": self.task_count,
            "sample_size": TASK_SAMPLE_SIZE,
            "sampling_strategy": SAMPLING_STRATEGY,
            "sampling_seed_version": SAMPLING_SEED_VERSION,
            "source_url": self.source_url,
            "description": self.description,
        }


BENCHMARKS: tuple[PublicBenchmark, ...] = (
    PublicBenchmark(
        suite_id="swe_bench_verified",
        display_name="SWE-bench Verified",
        version="c104f840cc67f8b6eec6f759ebc8b2693d585d4a",
        description="500 engineer-verified GitHub issues, scored by repository tests in isolated containers.",
        score_direction="higher",
        harness_kind="agent",
        task_count=500,
        setup_cost_minor=5_000_000,
        inference_cost_minor_per_task_at_reference=350_000,
        source_url="https://github.com/SWE-bench/SWE-bench",
    ),
    PublicBenchmark(
        suite_id="gdpval",
        display_name="GDPval",
        version="2026-02",
        description="Real-world economically valuable work products across 44 occupations, graded against expert rubrics.",
        score_direction="higher",
        harness_kind="agent",
        task_count=220,
        setup_cost_minor=5_000_000,
        inference_cost_minor_per_task_at_reference=360_000,
        source_url="https://huggingface.co/datasets/openai/gdpval",
    ),
    PublicBenchmark(
        suite_id="harvey_lab",
        display_name="Harvey LAB",
        version="1.0",
        description="Long-horizon legal-agent tasks with matter files, required deliverables, and expert-written rubrics.",
        score_direction="higher",
        harness_kind="agent",
        task_count=1_660,
        setup_cost_minor=10_000_000,
        inference_cost_minor_per_task_at_reference=500_000,
        source_url="https://github.com/harveyai/harvey-labs",
    ),
    PublicBenchmark(
        suite_id="mmlu_pro",
        display_name="MMLU-Pro",
        version="b189ec765aa7ed75c8acfea42df31fdae71f97be",
        description="12,000+ reasoning-focused multiple-choice questions across 14 academic and professional domains.",
        score_direction="higher",
        harness_kind="multiple_choice",
        task_count=12_032,
        setup_cost_minor=10_000,
        inference_cost_minor_per_task_at_reference=300,
        source_url="https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro",
    ),
    PublicBenchmark(
        suite_id="math_500",
        display_name="MATH-500",
        version="6e4ed1a2a79af7d8630a6b768ec859cb5af4d3be",
        description="500 public competition-mathematics problems with exact-answer verification.",
        score_direction="higher",
        harness_kind="expert_math",
        task_count=500,
        setup_cost_minor=100_000,
        inference_cost_minor_per_task_at_reference=20_000,
        source_url="https://huggingface.co/datasets/HuggingFaceH4/MATH-500",
    ),
)

BY_ID = {benchmark.suite_id: benchmark for benchmark in BENCHMARKS}


class PublicBenchmarkSelectionError(ValueError):
    pass


def sample_task_ids(
    task_ids: Sequence[str], *, artifact_digest: str, suite_id: str
) -> list[str]:
    """Choose the reproducible 100-task sample for one model and benchmark.

    Sampling is random without replacement, but derived from the immutable
    artifact digest so retries evaluate the same tasks and remain auditable.
    """
    unique_ids = list(dict.fromkeys(task_ids))
    if len(unique_ids) < TASK_SAMPLE_SIZE:
        raise ValueError(
            f"{suite_id} has {len(unique_ids)} tasks; {TASK_SAMPLE_SIZE} are required"
        )
    seed_material = (
        f"{SAMPLING_SEED_VERSION}:{artifact_digest}:{suite_id}".encode("utf-8")
    )
    seed = int.from_bytes(hashlib.sha256(seed_material).digest(), "big")
    return random.Random(seed).sample(unique_ids, TASK_SAMPLE_SIZE)


def normalise_selection(selected: list[str] | None) -> list[str]:
    chosen = list(dict.fromkeys(selected or []))
    unknown = [suite_id for suite_id in chosen if suite_id not in BY_ID]
    if unknown:
        raise PublicBenchmarkSelectionError(
            f"unknown benchmarks: {', '.join(sorted(unknown))}"
        )
    return chosen


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
    ordered = sorted(BENCHMARKS, key=lambda benchmark: benchmark.display_name.lower())
    return [benchmark.as_dict(model_weight_bytes) for benchmark in ordered]
