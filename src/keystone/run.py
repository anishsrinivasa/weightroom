"""Suite execution, shared by the sandboxed runner and the local smoke path.

Both call the same code so a smoke run exercises the real thing rather than a
parallel implementation that can drift.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

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
    result.domain = manifest.domain
    result.role = manifest.role
    result.conditioned_by = manifest.conditioned_by
    result.chance_floor = manifest.chance_floor
    return result


def collect_suites(
    client: ModelClient,
    *,
    model_name: str,
    capabilities: Capabilities,
    modality: list[Modality],
    suites_root: Path,
    scratch_dir: Path,
    only: list[str] | None = None,
    seed: int = 0,
) -> tuple[list[SuiteResult], list[Pending]]:
    """Phase one. Everything that needs the model under test served.

    Returns finished results plus any judged suites still awaiting a verdict.
    The caller owns what happens between the phases -- in the sandbox that is
    tearing down this model and standing up the judge, which is why the two
    halves are separate callables rather than one.
    """
    eligible, skipped = select(discover(suites_root), capabilities, modality, only=only)

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

    def context(suite) -> SuiteContext:
        return SuiteContext(
            client=client,
            model_name=model_name,
            capabilities=capabilities,
            scratch_dir=scratch_dir,
            assets_dir=suites_root / suite.manifest.id / "assets",
            seed=seed,
        )

    direct = [s for s in eligible if not s.manifest.judged]
    two_phase = [s for s in eligible if s.manifest.judged]

    async def _all() -> tuple[list, list]:
        ran, collected = await asyncio.gather(
            asyncio.gather(*(_run_one(s, context(s)) for s in direct)),
            asyncio.gather(*(_collect_one(s, context(s)) for s in two_phase)),
        )
        return list(ran), list(collected)

    completed, collected = asyncio.run(_all())
    # A collect that failed already produced its own error result.
    completed.extend(c for c in collected if isinstance(c, SuiteResult))
    pending = [c for c in collected if isinstance(c, Pending)]

    results.extend(_stamp(r, by_id.get(r.suite_id)) for r in completed)
    return results, pending


def run_suites(
    client: ModelClient,
    *,
    model_name: str,
    capabilities: Capabilities,
    modality: list[Modality],
    suites_root: Path,
    scratch_dir: Path,
    only: list[str] | None = None,
    seed: int = 0,
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
        only=only,
        seed=seed,
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
