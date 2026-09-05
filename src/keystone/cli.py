"""keystone — certification pipeline CLI.

  certify   one model, sandboxed, on GPU. The real thing.
  batch     many models, measuring yield. Failure is data, not a stop.
  smoke     suites against any endpoint. No Modal, no GPU, no spend.
"""

from __future__ import annotations

import json
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from keystone.pipeline import FailureKind, Outcome, certify_one, grade
from keystone.schema import (
    Capabilities,
    CertificationReport,
    Environment,
    Modality,
    Rating,
    Source,
    SourceKind,
    Status,
    Subject,
)

app = typer.Typer(add_completion=False, help="Keystone model certification pipeline.")
console = Console()


# --------------------------------------------------------------------------
# certify
# --------------------------------------------------------------------------

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

    console.rule(f"[bold]certifying[/] {ref}")
    with modal_app.app.run():
        outcome = certify_one(
            ref,
            revision=revision,
            only=list(suite) if suite else None,
            seed=seed,
            max_context=max_context,
            on_step=lambda m: console.print(f"[cyan]·[/] {m}"),
        )

    if outcome.report is None:
        console.print(f"[red]{outcome.failure.value}[/] {outcome.detail}")
        raise typer.Exit(code=2)

    path = _write(outcome.report, out_dir)
    _render(outcome.report, outcome.wall_s)
    console.print(f"\nreport: [bold]{path}[/]")


# --------------------------------------------------------------------------
# batch — the yield measurement
# --------------------------------------------------------------------------

@app.command()
def batch(
    models: Path = typer.Argument(..., help="File with one HF repo id per line (# comments ok)."),
    suite: list[str] = typer.Option(None, "--suite"),
    seed: int = typer.Option(0),
    out_dir: Path = typer.Option(Path("out/batch")),
) -> None:
    """Certify many models and report yield.

    The number this exists to produce: what fraction of arbitrary models go from
    reference to rating with nobody touching them. If that number is low, this
    is a consulting business wearing a platform costume.

    A model that fails does not stop the batch -- failures are the measurement.
    """
    from keystone.runner import modal_app

    refs = [
        line.strip()
        for line in models.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not refs:
        console.print("[red]no model refs found[/]")
        raise typer.Exit(code=2)

    console.rule(f"[bold]batch[/] {len(refs)} models")
    outcomes: list[Outcome] = []
    started = time.monotonic()

    with modal_app.app.run():
        for i, ref in enumerate(refs, 1):
            console.print(f"\n[bold]({i}/{len(refs)})[/] {ref}")
            outcome = certify_one(
                ref,
                only=list(suite) if suite else None,
                seed=seed,
                on_step=lambda m: console.print(f"  [cyan]·[/] {m}"),
            )
            outcomes.append(outcome)
            if outcome.report is not None:
                _write(outcome.report, out_dir)
                console.print(
                    f"  [green]{outcome.report.rating.grade}[/] "
                    f"in {outcome.wall_s:.0f}s"
                )
            else:
                console.print(f"  [red]{outcome.failure.value}[/] {(outcome.detail or '')[:110]}")

    summary = _summarise(outcomes, time.monotonic() - started)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "yield.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _render_yield(summary)
    console.print(f"\nyield report: [bold]{path}[/]")


def _summarise(outcomes: list[Outcome], wall_s: float) -> dict:
    certified = [o for o in outcomes if o.ok]
    clean = [o for o in outcomes if o.served_clean]
    failures: dict[str, int] = {}
    for o in outcomes:
        if o.failure is not None:
            failures[o.failure.value] = failures.get(o.failure.value, 0) + 1

    gpu = [o.report.cost.gpu_seconds for o in certified if o.report.cost]
    usd = [o.report.cost.usd_estimate or 0.0 for o in certified if o.report.cost]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_attempted": len(outcomes),
        "n_certified": len(certified),
        "n_served_clean": len(clean),
        # THE number.
        "clean_rate": round(len(clean) / len(outcomes), 3) if outcomes else 0.0,
        "failures": failures,
        "total_usd": round(sum(usd), 4),
        "median_gpu_s": round(statistics.median(gpu), 1) if gpu else None,
        "median_wall_s": round(statistics.median([o.wall_s for o in outcomes]), 1),
        "batch_wall_s": round(wall_s, 1),
        "models": [
            {
                "ref": o.ref,
                "ok": o.ok,
                "grade": o.report.rating.grade if o.report else None,
                "failure": o.failure.value if o.failure else None,
                "detail": o.detail,
                "wall_s": round(o.wall_s, 1),
                "gpu_s": o.report.cost.gpu_seconds if o.report and o.report.cost else None,
                "usd": o.report.cost.usd_estimate if o.report and o.report.cost else None,
                "gib": round(o.report.subject.total_bytes / 1024**3, 2) if o.report else None,
                "arch": o.report.serving_profile.architecture if o.report else None,
            }
            for o in outcomes
        ],
    }


def _render_yield(s: dict) -> None:
    table = Table(title="yield")
    for col in ("model", "arch", "GiB", "result", "wall", "gpu", "$"):
        table.add_column(col)
    for m in s["models"]:
        result = f"[green]{m['grade']}[/]" if m["ok"] else f"[red]{m['failure']}[/]"
        table.add_row(
            m["ref"],
            m["arch"] or "-",
            f"{m['gib']:.2f}" if m["gib"] is not None else "-",
            result,
            f"{m['wall_s']:.0f}s",
            f"{m['gpu_s']:.0f}s" if m["gpu_s"] is not None else "-",
            f"{m['usd']:.3f}" if m["usd"] is not None else "-",
        )
    console.print(table)
    console.print(
        f"[bold]clean rate[/] {s['clean_rate']:.0%} "
        f"({s['n_served_clean']}/{s['n_attempted']} needed no intervention)   "
        f"[bold]certified[/] {s['n_certified']}   "
        f"[bold]spend[/] ${s['total_usd']:.2f}"
    )
    if s["failures"]:
        console.print("[bold]failures[/] " + ", ".join(f"{k}={v}" for k, v in s["failures"].items()))


# --------------------------------------------------------------------------
# smoke — free path
# --------------------------------------------------------------------------

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

    letter, rationale = grade([], results, sandboxed=False)
    report = CertificationReport(
        report_id=str(uuid.uuid4()),
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
        rating=Rating(grade=letter, rationale=rationale, as_tested_at=datetime.now(timezone.utc)),
    )

    path = _write(report, out_dir)
    _render(report, time.monotonic() - wall_start)
    console.print("[yellow]not a certification[/] — unsandboxed, unscanned, unrated")
    console.print(f"report: [bold]{path}[/]")


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False),
) -> None:
    """Run the API and the placeholder UI. Dev wiring: SQLite, local store, mock payments."""
    import uvicorn

    console.print(f"[bold]http://{host}:{port}[/]  (UI at /, docs at /docs)")
    uvicorn.run("keystone.api:dev_app", factory=True, host=host, port=port, reload=reload)


@app.command()
def worker(
    once: bool = typer.Option(False, "--once", help="Drain the queue and exit."),
    interval: int = typer.Option(15, help="Seconds between polls."),
    limit: int = typer.Option(5, help="Max listings per pass."),
    db: str = typer.Option("sqlite:///keystone.db"),
) -> None:
    """Certify queued listings. Separate process: no request thread waits on a GPU."""
    from keystone.db import Store
    from keystone.runner import modal_app
    from keystone.worker import process_pending

    store = Store(db)
    store.create_all()
    console.print("[bold]worker[/] draining pending_certification" + ("" if once else f" every {interval}s"))

    while True:
        with modal_app.app.run():
            done = process_pending(
                store, limit=limit, on_step=lambda m: console.print(f"  [cyan]·[/] {m}")
            )
        if not done:
            console.print("  [dim]nothing queued[/]")
        if once:
            return
        time.sleep(interval)


@app.command()
def seed(db: str = typer.Option("sqlite:///keystone.db")) -> None:
    """Populate the dev database so the placeholder UI has something to show.

    Uses fabricated reports -- no GPU, no spend. The signatures are real.
    """
    import uuid as _uuid
    from datetime import timedelta

    from keystone.db import Store
    from keystone.listing import Attempt, ListingState
    from keystone.pipeline import Outcome
    from keystone.schema import Cost, Environment, Rating, ScanResult, SuiteResult
    from keystone.signing import Ed25519Signer
    from keystone.worker import publish_certified, record_outcome

    store = Store(db)
    store.create_all()
    signer = Ed25519Signer.from_env() or Ed25519Signer.generate()
    now = datetime.now(timezone.utc)

    def report(grade: str, digest: str, held: float) -> CertificationReport:
        return CertificationReport(
            report_id=f"rep_{_uuid.uuid4().hex[:12]}",
            created_at=now,
            status=Status.PASS if grade != "F" else Status.FAIL,
            subject=Subject(
                source=Source(kind=SourceKind.UPLOAD, ref=f"rcpt_{digest[:8]}"),
                artifact_digest=digest,
                files=[],
                total_bytes=1_400_000_000,
            ),
            environment=Environment(sandboxed=True, seed=0, gpu="NVIDIA A10",
                                    engine_version="0.28.0", python_version="3.12.10"),
            scans=[ScanResult(scanner="picklescan", status=Status.PASS),
                   ScanResult(scanner="format_hygiene", status=Status.PASS)],
            suite_results=[
                SuiteResult(suite_id="heldout_harm", suite_version="1.2.0",
                            status=Status.PASS if held >= 0.75 else Status.FAIL,
                            held_out=True, score=held, metrics={"refusal_rate": held},
                            n_items=200,
                            categories=[] if held >= 0.75 else ["harmful_content_refusal"],
                            remediation=None if held >= 0.75 else "public/harm_practice_v1"),
                SuiteResult(suite_id="public_capability", suite_version="0.4.0",
                            status=Status.PASS, held_out=False, score=0.91,
                            metrics={"accuracy": 0.91}, n_items=50),
            ],
            cost=Cost(gpu_seconds=142.0, cpu_seconds=9.0,
                      bytes_transferred=1_400_000_000, usd_estimate=0.0435),
            rating=Rating(grade=grade, as_tested_at=now,
                          rationale=f"Mean suite score across 2 suite(s); grade {grade}."),
        )

    # price in USDC minor units (6 decimals); 0 is a real price
    fixtures = [
        ("Legalese-7B (contract QA)", "1" * 64, "A", 0.94, True, 120_000_000),
        ("MedNote-3B (clinical summaries)", "2" * 64, "B", 0.81, True, 45_000_000),
        ("Tokenizer-Bench-0.5B (open)", "4" * 64, "A", 0.92, True, 0),
        ("Sentinel-1B (log triage)", "3" * 64, "D", 0.62, False, 30_000_000),
    ]

    with store.session() as s:
        store.upsert_user(s, "u_creator", "creator@example.com")
        for _, digest, *_ in fixtures:
            store.put_artifact(s, digest, [], 1_400_000_000)
        s.commit()

    for title, digest, grade, held, go_live, price in fixtures:
        listing_id = f"lst_{_uuid.uuid4().hex[:16]}"
        with store.session() as s:
            row = store.create_listing(s, listing_id, "u_creator", digest, title)
            row.price_minor = price
            s.commit()
        record_outcome(store, listing_id, Outcome(digest, report=report(grade, digest, held)),
                       signer=signer, now=now)
        if go_live:
            publish_certified(store, listing_id)
        tag = "free" if price == 0 else f"{price / 1e6:.0f} USDC"
        console.print(
            f"  {title}  [bold]{grade}[/]  "
            f"{'listed' if go_live else 'rejected'}  {tag}"
        )

    # A listing that looks like eval-set probing, for the admin queue.
    probe_id = f"lst_{_uuid.uuid4().hex[:16]}"
    with store.session() as s:
        store.put_artifact(s, "9" * 64, [], 900_000_000)
        store.create_listing(s, probe_id, "u_creator", "9" * 64, "Nudged-2B (attempt 4)")
        s.commit()
    for i, score in enumerate((0.700, 0.720, 0.735)):
        record_outcome(store, probe_id, Outcome("9" * 64, report=report("D", "9" * 64, score)),
                       signer=signer, now=now + timedelta(days=i))
    console.print("  Nudged-2B  [yellow]flagged for review[/] (monotonic score creep)")
    console.print(f"\nseeded {db}")


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


def _write(report: CertificationReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report.report_id}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def _render(report: CertificationReport, wall_s: float) -> None:
    table = Table(title=f"{report.subject.source.ref}  —  grade {report.rating.grade}")
    for col, justify in (("suite", "left"), ("status", "left"), ("score", "right"), ("detail", "left")):
        table.add_column(col, justify=justify)
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


if __name__ == "__main__":
    app()
