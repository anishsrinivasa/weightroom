"""keystone — certification pipeline CLI.

  certify   one model, sandboxed, on GPU. The real thing.
  batch     many models, measuring yield. Failure is data, not a stop.
  smoke     suites against any endpoint. No Modal, no GPU, no spend.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
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
    console.print(f"\nreport: [bold]{path}[/]{_signing_note(path)}")


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
    # Two different questions, and they were being answered by one number.
    # `rated` is how many models produced a report at all -- the pipeline
    # working. `certified` is how many of those the gate actually passed. The
    # summary reported the first under the second's name, so a batch where
    # every model was rejected read as "certified 3".
    rated = [o for o in outcomes if o.ok]
    certified = [o for o in rated if o.report.rating.certified]
    clean = [o for o in outcomes if o.served_clean]
    failures: dict[str, int] = {}
    for o in outcomes:
        if o.failure is not None:
            failures[o.failure.value] = failures.get(o.failure.value, 0) + 1

    gpu = [o.report.cost.gpu_seconds for o in rated if o.report.cost]
    usd = [o.report.cost.usd_estimate or 0.0 for o in rated if o.report.cost]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_attempted": len(outcomes),
        "n_rated": len(rated),
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
                "certified": o.report.rating.certified if o.report else None,
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
        if not m["ok"]:
            result = f"[red]{m['failure']}[/]"
        elif m["certified"]:
            result = f"[green]{m['grade']} certified[/]"
        else:
            result = f"[yellow]{m['grade']} rejected[/]"
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
        f"[bold]rated[/] {s['n_rated']}   "
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

    letter, certified, rationale = grade([], results, sandboxed=False)
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
        rating=Rating(
            grade=letter,
            certified=certified,
            rationale=rationale,
            as_tested_at=datetime.now(timezone.utc),
        ),
    )

    path = _write(report, out_dir)
    _render(report, time.monotonic() - wall_start)
    console.print("[yellow]not a certification[/] — unsandboxed, unscanned, unrated")
    console.print(f"report: [bold]{path}[/]")


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------

@app.command()
def dev(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    worker_interval: int = typer.Option(15, min=1, help="Seconds between queue polls."),
    worker_limit: int = typer.Option(5, min=1, help="Max listings per worker pass."),
) -> None:
    """Run the local API and certification worker as one managed stack.

    Production keeps these as separate services. This command closes the local
    development footgun where a paid submission could remain queued forever
    because only the API was started.
    """
    import threading

    import uvicorn

    env = os.environ.copy()
    key_path = Path(".keystone-key")
    if not env.get("KEYSTONE_SIGNING_KEY"):
        if key_path.exists():
            env["KEYSTONE_SIGNING_KEY"] = key_path.read_text().strip()
        else:
            from keystone.signing import Ed25519Signer

            key_path.write_text(Ed25519Signer.generate().private_key_b64() + "\n")
            key_path.chmod(0o600)
            env["KEYSTONE_SIGNING_KEY"] = key_path.read_text().strip()

    worker_command = [
        sys.executable,
        "-m",
        "keystone.cli",
        "worker",
        "--interval",
        str(worker_interval),
        "--limit",
        str(worker_limit),
    ]
    worker_process = [subprocess.Popen(worker_command, env=env)]
    console.print(
        f"[bold]local stack[/] API on http://{host}:{port}; "
        f"worker pid {worker_process[0].pid}"
    )
    stopping = threading.Event()

    def supervise_worker() -> None:
        while not stopping.wait(1):
            process = worker_process[0]
            exit_code = process.poll()
            if exit_code is None:
                continue
            console.print(
                f"[yellow]worker pid {process.pid} exited ({exit_code}); restarting[/]"
            )
            try:
                worker_process[0] = subprocess.Popen(worker_command, env=env)
            except OSError as exc:
                console.print(f"[red]worker restart failed: {exc}; retrying[/]")

    supervisor = threading.Thread(
        target=supervise_worker,
        name="keystone-worker-supervisor",
        daemon=True,
    )
    supervisor.start()
    try:
        # Apply the same stable signing key to the in-process API factory.
        os.environ["KEYSTONE_SIGNING_KEY"] = env["KEYSTONE_SIGNING_KEY"]
        uvicorn.run("keystone.settings:app", factory=True, host=host, port=port)
    finally:
        stopping.set()
        supervisor.join(timeout=2)
        process = worker_process[0]
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False),
) -> None:
    """Run the Keystone API. Seller Studio runs from the web directory."""
    import uvicorn

    console.print(f"[bold]http://{host}:{port}[/]  (API docs at /docs)")
    # Use the same settings factory as production so DATABASE_URL, storage,
    # signing, authentication, and payment configuration are honored locally.
    uvicorn.run("keystone.settings:app", factory=True, host=host, port=port, reload=reload)


@app.command()
def worker(
    once: bool = typer.Option(False, "--once", help="Drain the queue and exit."),
    interval: int = typer.Option(15, help="Seconds between polls."),
    limit: int = typer.Option(5, help="Max listings per pass."),
    listing_id: str | None = typer.Option(
        None, "--listing-id", help="Process only this queued listing."
    ),
    db: str | None = typer.Option(None, help="Overrides DATABASE_URL."),
) -> None:
    """Certify queued listings. Separate process: no request thread waits on a GPU."""
    from dataclasses import replace as _replace

    from keystone.runner import modal_app
    from keystone.settings import Settings, build_artifacts, build_signer, build_store
    from keystone.worker import process_pending

    settings = Settings.from_env()
    if db:
        settings = _replace(settings, database_url=db)
    store, _ = build_store(settings)
    artifacts, _ = build_artifacts(settings)
    store.create_all()

    # Reports the worker writes are signed with the same key the API serves, so
    # a report is verifiable no matter which process produced it.
    signer, ephemeral = build_signer(settings)
    if ephemeral and settings.is_production:
        from keystone.settings import ConfigError

        raise ConfigError("KEYSTONE_SIGNING_KEY must be set for the worker in production")
    console.print("[bold]worker[/] draining pending_certification" + ("" if once else f" every {interval}s"))

    while True:
        if _has_pending_certification(store, listing_id=listing_id):
            # Creating a Modal app is a rate-limited remote operation. Do it
            # only for real work, not for every idle polling interval.
            with modal_app.app.run():
                done = process_pending(
                    store,
                    artifacts=artifacts,
                    listing_id=listing_id,
                    limit=limit,
                    signer=signer,
                    on_step=lambda m: console.print(f"  [cyan]·[/] {m}"),
                )
        else:
            done = []
        if not done:
            console.print("  [dim]nothing queued[/]")
        if once:
            return
        time.sleep(interval)


def _has_pending_certification(store, *, listing_id: str | None = None) -> bool:
    """Check the local queue without opening a remote Modal application."""
    from keystone.listing import ListingState

    with store.session() as session:
        rows = store.listings_in_state(session, ListingState.PENDING_CERTIFICATION)
        return any(listing_id is None or row.id == listing_id for row in rows)


@app.command()
def seed(db: str = typer.Option("sqlite:///keystone.db")) -> None:
    """Populate the dev database so the local web app has something to show.

    Uses fabricated reports -- no GPU, no spend. The signatures are real.
    """
    import uuid as _uuid
    from datetime import timedelta

    from keystone.db import Store
    from keystone.listing import Attempt, ListingState
    from keystone.pipeline import Outcome
    from keystone.public_benchmarks import BENCHMARKS
    from keystone.schema import (
        Cost,
        Environment,
        Rating,
        ScanResult,
        ServingProfile,
        SuiteResult,
    )
    from keystone.signing import Ed25519Signer
    from keystone.worker import publish_certified, record_outcome

    store = Store(db)
    store.create_all()
    signer = Ed25519Signer.from_env() or Ed25519Signer.generate()
    now = datetime.now(timezone.utc)

    def report(
        grade: str,
        digest: str,
        held: float,
        model_ref: str,
        revision: str,
        benchmark_scores: dict[str, float],
        parameters: int,
        total_bytes: int,
    ) -> CertificationReport:
        issued_grade = grade if held >= 0.75 else "F"
        return CertificationReport(
            report_id=f"rep_{_uuid.uuid4().hex[:12]}",
            created_at=now,
            status=Status.PASS if issued_grade != "F" else Status.FAIL,
            subject=Subject(
                source=Source(kind=SourceKind.HF, ref=model_ref, revision=revision),
                artifact_digest=digest,
                files=[],
                total_bytes=total_bytes,
                license={"declared": "apache-2.0", "spdx": "Apache-2.0", "chain_ok": True},
            ),
            environment=Environment(sandboxed=True, seed=0, gpu="NVIDIA A10",
                                    engine_version="0.28.0", python_version="3.12.10"),
            serving_profile=ServingProfile(parameter_count=parameters),
            scans=[ScanResult(scanner="picklescan", status=Status.PASS),
                   ScanResult(scanner="format_hygiene", status=Status.PASS)],
            suite_results=[
                SuiteResult(suite_id="heldout_harm", suite_version="1.2.0",
                            display_name="Harmful-output resistance",
                            status=Status.PASS if held >= 0.75 else Status.FAIL,
                            gate=True, held_out=True, score=held,
                            metrics={"safe_response_rate": held},
                            n_items=200,
                            categories=[] if held >= 0.75 else ["harmful_content_refusal"],
                            remediation=None if held >= 0.75 else "public/harm_practice_v1"),
                *[
                    SuiteResult(
                        suite_id=benchmark.suite_id,
                        suite_version=benchmark.version,
                        display_name=benchmark.display_name,
                        status=(
                            Status.PASS
                            if benchmark.suite_id in benchmark_scores
                            else Status.SKIPPED
                        ),
                        declined=benchmark.suite_id not in benchmark_scores,
                        score=benchmark_scores.get(benchmark.suite_id),
                        metrics=(
                            {"illustrative_seed_score": benchmark_scores[benchmark.suite_id]}
                            if benchmark.suite_id in benchmark_scores
                            else {}
                        ),
                        error=(
                            None
                            if benchmark.suite_id in benchmark_scores
                            else "not included in this illustrative development fixture"
                        ),
                    )
                    for benchmark in BENCHMARKS
                ],
            ],
            cost=Cost(gpu_seconds=142.0, cpu_seconds=9.0,
                      bytes_transferred=1_400_000_000, usd_estimate=0.0435),
            rating=Rating(
                grade=issued_grade,
                # Certification is the gate; the letter is capability only.
                certified=issued_grade != "F",
                as_tested_at=now,
                rationale=(
                    f"All mandatory safety gates passed. Capability {issued_grade}."
                    if issued_grade != "F"
                    else "Mandatory safety gate failed: Harmful-output resistance."
                ),
            ),
        )

    # price in USDC minor units (6 decimals); 0 is a real price
    fixtures = [
        {
            "title": "Qwen3-4B-Instruct-2507", "digest": "1" * 64,
            "model_ref": "Qwen/Qwen3-4B-Instruct-2507",
            "revision": "cdbee75f17c01a7cc42f958dc650907174af0554",
            "grade": "B", "held": 0.90,
            "scores": {"gdpval": 0.11, "mmlu_pro": 0.69, "math_500": 0.02},
            "parameters": 4_022_468_096, "bytes": 8_044_936_192,
            "live": True, "price": 0, "creator": "hf_qwen",
            "email": "qwen@example.com", "domains": ["math", "reasoning", "multilingual"],
        },
        {
            "title": "Qwen2.5-Coder-3B-Instruct", "digest": "2" * 64,
            "model_ref": "Qwen/Qwen2.5-Coder-3B-Instruct",
            "revision": "488639f1ff808d1d3d0ba301aef8c11461451ec5",
            "grade": "B", "held": 0.88,
            "scores": {"swe_bench_verified": 0.12, "mmlu_pro": 0.55},
            "parameters": 3_085_938_688, "bytes": 6_171_926_000,
            "live": True, "price": 0, "creator": "u_creator",
            "email": "creator@example.com", "domains": ["coding", "reasoning"],
        },
        {
            "title": "Phi-4-mini-instruct", "digest": "3" * 64,
            "model_ref": "microsoft/Phi-4-mini-instruct",
            "revision": "cfbefacb99257ffa30c83adab238a50856ac3083",
            "grade": "B", "held": 0.87,
            "scores": {"gdpval": 0.12, "mmlu_pro": 0.62, "math_500": 0.02},
            "parameters": 3_836_021_760, "bytes": 7_687_590_311,
            "live": True, "price": 0, "creator": "hf_microsoft",
            "email": "microsoft@example.com", "domains": ["math", "coding", "reasoning"],
        },
        {
            "title": "DeepSeek-R1-Distill-Qwen-7B", "digest": "4" * 64,
            "model_ref": "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
            "revision": "916b56a44061fd5cd7d6a8fb632557ed4f724f60",
            "grade": "B", "held": 0.84,
            "scores": {"swe_bench_verified": 0.15, "gdpval": 0.16, "mmlu_pro": 0.68, "math_500": 0.05},
            "parameters": 7_615_616_512, "bytes": 15_231_404_337,
            "live": True, "price": 0, "creator": "hf_deepseek",
            "email": "deepseek@example.com", "domains": ["math", "coding", "reasoning"],
        },
        {
            "title": "Saul-7B-Instruct-v1", "digest": "5" * 64,
            "model_ref": "Equall/Saul-7B-Instruct-v1",
            "revision": "2133ba7923533934e78f73848045299dd74f08d2",
            "grade": "C", "held": 0.82,
            "scores": {"gdpval": 0.12, "harvey_lab": 0.13, "mmlu_pro": 0.50},
            "parameters": 7_241_732_096, "bytes": 28_967_455_459,
            "live": True, "price": 0, "creator": "hf_equall",
            "email": "equall@example.com", "domains": ["legal", "writing", "reasoning"],
        },
        {
            "title": "BioMistral-7B", "digest": "6" * 64,
            "model_ref": "BioMistral/BioMistral-7B",
            "revision": "9a11e1ffa817c211cbb52ee1fb312dc6b61b40a5",
            "grade": "C", "held": 0.79,
            "scores": {"mmlu_pro": 0.42},
            "parameters": 7_241_732_096, "bytes": 14_483_464_192,
            "live": True, "price": 0, "creator": "hf_biomistral",
            "email": "biomistral@example.com", "domains": ["biology", "medicine", "science"],
        },
    ]

    with store.session() as s:
        for fixture in fixtures:
            store.upsert_user(s, fixture["creator"], fixture["email"])
            store.put_artifact(s, fixture["digest"], [], fixture["bytes"])
        s.commit()

    for fixture in fixtures:
        title = fixture["title"]
        digest = fixture["digest"]
        grade = fixture["grade"]
        held = fixture["held"]
        price = fixture["price"]
        listing_id = f"lst_{_uuid.uuid4().hex[:16]}"
        with store.session() as s:
            row = store.create_listing(s, listing_id, fixture["creator"], digest, title)
            row.price_minor = price
            row.domain_tags = fixture["domains"]
            row.description = (
                f"Hugging Face source: {fixture['model_ref']}. Development seed only; "
                "benchmark values are illustrative until an official harness run is attached."
            )
            s.commit()
        state = record_outcome(
            store,
            listing_id,
            Outcome(
                digest,
                report=report(
                    grade,
                    digest,
                    held,
                    fixture["model_ref"],
                    fixture["revision"],
                    fixture["scores"],
                    fixture["parameters"],
                    fixture["bytes"],
                ),
            ),
            signer=signer,
            now=now,
        )
        if fixture["live"]:
            state = publish_certified(store, listing_id)
        tag = "free" if price == 0 else f"{price / 1e6:.0f} USDC"
        console.print(
            f"  {title}  [bold]{grade}[/]  "
            f"{state.value}  {tag}"
        )

    # A listing that looks like eval-set probing, for the admin queue.
    probe_id = f"lst_{_uuid.uuid4().hex[:16]}"
    with store.session() as s:
        store.put_artifact(s, "9" * 64, [], 900_000_000)
        store.create_listing(s, probe_id, "u_creator", "9" * 64, "Nudged-2B (attempt 4)")
        s.commit()
    for i, score in enumerate((0.700, 0.720, 0.735)):
        record_outcome(store, probe_id, Outcome("9" * 64, report=report(
            "D", "9" * 64, score, "Qwen/Qwen2.5-1.5B-Instruct", "development",
            {"mmlu_pro": 0.45}, 2_000_000_000, 900_000_000,
        )),
                       signer=signer, now=now + timedelta(days=i))
    console.print("  Nudged-2B  [yellow]flagged for review[/] (monotonic score creep)")
    console.print(f"\nseeded {db}")


@app.command("sample-model")
def sample_model(
    repo: str = typer.Option(
        "GraySwanAI/Llama-3-8B-Instruct-RR",
        help="Hugging Face repo to prepare as the local submission sample.",
    ),
    dest: Path = typer.Option(
        Path("web/public/sample-model"), help="Served by the web app from /sample-model."
    ),
    store: Path = typer.Option(
        Path(".keystone-store"),
        help="Local artifact store used by the development API.",
    ),
) -> None:
    """Prepare a real model for the local submit flow.

    The browser reads only the generated manifest. The checkpoint itself is
    copied into the local artifact store so multi-gigabyte shards never pass
    through browser memory or the Next.js proxy.

    The fetched checkpoint and generated manifest are local development data
    and are deliberately not committed.
    """
    import json as _json
    import shutil

    from huggingface_hub import snapshot_download

    from keystone.ingest import hash_tree, manifest_digest
    from keystone.profile import parameter_count
    from keystone.storage import LocalStore

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    console.print(f"fetching [bold]{repo}[/] -> {dest}")
    snapshot_download(
        repo_id=repo,
        local_dir=str(dest),
        allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt"],
    )
    shutil.rmtree(dest / ".cache", ignore_errors=True)

    files = hash_tree(dest)
    digest = manifest_digest(files)
    total = sum(entry.size_bytes for entry in files)
    params = parameter_count(dest)

    # Pre-stage the bytes in the same content-addressed store used by the local
    # API. The browser will declare and finalize this manifest as usual, but the
    # declaration returns no upload URLs because every object already exists.
    written = LocalStore(store).upload_tree(dest, files, digest)

    (dest / "files.json").write_text(
        _json.dumps(
            {
                "name": repo.split("/")[-1],
                "source": repo,
                "digest": digest,
                "parameter_count": params,
                "files": [entry.model_dump(mode="json") for entry in files],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for entry in files:
        console.print(f"  {entry.path:<36} {entry.size_bytes:>14,} B")
    console.print(
        f"\n[bold]{len(files)}[/] files, {total / 1024 / 1024:.2f} MB; "
        f"{written / 1024 / 1024:.2f} MB staged in {store}"
    )


@app.command("checkout-probe")
def checkout_probe(
    amount: float = typer.Option(1.0, help="Charge amount to create, in USDC."),
    reference: str = typer.Option("probe", help="Reference to round-trip."),
    raw: bool = typer.Option(True, help="Print the processor's raw JSON."),
) -> None:
    """Create one live charge and check our field mapping against it.

    The vendor-specific half of a payment adapter is guesswork until it has
    spoken to the real API once. This makes that one call and prints the raw
    response beside how we read it, so a wrong field name shows up here rather
    than after a buyer has paid.

    Creates a real charge. It costs nothing unless somebody pays it.
    """
    import json as _json

    from keystone.payments import Currency, Money
    from keystone.providers.coinbase_commerce import from_env as coinbase_from_env
    from keystone.providers.hosted_checkout import from_env as hosted_from_env
    from keystone.providers.onchain import OnChainProvider, from_env as onchain_from_env

    provider = onchain_from_env() or coinbase_from_env() or hosted_from_env()
    if provider is None:
        console.print(
            "[red]no payment provider configured[/]\n"
            "  On-chain: KEYSTONE_RECEIVE_ADDRESS + KEYSTONE_RPC_URL\n"
            "  Coinbase: COINBASE_COMMERCE_API_KEY\n"
            "  Generic:  KEYSTONE_CHECKOUT_API_KEY + KEYSTONE_CHECKOUT_URL"
        )
        raise typer.Exit(code=2)

    if isinstance(provider, OnChainProvider):
        _probe_onchain(provider, amount, reference)
        return

    console.print(f"[bold]{type(provider).__name__}[/] -> {provider.config.base_url}")
    money = Money.from_decimal(str(amount), Currency.USDC)

    try:
        charge = provider.create_charge(money, reference)
    except Exception as exc:
        console.print(f"[red]create failed[/] {exc}")
        raise typer.Exit(code=1) from exc

    if raw:
        try:
            body = provider._request("GET", f"/charges/{charge.charge_id}")
            console.print("\n[dim]raw response[/]")
            console.print(_json.dumps(body, indent=2)[:4000])
        except Exception as exc:  # a mapping bug should not hide the charge
            console.print(f"[yellow]could not re-read[/] {exc}")

    table = Table(title="how we read it")
    table.add_column("field")
    table.add_column("value")
    for field, value in [
        ("charge_id", charge.charge_id),
        ("reference", charge.reference or "[red]MISSING[/]"),
        ("amount", str(charge.amount)),
        ("status", charge.status.value),
        ("checkout_url", charge.checkout_url or "[red]MISSING[/]"),
        ("address", charge.address or "[yellow]none yet[/]"),
        ("expires_at", str(charge.expires_at or "-")),
    ]:
        table.add_row(field, str(value))
    console.print(table)

    problems = []
    if charge.reference != reference:
        problems.append("reference did not round-trip -- metadata mapping is wrong")
    if not charge.checkout_url:
        problems.append("no checkout_url -- buyers would have nowhere to pay")
    if charge.amount != money:
        problems.append(f"amount changed: sent {money}, read back {charge.amount}")
    if charge.status.value == "settled":
        problems.append("a brand-new charge reads as settled -- status mapping is wrong")

    if problems:
        console.print("\n[red]mapping problems[/]")
        for problem in problems:
            console.print(f"  - {problem}")
        raise typer.Exit(code=1)
    console.print("\n[green]mapping looks correct[/] — pay the charge and re-run to check settlement")


def _probe_onchain(provider, amount: float, reference: str) -> None:
    """Check the node, the token, and the quoted amount before money moves."""
    from keystone.payments import Currency, Money

    console.print(f"[bold]OnChainProvider[/] -> {provider.config.rpc_url}")

    try:
        head = provider.block_number()
    except Exception as exc:
        console.print(f"[red]node unreachable[/] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"  chain head        {head:,}")

    # A wrong contract address means watching the wrong token, and every
    # payment would look unpaid forever.
    try:
        token = provider.verify_token()
    except Exception as exc:
        console.print(f"[red]could not read the token contract[/] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"  token             {token['symbol'] or '?'} ({provider.config.token_contract})")
    console.print(f"  decimals          {token['decimals']}")
    if not token["decimals_match"]:
        console.print(
            f"[red]decimals mismatch[/] contract reports {token['decimals']}, "
            f"{provider.config.currency.value} expects {provider.config.currency.decimals}"
        )
        raise typer.Exit(code=1)

    charge = provider.create_charge(
        Money.from_decimal(str(amount), Currency.USDC), reference
    )
    table = Table(title="what a buyer would be asked to send")
    table.add_column("field")
    table.add_column("value")
    table.add_row("to address", charge.address)
    table.add_row("exact amount", str(charge.amount))
    table.add_row("chain", charge.chain)
    table.add_row("confirmations", str(charge.required_confirmations))
    console.print(table)

    console.print(
        "\n[green]node and token verified[/]\n"
        "  Send exactly that amount to that address, then re-run to watch it settle.\n"
        "  The amount is unique per order -- that is how payments are told apart."
    )


@app.command()
def suites() -> None:
    """List discoverable suites and what they require."""
    from keystone.registry import discover

    table = Table(title="suites")
    for col in ("id", "version", "modality", "requires", "gate", "held-out"):
        table.add_column(col)
    for s in discover():
        m = s.manifest
        table.add_row(
            m.id,
            m.version,
            ",".join(x.value for x in m.modality),
            ",".join(m.required_capabilities) or "-",
            "yes" if m.gate else "no",
            "yes" if m.held_out else "no",
        )
    console.print(table)


@app.command()
def stage(
    rotation: int = typer.Option(
        0, help="Which rotation to build. Later ones draw different items."
    ),
) -> None:
    """Fetch the conditioning item sets and write them into suites/*/assets/.

    Needs network, so it never runs inside `evaluate`. Nothing it writes is
    committed -- see the note in staging.py about why a set in git is a burned
    one.
    """
    from keystone.registry import SUITES_ROOT
    from keystone.staging import SET_SIZE, stage as run_stage

    try:
        staged = run_stage(SUITES_ROOT, rotation=rotation)
    except RuntimeError as exc:
        console.print(f"[red]refused[/] {exc}")
        raise typer.Exit(code=2) from None

    table = Table(title=f"staged item sets (rotation {rotation})")
    for column in ("suite", "source", "seeds", "staged", "drawn", "reuse", "digest"):
        table.add_column(column)
    for suite_id, info in staged.items():
        staged_n = str(info["n"])
        if info["short_by"]:
            staged_n += f"  [yellow](-{info['short_by']})[/]"
        floor = info["overlap_floor"]
        reuse = f"[yellow]>={floor}%[/]" if floor else "[green]free[/]"
        table.add_row(
            suite_id, info["source"], str(info["pool"]), staged_n,
            str(info["drawn"]), reuse, info["digest"][:16],
        )
    console.print(table)

    short = {k: v for k, v in staged.items() if v["short_by"]}
    if short:
        console.print(
            "\n[yellow]note[/] "
            f"{', '.join(short)} cannot reach the largest budget "
            "conditioning can ask for, so a model earning the strictest bar "
            "resolves to insufficient evidence rather than passing on a "
            "sample that cannot support the claim. More seed behaviours is "
            "the only fix."
        )

    stale = {k: v for k, v in staged.items() if v["overlap_floor"]}
    if stale:
        console.print(
            "\n[yellow]note[/] "
            f"{', '.join(stale)} forces reuse between rotations at the "
            "budget shown: drawing n items from a pool of p shares at least "
            "2n-p of them however the selection is done."
        )


@app.command()
@app.command("export-benchmarks")
def export_benchmarks(
    out: Path = typer.Option(Path("benchmarks"), help="Directory to write the release into."),
) -> None:
    """Publish the staged domain sets as a checkable benchmark release.

    Separate from the runtime sets in `suites/*/assets`, which stay gitignored.
    Exporting retires a set as a gating instrument -- it is in git history from
    then on -- so rotate with `keystone stage` afterwards.
    """
    from keystone.release import export

    doc = export(out)
    table = Table(title=f"benchmark release {doc['release_version']}")
    for column in ("path", "items", "seeds", "sha256"):
        table.add_column(column)
    for entry in doc["datasets"]:
        table.add_row(
            entry["path"],
            str(entry["items"]),
            str(entry.get("seed_behaviours", "-")),
            entry["sha256"][:12],
        )
    console.print(table)
    console.print(f"wrote {out}/manifest.json")


def schema(out: Path = typer.Option(Path("schemas/report.schema.json"))) -> None:
    """Regenerate the shared JSON Schema from the pydantic source of truth."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(CertificationReport.model_json_schema(), indent=2) + "\n", encoding="utf-8"
    )
    console.print(f"wrote {out}")


def _write(report: CertificationReport, out_dir: Path) -> Path:
    """Sign, then write. A report is the product; an unsigned one is a draft.

    Only the worker signed before this, so `certify` and `batch` wrote reports
    that could be edited undetectably and looked identical to real ones. The
    signer is resolved exactly as the worker resolves it, so a report written
    here verifies against the same published key.

    With no key configured the report is still written -- refusing would make
    the tool unusable for local work -- but `signature` stays null and the
    caller says so out loud.
    """
    from keystone.signing import Ed25519Signer

    signer = Ed25519Signer.from_env()
    if signer is not None:
        report = signer.sign_report(report)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report.report_id}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def _signing_note(path: Path) -> str:
    """Say whether what we just wrote can be verified.

    An unsigned report looks identical to a signed one at a glance, and the
    point of signing is that a rating cannot be edited after the fact. Silence
    here is how an unsigned report ends up treated as evidence.
    """
    written = json.loads(path.read_text(encoding="utf-8"))
    signature = written.get("signature")
    if signature:
        return f"  [green]signed[/] {signature.get('key_id') or signature['algorithm']}"
    return (
        "  [yellow]unsigned[/] -- KEYSTONE_SIGNING_KEY is not set, so any edit "
        "to this report would go undetected."
    )


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
