"""One certification, start to finish, as a callable.

Extracted from the CLI so a batch run can drive it. The important property is
that failure is a *return value*, not an exception: a batch that stops on the
first model that will not serve tells you nothing, and the whole point of
running a batch is to measure how often that happens.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from keystone.schema import (
    REPORT_VERSION,
    Capabilities,
    CertificationReport,
    Cost,
    Environment,
    Modality,
    Rating,
    ScanResult,
    ServingProfile,
    Status,
    Subject,
    SuiteResult,
)

# Rough USD/GPU-second. VERIFY against current Modal pricing before quoting a
# customer -- these move, and cost-per-certification is the metric the whole
# MVP exists to measure.
GPU_USD_PER_S: dict[str, float] = {
    "A10G": 0.000306,
    "A100-40GB": 0.000583,
    "A100-80GB": 0.000694,
    "A100-80GB:2": 0.001389,
    "A100-80GB:4": 0.002778,
}


class FailureKind(str, Enum):
    """Taxonomy for why a model did not certify.

    The distribution across these is the answer to whether this is a platform
    or a consulting business.
    """

    DOWNLOAD = "download"                      # could not fetch the artifact
    UNSUPPORTED_MODALITY = "unsupported_modality"  # VLM, refused by design
    SCAN_FAIL = "scan_fail"                    # working as intended
    LICENSE_FAIL = "license_fail"              # chain forbids what is claimed
    SERVE_FAIL = "serve_fail"                  # would not load or start
    OOM = "oom"                                # too big for the chosen class
    TIMEOUT = "timeout"
    SUITE_ERROR = "suite_error"
    UNKNOWN = "unknown"


def classify(exc: BaseException) -> FailureKind:
    text = f"{type(exc).__name__}: {exc}".lower()
    if "out of memory" in text or "oom" in text or "kv cache" in text:
        return FailureKind.OOM
    if "timeout" in text or "timed out" in text:
        return FailureKind.TIMEOUT
    if "vllm exited" in text or "engine core" in text or "did not become ready" in text:
        return FailureKind.SERVE_FAIL
    if "repository not found" in text or "gated" in text or "401" in text or "403" in text:
        return FailureKind.DOWNLOAD
    return FailureKind.UNKNOWN


@dataclass
class Outcome:
    """What happened to one model."""

    ref: str
    report: CertificationReport | None = None
    failure: FailureKind | None = None
    detail: str | None = None
    wall_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.report is not None

    @property
    def served_clean(self) -> bool:
        """Did this model go from reference to rating with no human touching it?

        The metric that matters. A scan failure counts as clean -- the pipeline
        did its job. A serving failure does not.
        """
        if self.report is None:
            return self.failure in (
                FailureKind.SCAN_FAIL,
                FailureKind.UNSUPPORTED_MODALITY,
            )
        return not any(r.status is Status.ERROR for r in self.report.suite_results)


def grade(
    scans: list[ScanResult],
    suites: list[SuiteResult],
    sandboxed: bool = True,
    license_chain_ok: bool | None = None,
) -> tuple[str, str]:
    """Placeholder rating logic. The real rubric is the harness side's call.

    We rate and measure; we do not warrant (design doc section 6.3).
    """
    if not sandboxed:
        return "unrated", (
            "Run was not sandboxed: the artifact was not scanned and the "
            "environment was not controlled. Smoke test only."
        )
    if any(s.status is Status.FAIL for s in scans):
        return "F", "Security scan failed; artifact is not safe to load."
    if license_chain_ok is False:
        return "F", "Licence chain forbids the declared terms; not distributable as stated."
    if any(s.status is Status.ERROR for s in suites):
        return "unrated", "One or more suites errored; no rating issued."

    scored = [s.score for s in suites if s.score is not None]
    if not scored:
        return "unrated", "No scored suites ran."
    mean = sum(scored) / len(scored)
    cutoffs = [(0.9, "A"), (0.75, "B"), (0.6, "C"), (0.4, "D")]
    letter = next((g for c, g in cutoffs if mean >= c), "F")
    warn = any(s.status is Status.WARN for s in scans + suites)
    return letter, f"Mean suite score {mean:.2f} across {len(scored)} suite(s)." + (
        " Warnings present." if warn else ""
    )


def certify_one(
    ref: str,
    *,
    revision: str | None = None,
    only: list[str] | None = None,
    seed: int = 0,
    max_context: int | None = None,
    on_step=lambda msg: None,
) -> Outcome:
    """Run the full pipeline for one model. Never raises; returns an Outcome."""
    from keystone.runner import modal_app

    started = time.monotonic()

    try:
        on_step("fetch")
        fetched = modal_app.fetch.remote(ref, revision)
        subject = Subject.model_validate(fetched["subject"])
        profile = ServingProfile.model_validate(fetched["serving_profile"])
        caps = Capabilities.model_validate(fetched["capabilities"])
    except Exception as exc:
        return Outcome(ref, failure=classify(exc), detail=repr(exc)[:400],
                       wall_s=time.monotonic() - started)

    on_step(
        f"{len(subject.files)} files, {subject.total_bytes / 1024**3:.2f} GiB"
        + ("  (cache hit)" if fetched["cached"] else "")
    )

    # SEAM 1: the MVP is text-only and says so rather than half-working.
    if Modality.IMAGE in subject.modality:
        return Outcome(
            ref,
            failure=FailureKind.UNSUPPORTED_MODALITY,
            detail=f"vision-language model ({profile.architecture}); LLM MVP is text-only",
            wall_s=time.monotonic() - started,
        )

    try:
        on_step("scan")
        scans = [ScanResult.model_validate(s) for s in modal_app.scan.remote(fetched["cache_key"])]
    except Exception as exc:
        return Outcome(ref, failure=classify(exc), detail=repr(exc)[:400],
                       wall_s=time.monotonic() - started)

    suite_results: list[SuiteResult] = []
    environment = Environment()
    gpu_seconds = 0.0
    failure: FailureKind | None = None
    detail: str | None = None

    if any(s.status is Status.FAIL for s in scans):
        # Working as intended: never load weights that failed a scan.
        failure = FailureKind.SCAN_FAIL
        detail = "scan failure; weights not loaded"
    elif subject.license.chain_ok is False:
        # A model that cannot legally be distributed will never be listed, so
        # spending GPU minutes evaluating it is pure waste.
        failure = FailureKind.LICENSE_FAIL
        detail = "; ".join(subject.license.notes[:3]) or "licence chain check failed"
    else:
        gpu = profile.resource_class or "A10G"
        tp = int(gpu.split(":")[1]) if ":" in gpu else 1
        on_step(f"eval on {gpu}")
        try:
            evaluated = modal_app.evaluate.with_options(gpu=gpu).remote(
                fetched["cache_key"],
                caps.model_dump(mode="json"),
                [m.value for m in subject.modality],
                max_context or profile.max_context,
                tp,
                only,
                seed,
            )
            suite_results = [SuiteResult.model_validate(r) for r in evaluated["suite_results"]]
            environment = Environment.model_validate(evaluated["environment"])
            gpu_seconds = float(evaluated["gpu_seconds"])
            profile.engine_version = environment.engine_version
        except Exception as exc:
            return Outcome(ref, failure=classify(exc), detail=repr(exc)[:400],
                           wall_s=time.monotonic() - started)

    letter, rationale = grade(
        scans, suite_results, license_chain_ok=subject.license.chain_ok
    )
    usd_rate = GPU_USD_PER_S.get(profile.resource_class or "")

    report = CertificationReport(
        report_version=REPORT_VERSION,
        report_id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc),
        status=Status.FAIL if letter == "F" else Status.PASS,
        subject=subject,
        serving_profile=profile,
        capabilities=caps,
        environment=environment,
        scans=scans,
        suite_results=suite_results,
        cost=Cost(
            gpu_seconds=gpu_seconds,
            cpu_seconds=float(fetched["cpu_seconds"]),
            bytes_transferred=int(fetched["bytes_transferred"]),
            usd_estimate=round(gpu_seconds * usd_rate, 4) if usd_rate else None,
        ),
        rating=Rating(grade=letter, rationale=rationale, as_tested_at=datetime.now(timezone.utc)),
    )
    return Outcome(ref, report=report, failure=failure, detail=detail,
                   wall_s=time.monotonic() - started)
