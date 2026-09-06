"""Suite execution, shared by the sandboxed runner and the local smoke path.

Both call the same code so a smoke run exercises the real thing rather than a
parallel implementation that can drift.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from keystone.conditioning import (
    BASELINE_ITEMS,
    CLUSTER_ALLOWANCE,
    gate_floor,
    MAX_ITEMS,
    NOT_REQUIRED,
    TYPICAL_BASELINE_N,
    TYPICAL_BASELINE_RATE,
    activating_capability,
    items_for_kappa,
    rated_capability,
    tolerated_harm,
)
from keystone.judging import HeuristicJudge, Judge, Judgement, Transcript
from keystone.registry import discover, select
from keystone.schema import Capabilities, Modality, Status, SuiteResult
from keystone.suites import ModelClient, SuiteContext


@dataclass
class Pending:
    """A judged suite that has collected but not yet scored.

    Held in memory only. These carry elicitation prompts paired with what the
    model said back, which is precisely the material that must never be
    persisted or leave the sandbox.
    """

    suite: object
    transcripts: list[Transcript] = field(default_factory=list)
    started: float = 0.0


async def _run_one(suite, ctx: SuiteContext) -> SuiteResult:
    try:
        return await asyncio.wait_for(suite.run(ctx), timeout=suite.manifest.timeout_s)
    except TimeoutError:
        return SuiteResult(
            suite_id=suite.manifest.id,
            suite_version=suite.manifest.version,
            status=Status.ERROR,
            error=f"timed out after {suite.manifest.timeout_s}s",
        )
    except Exception as exc:
        return SuiteResult(
            suite_id=suite.manifest.id,
            suite_version=suite.manifest.version,
            status=Status.ERROR,
            error=repr(exc),
        )


async def _collect_one(suite, ctx: SuiteContext) -> Pending | SuiteResult:
    started = time.monotonic()
    try:
        transcripts = await asyncio.wait_for(
            suite.collect(ctx), timeout=suite.manifest.timeout_s
        )
        return Pending(suite=suite, transcripts=list(transcripts), started=started)
    except TimeoutError:
        return SuiteResult(
            suite_id=suite.manifest.id,
            suite_version=suite.manifest.version,
            status=Status.ERROR,
            error=f"timed out after {suite.manifest.timeout_s}s",
        )
    except Exception as exc:
        return SuiteResult(
            suite_id=suite.manifest.id,
            suite_version=suite.manifest.version,
            status=Status.ERROR,
            error=repr(exc),
        )


def finalise_judged(
    pending: list[Pending], judge: Judge, *, suites_root: Path | None = None
) -> list[SuiteResult]:
    """Phase two: grade the transcripts and let each suite score its own.

    Judging is one batch across every pending suite, so the judge model is
    stood up once no matter how many suites needed it.
    """
    if not pending:
        return []

    flat: list[Transcript] = []
    spans: list[tuple[Pending, int, int]] = []
    for item in pending:
        start = len(flat)
        flat.extend(item.transcripts)
        spans.append((item, start, len(flat)))

    try:
        graded: list[Judgement] = asyncio.run(judge.judge(flat))
    except Exception as exc:
        return [
            SuiteResult(
                suite_id=item.suite.manifest.id,
                suite_version=item.suite.manifest.version,
                status=Status.ERROR,
                judge_id=getattr(judge, "id", None),
                error=f"judge failed: {exc!r}",
            )
            for item in pending
        ]

    results: list[SuiteResult] = []
    for item, start, end in spans:
        manifest = item.suite.manifest
        elapsed = round(time.monotonic() - item.started, 2)
        try:
            scored = item.suite.score(graded[start:end], elapsed)
            # Stamped by the platform, not the suite. A suite cannot be trusted
            # to record what graded it, and a result that does not name its
            # judge cannot be reproduced or audited.
            results.append(
                _stamp(
                    scored.model_copy(update={"judge_id": getattr(judge, "id", None)}),
                    manifest,
                )
            )
        except Exception as exc:
            results.append(
                SuiteResult(
                    suite_id=item.suite.manifest.id,
                    suite_version=item.suite.manifest.version,
                    status=Status.ERROR,
                    error=repr(exc),
                )
            )
    return results


def _stamp(result: SuiteResult, manifest) -> SuiteResult:
    """Copy manifest facts onto a result the suite may not have set.

    Read from the manifest rather than trusted from the suite, so a suite
    cannot declare itself non-gating or publicly visible.
    """
    if manifest is None:
        return result
    result.display_name = manifest.name
    result.held_out = manifest.held_out
    result.gate = manifest.gate
    result.diagnostic = manifest.diagnostic
    result.internal = manifest.internal
    result.utility = manifest.utility
    result.baseline = manifest.baseline
    result.domain = manifest.domain
    result.role = manifest.role
    result.conditioned_by = manifest.conditioned_by
    result.chance_floor = manifest.chance_floor
    return result


def item_budgets(probe_results: list[SuiteResult], judged: list) -> dict[str, int]:
    """How many items each judged suite should run, given what the probes found.

    Zero means the domain did not activate and the set is skipped outright.
    Otherwise the count is whatever it takes to demonstrate the bar that
    capability earns -- a few dozen at the bottom, over a thousand at the top.
    A fixed size over-buys evidence for models that cannot do harm and
    under-buys it for the ones that can.
    """
    by_id = {r.suite_id: r for r in probe_results}
    budgets: dict[str, int] = {}
    for suite in judged:
        manifest = suite.manifest
        if not manifest.conditioned_by:
            continue
        probe = by_id.get(manifest.conditioned_by)
        if probe is None or probe.score is None:
            # No probe means fail-closed, which needs the strictest bar --
            # and therefore the most evidence we are willing to buy.
            budgets[manifest.id] = MAX_ITEMS
            continue
        floor = gate_floor(manifest.domain)
        if activating_capability(probe) < floor:
            budgets[manifest.id] = 0
            continue
        # Inflated for clustering: budgets count raw items and are spent
        # against a smaller effective size, so the raw figure under-provisions
        # and lands on insufficient-evidence.
        ceiling = tolerated_harm(max(rated_capability(probe), floor))
        budgets[manifest.id] = (
            min(MAX_ITEMS, int(items_for_kappa(ceiling) * CLUSTER_ALLOWANCE))
            if ceiling
            else 0
        )
    return budgets


def _not_required(suite) -> SuiteResult:
    """A domain the probe ruled out. Recorded, never silently absent."""
    manifest = suite.manifest
    return SuiteResult(
        suite_id=manifest.id,
        suite_version=manifest.version,
        status=Status.PASS,
        score=None,
        n_items=0,
        conditioned_verdict=NOT_REQUIRED,
        threshold_basis=(
            f"{manifest.conditioned_by}: capability below the gate floor, so "
            "this domain cannot produce meaningful uplift and was not run"
        ),
    )


def _reporter(suite, emit):
    """Progress events for one suite, filtered for what a creator may learn.

    Streaming progress is a seller-facing feature and conditioning made it a
    leak. Two rules, both about the same secret:

    An **internal** suite emits nothing at all. A probe's id appearing in the
    stream tells a creator a capability measurement is being taken, and the
    only content of that suite is the number they must never see.

    A **conditioned** suite emits a fraction, never counts. Its item budget is
    derived from the tolerated-harm ceiling, which is derived from the
    capability band -- so "0 / 128" and "0 / 192" are that band, printed on the
    progress bar. Percent-of-run carries the same usefulness and none of the
    number.
    """
    manifest = suite.manifest
    if manifest.internal:
        return lambda completed, total: None

    if manifest.conditioned_by:
        def report(completed: int, total: int) -> None:
            emit({
                "suite_id": manifest.id,
                "display_name": manifest.name,
                "percent": round(100 * completed / total) if total else 0,
            })

        return report

    def report(completed: int, total: int) -> None:
        emit({
            "suite_id": manifest.id,
            "display_name": manifest.name,
            "completed": completed,
            "total": total,
        })

    return report


def collect_suites(
    client: ModelClient,
    *,
    model_name: str,
    capabilities: Capabilities,
    modality: list[Modality],
    suites_root: Path,
    scratch_dir: Path,
    assets_root: Path | None = None,
    only: list[str] | None = None,
    disabled_ids: set[str] | None = None,
    seed: int = 0,
    on_progress: Callable[[dict], None] = lambda event: None,
) -> tuple[list[SuiteResult], list[Pending]]:
    """Phase one. Everything that needs the model under test served.

    Returns finished results plus any judged suites still awaiting a verdict.
    The caller owns what happens between the phases -- in the sandbox that is
    tearing down this model and standing up the judge, which is why the two
    halves are separate callables rather than one.
    """
    disabled_ids = disabled_ids or set()
    installed = [
        suite
        for suite in discover(suites_root)
        if suite.manifest.id not in disabled_ids
    ]
    eligible, skipped = select(installed, capabilities, modality, only=only)

    results = [
        SuiteResult(
            suite_id=s.suite_id,
            suite_version="-",
            display_name=s.display_name,
            status=Status.SKIPPED,
            gate=s.gate,
            declined=s.declined,
            error=s.reason,
        )
        for s in skipped
    ]

    if not eligible:
        return results, []

    scratch_dir.mkdir(parents=True, exist_ok=True)
    by_id = {s.manifest.id: s.manifest for s in eligible}

    def assets_for(suite) -> Path:
        """Where this suite's items live.

        `assets_root` is where the sandbox drops corpora it fetched while it
        still had network. Only suites whose items are fetched that way have a
        directory there -- a suite whose items were staged into the image keeps
        its own. Applying the override to everything pointed the conditioning
        pairs at a directory that only ever holds the public benchmarks, so
        they loaded zero items and errored on every run.
        """
        own = suites_root / suite.manifest.id / "assets"
        if assets_root is None:
            return own
        staged = assets_root / suite.manifest.id
        return staged if staged.exists() else own

    def context(suite, budget: int | None = None) -> SuiteContext:
        return SuiteContext(
            client=client,
            model_name=model_name,
            capabilities=capabilities,
            scratch_dir=scratch_dir,
            assets_dir=assets_for(suite),
            seed=seed,
            item_budget=budget,
            on_progress=_reporter(suite, on_progress),
        )

    direct = [s for s in eligible if not s.manifest.judged]
    two_phase = [s for s in eligible if s.manifest.judged]

    # Probes first, then the sets they size. The probe was always described as
    # a router and until now routed nothing -- every elicitation set ran at a
    # fixed 100 items whatever the model turned out to be capable of.
    async def _direct() -> list:
        return list(await asyncio.gather(*(_run_one(s, context(s)) for s in direct)))

    completed = [_stamp(r, by_id.get(r.suite_id)) for r in asyncio.run(_direct())]
    budgets = item_budgets(completed, two_phase)

    async def _collect() -> list:
        return list(await asyncio.gather(*(
            _collect_one(s, context(s, budgets.get(s.manifest.id)))
            for s in two_phase
            if budgets.get(s.manifest.id) != 0
        )))

    collected = asyncio.run(_collect()) if two_phase else []

    # Domains the probe says are not worth gating never run at all. That is the
    # routing saving, and it is also the correct answer rather than a shortcut.
    for suite in two_phase:
        if budgets.get(suite.manifest.id) == 0:
            completed.append(_stamp(_not_required(suite), suite.manifest))

    # A collect that failed already produced its own error result.
    completed.extend(c for c in collected if isinstance(c, SuiteResult))
    pending = [c for c in collected if isinstance(c, Pending)]

    results.extend(completed)
    return results, pending


def run_suites(
    client: ModelClient,
    *,
    model_name: str,
    capabilities: Capabilities,
    modality: list[Modality],
    suites_root: Path,
    scratch_dir: Path,
    assets_root: Path | None = None,
    only: list[str] | None = None,
    disabled_ids: set[str] | None = None,
    seed: int = 0,
    on_progress: Callable[[dict], None] = lambda event: None,
    judge: Judge | None = None,
) -> list[SuiteResult]:
    """Both phases, back to back.

    Correct only where the judge does not contend with the model under test --
    an external endpoint, or a fake in tests. The sandboxed runner shares one
    GPU between them and drives the two halves itself.

    Without a judge, judged suites fall back to the marker heuristic. The id of
    whatever graded them lands on the result, so such a run is identifiable
    rather than quietly indistinguishable from a real one.
    """
    results, pending = collect_suites(
        client,
        model_name=model_name,
        capabilities=capabilities,
        modality=modality,
        suites_root=suites_root,
        scratch_dir=scratch_dir,
        assets_root=assets_root,
        only=only,
        disabled_ids=disabled_ids,
        seed=seed,
        on_progress=on_progress,
    )
    if pending:
        results.extend(
            finalise_judged(pending, judge or HeuristicJudge(), suites_root=suites_root)
        )
    return results


def held_out_suites(suites_root: Path, only: list[str] | None = None) -> list[str]:
    """Ids of suites that must not run outside the sandbox.

    Held-out sets are the obvious case: an external endpoint sees every prompt
    sent to it, so running them there burns the eval set.

    Internal suites are refused for a second reason. A conditioning probe run
    against an endpoint the creator controls hands them their own capability
    band, which is the number the safety threshold is derived from -- and the
    one thing they must not be able to read. That holds even while the seed
    items are public, because the rule has to be in place before generated
    items make it matter.
    """
    return [
        s.manifest.id
        for s in discover(suites_root)
        if (s.manifest.held_out or s.manifest.internal)
        and (not only or s.manifest.id in only)
    ]
