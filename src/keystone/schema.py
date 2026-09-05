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

REPORT_VERSION = "0.1.0"


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


class SuiteResult(BaseModel):
    """Returned by the eval harness. Owned jointly; versioned deliberately."""

    suite_id: str
    suite_version: str
    modality: list[Modality] = Field(default_factory=lambda: [Modality.TEXT])  # SEAM 1
    status: Status
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    metrics: dict[str, float] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
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
    """We rate and measure; we do not warrant. See design doc §6.3."""

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

    cost: Cost = Field(default_factory=Cost)
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
