"""Suite execution, shared by the sandboxed runner and the local smoke path.

Both call the same code so a smoke run exercises the real thing rather than a
parallel implementation that can drift.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from keystone.registry import discover, select
from keystone.schema import Capabilities, Modality, Status, SuiteResult
from keystone.suites import ModelClient, SuiteContext


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
    seed: int = 0,
) -> list[SuiteResult]:
    """Gate on eligibility, then run everything eligible concurrently.

    Gating happens before the caller ever loads a model, so an ineligible suite
    costs no GPU time.
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
        return results

    scratch_dir.mkdir(parents=True, exist_ok=True)

    async def _all() -> list[SuiteResult]:
        return await asyncio.gather(
            *(
                _run_one(
                    suite,
                    SuiteContext(
                        client=client,
                        model_name=model_name,
                        capabilities=capabilities,
                        scratch_dir=scratch_dir,
                        assets_dir=(
                            assets_root / suite.manifest.id
                            if assets_root is not None
                            else suites_root / suite.manifest.id / "assets"
                        ),
                        seed=seed,
                    ),
                )
                for suite in eligible
            )
        )

    completed = asyncio.run(_all())
    by_id = {s.manifest.id: s.manifest for s in eligible}
    for result in completed:
        manifest = by_id.get(result.suite_id)
        if manifest is not None:
            result.display_name = manifest.name
            result.held_out = manifest.held_out
            result.gate = manifest.gate
            result.diagnostic = manifest.diagnostic
            result.gate = manifest.gate
    results.extend(completed)
    return results


def held_out_suites(suites_root: Path, only: list[str] | None = None) -> list[str]:
    """Ids of held-out suites that would run. Used to refuse unsandboxed runs."""
    return [
        s.manifest.id
        for s in discover(suites_root)
        if s.manifest.held_out and (not only or s.manifest.id in only)
    ]
