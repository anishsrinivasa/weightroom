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
        with modal_app.app.run():
            done = process_pending(
                store,
                artifacts=artifacts,
                listing_id=listing_id,
                limit=limit,
                signer=signer,
                on_step=lambda m: console.print(f"  [cyan]·[/] {m}"),
            )
        if not done:
            console.print("  [dim]nothing queued[/]")
        if once:
            return
        time.sleep(interval)


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
    from keystone.schema import Cost, Environment, Rating, ScanResult, SuiteResult
    from keystone.signing import Ed25519Signer
    from keystone.worker import publish_certified, record_outcome

    store = Store(db)
    store.create_all()
    signer = Ed25519Signer.from_env() or Ed25519Signer.generate()
    now = datetime.now(timezone.utc)

    def report(grade: str, digest: str, held: float) -> CertificationReport:
        issued_grade = grade if held >= 0.75 else "F"
        return CertificationReport(
            report_id=f"rep_{_uuid.uuid4().hex[:12]}",
            created_at=now,
            status=Status.PASS if issued_grade != "F" else Status.FAIL,
            subject=Subject(
                source=Source(kind=SourceKind.UPLOAD, ref=f"rcpt_{digest[:8]}"),
                artifact_digest=digest,
                files=[],
                total_bytes=1_400_000_000,
                license={"declared": "apache-2.0", "spdx": "Apache-2.0", "chain_ok": True},
            ),
            environment=Environment(sandboxed=True, seed=0, gpu="NVIDIA A10",
                                    engine_version="0.28.0", python_version="3.12.10"),
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
                SuiteResult(suite_id="public_capability", suite_version="0.4.0",
                            display_name="Instruction following",
                            status=Status.PASS, held_out=False, score=0.91,
                            metrics={"accuracy": 0.91}, n_items=50),
                # Declined benchmarks are part of the record: a seller who can
                # silently omit one can hide a bad result behind it.
                SuiteResult(suite_id="stub_reasoning", suite_version="-",
                            display_name="Multi-step reasoning",
                            status=Status.SKIPPED, declined=True,
                            error="declined by the creator"),
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
        ("Legalese-7B (contract QA)", "1" * 64, "A", 0.94, True, 120_000_000),
        ("MedNote-3B (clinical summaries)", "2" * 64, "B", 0.81, True, 45_000_000),
        ("Tokenizer-Bench-0.5B (open)", "4" * 64, "A", 0.92, True, 0),
        ("ReadySet-3B (support)", "5" * 64, "A", 0.91, False, 60_000_000),
        ("Sentinel-1B (log triage)", "3" * 64, "F", 0.62, False, 30_000_000),
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
        state = record_outcome(
            store,
            listing_id,
            Outcome(digest, report=report(grade, digest, held)),
            signer=signer,
            now=now,
        )
        if go_live:
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
        record_outcome(store, probe_id, Outcome("9" * 64, report=report("D", "9" * 64, score)),
                       signer=signer, now=now + timedelta(days=i))
    console.print("  Nudged-2B  [yellow]flagged for review[/] (monotonic score creep)")
    console.print(f"\nseeded {db}")


@app.command("sample-model")
def sample_model(
    repo: str = typer.Option(
        "HuggingFaceTB/SmolLM2-135M-Instruct",
        help="Any small HuggingFace repo that a serving stack can actually run.",
    ),
    dest: Path = typer.Option(
        Path("web/public/sample-model"), help="Served by the web app from /sample-model."
    ),
) -> None:
    """Install a tiny real model for the submit flow to upload.

    A real checkpoint rather than synthesised bytes: genuine config, tokenizer
    and safetensors, so the demo exercises architecture detection, chat-template
    resolution, lineage and the scanners -- not just hashing.

    Deliberately not a `tiny-random-*` fixture. Those are a few megabytes and
    load fine in transformers, but their attention heads are four wide and no
    serving kernel will touch them, so certification dies on the GPU.

    Not committed. Six megabytes of weights would live in git history forever.
    """
    import json as _json
    import shutil

    from huggingface_hub import snapshot_download

    dest.mkdir(parents=True, exist_ok=True)
    console.print(f"fetching [bold]{repo}[/] -> {dest}")
    snapshot_download(
        repo_id=repo,
        local_dir=str(dest),
        allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt"],
    )
    shutil.rmtree(dest / ".cache", ignore_errors=True)

    files = sorted(
        p.relative_to(dest).as_posix()
        for p in dest.rglob("*")
        if p.is_file() and p.name != "files.json"
    )
    # The browser cannot list a directory, so it reads this manifest first.
    (dest / "files.json").write_text(
        _json.dumps({"name": repo.split("/")[-1], "source": repo, "files": files}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    total = sum((dest / f).stat().st_size for f in files)
    for f in files:
        console.print(f"  {f:<28} {(dest / f).stat().st_size:>10,} B")
    console.print(f"\n[bold]{len(files)}[/] files, {total / 1024 / 1024:.2f} MB")
    if total > 64 * 1024 * 1024:
        console.print("[yellow]warning[/] over the 64 MB browser upload limit")


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
    for column in ("suite", "source", "pool", "variants", "items", "reuse", "digest"):
        table.add_column(column)
    for suite_id, info in staged.items():
        count = str(info["n"])
        if info["short_by"]:
            count += f"  [yellow](short {info['short_by']} of {SET_SIZE})[/]"
        floor = info["overlap_floor"]
        reuse = f"[yellow]>={floor}%[/]" if floor else "[green]free[/]"
        table.add_row(
            suite_id, info["source"], str(info["pool"]), str(info["variants"]),
            count, reuse, info["digest"][:16],
        )
    console.print(table)

    short = {k: v for k, v in staged.items() if v["short_by"]}
    if short:
        console.print(
            "\n[yellow]note[/] "
            f"{', '.join(short)} is below {SET_SIZE} items. At that size the "
            "strictest band is cleared only by a perfect run, so a single judge "
            "error flips a verdict. Generated items close the gap."
        )


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
