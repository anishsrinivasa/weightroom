"""keystone certify <model-ref>

The week-one vertical slice: fetch, hash, scan, serve, evaluate, report.
No API, no accounts, no catalog. The point is to produce a signed-shaped report
and, more importantly, the three numbers that decide whether this business
prices: cost, wall-clock, and human minutes per certification.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from keystone.schema import (
    REPORT_VERSION,
    Capabilities,
    Source,
    SourceKind,
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

app = typer.Typer(add_completion=False, help="Keystone model certification pipeline.")
console = Console()

# Rough USD/GPU-second. VERIFY against current Modal pricing before quoting a
# customer -- these move, and cost-per-certification is the metric the whole
# MVP exists to measure.
_GPU_USD_PER_S: dict[str, float] = {
    "A10G": 0.000306,
    "A100-40GB": 0.000583,
    "A100-80GB": 0.000694,
    "A100-80GB:2": 0.001389,
    "A100-80GB:4": 0.002778,
}


def _grade(
    scans: list[ScanResult],
    suites: list[SuiteResult],
    environment_is_sandboxed: bool = True,
) -> tuple[str, str]:
    """Placeholder rating logic. The real rubric is the harness side's call.

    We rate and measure; we do not warrant (design doc section 6.3).
    """
    if not environment_is_sandboxed:
        return "unrated", (
            "Run was not sandboxed: the artifact was not scanned and the "
            "environment was not controlled. Smoke test only."
        )
    if any(s.status is Status.FAIL for s in scans):
        return "F", "Security scan failed; artifact is not safe to load."
    if any(s.status is Status.ERROR for s in suites):
        return "unrated", "One or more suites errored; no rating issued."

    scored = [s.score for s in suites if s.score is not None]
    if not scored:
        return "unrated", "No scored suites ran."
    mean = sum(scored) / len(scored)
    cutoffs = [(0.9, "A"), (0.75, "B"), (0.6, "C"), (0.4, "D")]
    grade = next((g for c, g in cutoffs if mean >= c), "F")
    warn = any(s.status is Status.WARN for s in scans + suites)
    return grade, f"Mean suite score {mean:.2f} across {len(scored)} suite(s)." + (
        " Warnings present." if warn else ""
    )


@app.command()
def certify(
    ref: str = typer.Argument(..., help="HuggingFace repo id, e.g. Qwen/Qwen2.5-0.5B-Instruct"),
    revision: str | None = typer.Option(None, help="Pin a commit sha."),
    suite: list[str] = typer.Option(None, "--suite", help="Only run these suite ids."),
    seed: int = typer.Option(0, help="Sampling seed, recorded in the report."),
    out_dir: Path = typer.Option(Path("out"), help="Where to write the report."),
    max_context: int | None = typer.Option(None, help="Override served context length."),
) -> None:
    from keystone.runner import modal_app

    wall_start = time.monotonic()
    report_id = str(uuid.uuid4())
    console.rule(f"[bold]certifying[/] {ref}")

    with modal_app.app.run():
        # ---- fetch (network on, no untrusted execution) -------------------
        console.print("[cyan]fetch[/]  resolving and hashing artifact...")
        fetched = modal_app.fetch.remote(ref, revision)
        subject = Subject.model_validate(fetched["subject"])
        profile = ServingProfile.model_validate(fetched["serving_profile"])
        caps = Capabilities.model_validate(fetched["capabilities"])
        console.print(
            f"        {len(subject.files)} files, {subject.total_bytes / 1024**3:.2f} GiB, "
            f"digest {subject.artifact_digest[:12]}"
            + ("  [green](cache hit)[/]" if fetched["cached"] else "")
        )

        # SEAM 1: the MVP is text-only and says so rather than half-working.
        if Modality.IMAGE in subject.modality:
            console.print(
                "[yellow]refused[/] vision-language model detected "
                f"(architecture={profile.architecture}). The LLM MVP is text-only; "
                "VLM support lands as an additive layer."
            )
            raise typer.Exit(code=2)

        # ---- scan (network off, before load) ------------------------------
        console.print("[cyan]scan[/]   inspecting serialization formats...")
        scans = [ScanResult.model_validate(s) for s in modal_app.scan.remote(fetched["cache_key"])]
        for s in scans:
            colour = {"pass": "green", "warn": "yellow"}.get(s.status.value, "red")
            console.print(f"        {s.scanner}: [{colour}]{s.status.value}[/]")

        if any(s.status is Status.FAIL for s in scans):
            console.print("[red]halting[/] scan failure -- weights will not be loaded.")
            suite_results: list[SuiteResult] = []
            environment = Environment()
            gpu_seconds = 0.0
        else:
            # ---- evaluate (network off, GPU, weights loaded) --------------
            gpu = profile.resource_class or "A10G"
            tp = int(gpu.split(":")[1]) if ":" in gpu else 1
            console.print(f"[cyan]eval[/]   serving on {gpu} and running suites...")
            evaluated = modal_app.evaluate.with_options(gpu=gpu).remote(
                fetched["cache_key"],
                caps.model_dump(mode="json"),
                [m.value for m in subject.modality],
                max_context or profile.max_context,
                tp,
                list(suite) if suite else None,
                seed,
            )
            suite_results = [SuiteResult.model_validate(r) for r in evaluated["suite_results"]]
            environment = Environment.model_validate(evaluated["environment"])
            gpu_seconds = float(evaluated["gpu_seconds"])
            profile.engine_version = environment.engine_version

    grade, rationale = _grade(scans, suite_results)
    usd_rate = _GPU_USD_PER_S.get(profile.resource_class or "", None)

    report = CertificationReport(
        report_version=REPORT_VERSION,
        report_id=report_id,
        created_at=datetime.now(timezone.utc),
        status=Status.FAIL if grade == "F" else Status.PASS,
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
        rating=Rating(grade=grade, rationale=rationale, as_tested_at=datetime.now(timezone.utc)),
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report_id}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    _render(report, time.monotonic() - wall_start)
    console.print(f"\nreport: [bold]{path}[/]")


def _render(report: CertificationReport, wall_s: float) -> None:
    table = Table(title=f"{report.subject.source.ref}  —  grade {report.rating.grade}")
    table.add_column("suite")
    table.add_column("status")
    table.add_column("score", justify="right")
    table.add_column("detail")
    for r in report.suite_results:
        colour = {"pass": "green", "warn": "yellow", "skipped": "dim"}.get(r.status.value, "red")
        table.add_row(
            r.suite_id,
            f"[{colour}]{r.status.value}[/]",
            f"{r.score:.2f}" if r.score is not None else "-",
            r.error or ", ".join(f"{k}={v:.2f}" for k, v in r.metrics.items()),
        )
    console.print(table)

    line = f"[bold]wall[/] {wall_s:.0f}s"
    # Absent for smoke runs, and for any report that has been redacted --
    # cost is INTERNAL only.
    if report.cost is not None:
        cost = report.cost
        line += (
            f"   [bold]gpu[/] {cost.gpu_seconds:.0f}s"
            f"   [bold]transferred[/] {cost.bytes_transferred / 1024**3:.2f} GiB"
            "   [bold]est[/] "
            + (f"${cost.usd_estimate:.2f}" if cost.usd_estimate is not None else "n/a")
        )
    console.print(line)


@app.command()
def smoke(
    endpoint: str = typer.Option(
        "http://localhost:8080/v1", help="Any OpenAI-compatible base URL."
    ),
    model: str = typer.Option("local", help="Model name the endpoint serves."),
    suite: list[str] = typer.Option(None, "--suite", help="Only run these suite ids."),
    seed: int = typer.Option(0),
    out_dir: Path = typer.Option(Path("out")),
) -> None:
    """Run suites against an existing endpoint. No Modal, no GPU, no spend.

    This is NOT a certification. Nothing is fetched, hashed, or scanned, and the
    model runs outside our sandbox. The report is stamped `sandboxed=False` and
    forced to `unrated` so it can never be mistaken for the real thing.

    Point it at llama.cpp's `llama-server`, LM Studio, Ollama, or a hosted API.
    """
    from keystone.client import OpenAIServerClient
    from keystone.registry import SUITES_ROOT
    from keystone.run import held_out_suites, run_suites

    # Held-out prompts must never leave our sandbox. An external endpoint sees
    # every prompt sent to it, which would burn the eval set outright.
    blocked = held_out_suites(SUITES_ROOT, list(suite) if suite else None)
    if blocked:
        console.print(
            "[red]refused[/] held-out suites cannot run against an external "
            f"endpoint: {', '.join(blocked)}\n"
            "        Their prompts are the moat; sending them out would burn it."
        )
        raise typer.Exit(code=2)

    wall_start = time.monotonic()
    report_id = str(uuid.uuid4())
    console.rule(f"[bold]smoke[/] {model} @ {endpoint}")

    caps = Capabilities()
    results = run_suites(
        OpenAIServerClient(model, base_url=endpoint, seed=seed),
        model_name=model,
        capabilities=caps,
        modality=[Modality.TEXT],
        suites_root=SUITES_ROOT,
        scratch_dir=Path(".keystone-scratch"),
        only=list(suite) if suite else None,
        seed=seed,
    )

    grade, rationale = _grade([], results, environment_is_sandboxed=False)
    report = CertificationReport(
        report_id=report_id,
        created_at=datetime.now(timezone.utc),
        status=Status.PASS,
        subject=Subject(
            source=Source(kind=SourceKind.UPLOAD, ref=f"endpoint:{endpoint}"),
            artifact_digest="0" * 64,  # nothing was hashed; there is no artifact
            files=[],
            total_bytes=0,
        ),
        capabilities=caps,
        environment=Environment(seed=seed, sandboxed=False),
        suite_results=results,
        rating=Rating(grade=grade, rationale=rationale, as_tested_at=datetime.now(timezone.utc)),
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report_id}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    _render(report, time.monotonic() - wall_start)
    console.print("[yellow]not a certification[/] — unsandboxed, unscanned, unrated")
    console.print(f"report: [bold]{path}[/]")


@app.command()
def suites() -> None:
    """List discoverable suites and what they require."""
    from keystone.registry import discover

    table = Table(title="suites")
    for col in ("id", "version", "modality", "requires", "held-out"):
        table.add_column(col)
    for s in discover():
        m = s.manifest
        table.add_row(
            m.id,
            m.version,
            ",".join(x.value for x in m.modality),
            ",".join(m.required_capabilities) or "-",
            "yes" if m.held_out else "no",
        )
    console.print(table)


@app.command()
def schema(out: Path = typer.Option(Path("schemas/report.schema.json"))) -> None:
    """Regenerate the shared JSON Schema from the pydantic source of truth."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(CertificationReport.model_json_schema(), indent=2) + "\n", encoding="utf-8"
    )
    console.print(f"wrote {out}")


if __name__ == "__main__":
    app()
