"""Shared source of truth for the certification report.

This module is authoritative. `schemas/report.schema.json` is generated from it
(`python -m keystone.schema`) so the eval harness and any non-Python consumer
cannot drift from the platform.

Every field that exists only to keep VLM support additive is marked SEAM.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

REPORT_VERSION = "0.2.0"


class Audience(str, Enum):
    """Who is allowed to see a piece of the report.

    Ordered from least to most privileged. A field tagged BUYER is visible to
    everyone; CREATOR is visible to the model's owner and to us; INTERNAL never
    leaves the platform.

    This boundary is load-bearing. Certification gates listing, so a rejected
    creator will resubmit -- and every bit of detail we hand back is a bit of
    our held-out eval set leaked. Coarse categories go out; per-item results
    never do. See `keystone.visibility`.
    """

    BUYER = "buyer"
    CREATOR = "creator"
    INTERNAL = "internal"

    @property
    def rank(self) -> int:
        return {"buyer": 0, "creator": 1, "internal": 2}[self.value]

    def can_see(self, required: Audience) -> bool:
        return self.rank >= required.rank


# --------------------------------------------------------------------------
# Subject: what was certified
# --------------------------------------------------------------------------

class Modality(str, Enum):
    TEXT = "text"
    IMAGE = "image"  # SEAM 1: unused in the LLM MVP, reserved for VLM


class SourceKind(str, Enum):
    HF = "huggingface"
    UPLOAD = "upload"


class Source(BaseModel):
    kind: SourceKind
    ref: str = Field(description="HF repo id, or upload receipt id")
    revision: str | None = Field(default=None, description="Resolved commit sha")


class FileEntry(BaseModel):
    path: str
    size_bytes: int
    sha256: str


class ParentEdge(BaseModel):
    """SEAM 4: lineage is a DAG, not a chain.

    A text model has one `base` parent. A VLM has several (base LLM, vision
    encoder, projector), each potentially under a different license.
    """

    role: Literal["base", "adapter", "vision_encoder", "projector", "merged_from"]
    ref: str
    revision: str | None = None
    license: str | None = None
    verified: bool = False


class LicenseInfo(BaseModel):
    declared: str | None = None
    spdx: str | None = None
    chain_ok: bool | None = Field(
        default=None,
        description="Whether the full derivation chain permits the declared terms. "
        "None = not yet assessed.",
    )
    notes: list[str] = Field(default_factory=list)


class Subject(BaseModel):
    modality: list[Modality] = Field(default_factory=lambda: [Modality.TEXT])  # SEAM 1
    source: Source
    artifact_digest: str = Field(description="sha256 over the sorted file manifest")
    files: list[FileEntry]
    total_bytes: int
    lineage: list[ParentEdge] = Field(default_factory=list)  # SEAM 4
    license: LicenseInfo = Field(default_factory=LicenseInfo)


# --------------------------------------------------------------------------
# How it was stood up
# --------------------------------------------------------------------------

class ProcessorProfile(BaseModel):
    """SEAM 3: null for text-only models; populated for VLMs."""

    image_size: int | None = None
    patch_size: int | None = None
    tiling: str | None = None
    image_token: str | None = None
    processor_config_sha256: str | None = None


class ServingProfile(BaseModel):
    engine: str = "vllm"
    engine_version: str | None = None
    architecture: str | None = None
    dtype: str | None = None
    quantization: str | None = None
    max_context: int | None = None
    chat_template_source: Literal["tokenizer_config", "override", "none"] | None = None
    chat_template_sha256: str | None = None
    resource_class: str | None = Field(default=None, description="e.g. A10G, A100-40GB:2")
    parameter_count: int | None = Field(
        default=None,
        ge=0,
        description="Number of parameters derived from checkpoint tensor shapes.",
    )
    head_dim: int | None = Field(
        default=None,
        description="Attention width per head. Below 16 no serving kernel will "
        "run the model, however well it loads in transformers.",
    )
    processor: ProcessorProfile | None = None  # SEAM 3


class Capabilities(BaseModel):
    """SEAM 5: one object, so VLM fields are additive."""

    chat: bool = True
    completions: bool = True
    logprobs: bool = False
    tool_use: bool = False
    max_context: int | None = None
    vision: bool = False
    max_images_per_request: int | None = None
    supported_image_formats: list[str] = Field(default_factory=list)


class Environment(BaseModel):
    """Everything needed to reproduce a rating. Load-bearing for §6.3."""

    container_digest: str | None = None
    gpu: str | None = None
    gpu_count: int = 1
    driver_version: str | None = None
    python_version: str | None = None
    torch_version: str | None = None
    engine_version: str | None = None
    seed: int | None = None
    harness_version: str | None = None
    sandboxed: bool = Field(
        default=True,
        description="False when the model was reached over an external endpoint "
        "instead of loaded in our no-egress sandbox. Such a run is a smoke test, "
        "never a certification: the artifact was not scanned, the environment was "
        "not controlled, and held-out prompts would have left the box.",
    )


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

class Status(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ScanResult(BaseModel):
    scanner: str
    scanner_version: str | None = None
    status: Status
    findings: list[dict] = Field(default_factory=list)
    duration_s: float | None = None


class Finding(BaseModel):
    id: str
    severity: Severity
    summary: str
    detail: str | None = None
    evidence_ref: str | None = Field(
        default=None,
        description="Pointer into stored transcripts. Never inline held-out prompt text.",
    )
    visibility: Audience = Field(
        default=Audience.INTERNAL,
        description="Minimum audience. Findings from held-out suites stay INTERNAL; "
        "their coarse category surfaces via SuiteResult.categories instead.",
    )


class SuiteResult(BaseModel):
    """Returned by the eval harness. Owned jointly; versioned deliberately."""

    suite_id: str
    suite_version: str
    modality: list[Modality] = Field(default_factory=lambda: [Modality.TEXT])  # SEAM 1
    status: Status
    gate: bool = Field(
        default=False,
        description="Whether this result is a mandatory certification gate rather than "
        "a benchmark that contributes to the capability grade.",
    )
    diagnostic: bool = Field(
        default=False,
        description="Reported but excluded from the capability grade. Recorded on "
        "the result so grading stays a pure function of the report: re-grading a "
        "stored report must not depend on which suites happen to be installed.",
    )
    held_out: bool = Field(
        default=False,
        description="If true, redaction is strict: no metrics or findings escape, "
        "and the score is bucketed before a creator or buyer sees it.",
    )
    declined: bool = Field(
        default=False,
        description="The creator chose not to run this benchmark. Always visible, "
        "to every audience: a benchmark you can silently omit is a benchmark you "
        "can hide a bad result behind.",
    )
    display_name: str | None = Field(
        default=None, description="Human-readable name, for the report and the menu."
    )
    # -- capability-conditioned safety -------------------------------------
    # Carried on the result, not just the manifest, for the same reason `gate`
    # and `diagnostic` are: grading must be a pure function of the report, and
    # a stored report cannot depend on which suites happen to be installed
    # when it is re-read.
    domain: str | None = Field(
        default=None,
        description="Subject domain this suite measures, e.g. 'bio'. Pairs a "
        "capability probe with the elicitation set that attacks the same "
        "subject matter.",
    )
    internal: bool = Field(
        default=False,
        description="Platform-only instrument. Never priced, never offered on "
        "the menu, and rendered for no audience -- stricter than held_out, "
        "which still surfaces a coarse band. A conditioning probe is internal "
        "because a creator who could see it could sandbag it.",
    )
    role: str | None = Field(
        default=None,
        description="'probe' (measures capability, sets a threshold) or "
        "'elicitation' (attacks the domain, is judged against one).",
    )
    conditioned_by: str | None = Field(
        default=None,
        description="On an elicitation result: the probe suite_id whose score "
        "sets this suite's pass threshold. The only cross-suite dependency in "
        "grading, kept declarative so resolution stays data, not logic.",
    )
    chance_floor: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Score obtainable by guessing, e.g. 0.25 for four-way "
        "multiple choice. Capability bands are defined on the chance-corrected "
        "scale so they mean the same thing across instruments.",
    )
    threshold_required: float | None = Field(
        default=None,
        description="The rate this suite had to clear, resolved from the "
        "paired probe. Recorded rather than recomputed: re-grading a stored "
        "report must not change its answer when the band table changes.",
    )
    threshold_basis: str | None = Field(
        default=None,
        description="Which probe and band produced `threshold_required`, so a "
        "verdict can be audited without re-running anything.",
    )
    effective_n: int | None = Field(
        default=None,
        description="Sample size after correcting for correlated items. An "
        "expanded set of N items built from K behaviours is not N independent "
        "observations, and a confidence bound over the raw count is narrower "
        "than the truth. Absent means the items were independent.",
    )
    baseline: bool = Field(
        default=False,
        description="A general harmful-request comparator. Conditioned gates "
        "are judged against the gap from this rather than against an absolute "
        "rate, so a domain-matched set being harder than a plain one does not "
        "read as the model being unsafe.",
    )
    utility: bool = Field(
        default=False,
        description="A benign-utility control: how much ordinary work the "
        "model still does. Any refusal bar can be satisfied by refusing "
        "everything, so this is the floor that closes that path.",
    )
    judge_id: str | None = Field(
        default=None,
        description="Which grader produced this result, when one was involved. "
        "Reproducibility, and honesty: a run scored by the fallback heuristic "
        "must be identifiable rather than indistinguishable from one scored by "
        "the real guard model.",
    )
    conditioned_verdict: str | None = Field(
        default=None,
        description="'pass', 'fail', or 'not_required' when capability was too "
        "low for the domain to be gated at all.",
    )
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    baseline_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Same suite run against the declared base model, when one "
        "could be verified. Absent means no comparison was possible, never "
        "that the base scored zero.",
    )
    delta: float | None = Field(
        default=None,
        description="score - baseline_score. Negative means this fine-tune is "
        "worse than the model it was derived from.",
    )
    score_band: str | None = Field(
        default=None,
        description="Coarse bucket of `score`. Populated during redaction so that "
        "repeated submissions cannot binary-search an exact threshold.",
    )
    metrics: dict[str, float] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    categories: list[str] = Field(
        default_factory=list,
        description="Coarse failing dimensions, e.g. ['harmful_content_refusal']. "
        "The only failure detail a creator receives from a held-out suite.",
    )
    remediation: str | None = Field(
        default=None,
        description="Pointer to the public practice suite for this dimension. "
        "Creators iterate against that, never against the held-out set.",
    )
    n_items: int | None = None
    duration_s: float | None = None
    error: str | None = None


# --------------------------------------------------------------------------
# Cost + rating
# --------------------------------------------------------------------------

class Cost(BaseModel):
    """The MVP's most important instrument: cost-per-certification."""

    gpu_seconds: float = 0.0
    cpu_seconds: float = 0.0
    bytes_transferred: int = 0
    usd_estimate: float | None = None
    human_minutes: float | None = Field(
        default=None, description="Manual intervention required. Logged by hand."
    )


class Rating(BaseModel):
    """We rate and measure; we do not warrant. See design doc §6.3.

    Two separate questions, deliberately not collapsed into one letter:

    `certified` is the gate -- did every mandatory safety check pass. It is
    binary and fail-closed, and only a certified model may be listed.

    `grade` is capability, and it says nothing about safety. It is "unrated"
    when no capability benchmark was purchased, because "we did not measure
    this" and "this scored badly" are different facts and a buyer reading a
    letter cannot tell them apart.
    """

    certified: bool = Field(
        default=False,
        description="Every mandatory safety gate passed. Required for listing.",
    )
    grade: Literal["A", "B", "C", "D", "F", "unrated"] = "unrated"
    rationale: str | None = None
    as_tested_at: datetime
    methodology_version: str = REPORT_VERSION


class Signature(BaseModel):
    algorithm: str
    key_id: str
    value: str


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------

class CertificationReport(BaseModel):
    report_version: str = REPORT_VERSION
    report_id: str
    created_at: datetime
    status: Status

    subject: Subject
    serving_profile: ServingProfile = Field(default_factory=ServingProfile)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    environment: Environment = Field(default_factory=Environment)

    scans: list[ScanResult] = Field(default_factory=list)
    suite_results: list[SuiteResult] = Field(default_factory=list)

    cost: Cost | None = Field(
        default=None, description="INTERNAL only. Stripped for buyer and creator views."
    )
    rating: Rating
    signature: Signature | None = None


if __name__ == "__main__":
    import json
    import pathlib

    out = pathlib.Path(__file__).resolve().parents[2] / "schemas" / "report.schema.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(CertificationReport.model_json_schema(), indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out}")
