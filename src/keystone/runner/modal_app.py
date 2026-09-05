"""The execution plane, split by trust level.

Three functions, three different privilege sets. The split is the sandbox story:

  fetch     network ON,  CPU.  Moves bytes only. No untrusted code executes.
  scan      network OFF, CPU.  Inspects artifacts before anything loads them.
  evaluate  network OFF, GPU.  Loads weights -- the moment hostile code can run.

`evaluate` is the dangerous one and therefore the locked-down one. Blocking its
egress does double duty: it contains a malicious checkpoint, and it stops
held-out eval prompts from leaving the box.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import modal

# TODO(pin): once a vLLM version is validated end to end, pin it here. A rating
# is only defensible if it is reproducible, and a floating engine version breaks
# that. The resolved version is recorded in report.environment either way.
VLLM_SPEC = "vllm"

APP_NAME = "keystone"
CACHE_ROOT = "/cache"
MODELS_DIR = f"{CACHE_ROOT}/models"

app = modal.App(APP_NAME)

# Content-addressed model cache. Two submissions of the same weights download
# once -- the single biggest lever on cost-per-certification.
cache = modal.Volume.from_name("keystone-model-cache", create_if_missing=True)

_PY = "3.12"
_local_src = ["keystone"]

fetch_image = (
    modal.Image.debian_slim(python_version=_PY)
    .pip_install("huggingface_hub>=1.0", "hf_transfer", "pydantic>=2.10")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
    .add_local_python_source(*_local_src)
)

scan_image = (
    modal.Image.debian_slim(python_version=_PY)
    .pip_install("picklescan", "pydantic>=2.10")
    .add_local_python_source(*_local_src)
)

eval_image = (
    modal.Image.debian_slim(python_version=_PY)
    .pip_install(VLLM_SPEC, "openai>=1.60", "pydantic>=2.10")
    .add_local_python_source(*_local_src)
    .add_local_dir(
        Path(__file__).resolve().parents[3] / "suites",
        remote_path="/root/suites",
    )
)


def _cache_key(ref: str, revision: str) -> str:
    return f"{ref.replace('/', '__')}@{revision}"


# ---------------------------------------------------------------------------
# fetch: network ON, no untrusted execution
# ---------------------------------------------------------------------------

@app.function(
    image=fetch_image,
    volumes={CACHE_ROOT: cache},
    timeout=4 * 60 * 60,
    secrets=[modal.Secret.from_name("huggingface", required_keys=["HF_TOKEN"])],
    ephemeral_disk=1024 * 1024,
)
def fetch(ref: str, revision: str | None = None) -> dict:
    from keystone.ingest import build_subject, resolve_and_download
    from keystone.profile import build_profile, weight_bytes

    started = time.monotonic()

    from huggingface_hub import HfApi

    resolved = HfApi().model_info(ref, revision=revision).sha or revision or "main"
    key = _cache_key(ref, resolved)
    dest = Path(MODELS_DIR) / key

    cached = dest.is_dir() and any(dest.iterdir())
    if not cached:
        dest.mkdir(parents=True, exist_ok=True)
        resolve_and_download(ref, dest, revision=resolved)
        cache.commit()

    subject = build_subject(ref, dest, resolved)
    wbytes = weight_bytes(dest)
    profile, caps, modality = build_profile(dest, wbytes)
    subject.modality = modality

    return {
        "cache_key": key,
        "cached": cached,
        "subject": subject.model_dump(mode="json"),
        "serving_profile": profile.model_dump(mode="json"),
        "capabilities": caps.model_dump(mode="json"),
        "weight_bytes": wbytes,
        "cpu_seconds": round(time.monotonic() - started, 2),
        "bytes_transferred": 0 if cached else subject.total_bytes,
    }


# ---------------------------------------------------------------------------
# scan: network OFF, before anything is loaded
# ---------------------------------------------------------------------------

@app.function(
    image=scan_image,
    volumes={CACHE_ROOT: cache},
    block_network=True,
    restrict_modal_access=True,
    timeout=60 * 60,
    cpu=4,
)
def scan(cache_key: str) -> list[dict]:
    from keystone.scan import run_all

    root = Path(MODELS_DIR) / cache_key
    return [r.model_dump(mode="json") for r in run_all(root)]


# ---------------------------------------------------------------------------
# evaluate: network OFF, GPU, weights get loaded here
# ---------------------------------------------------------------------------

@app.function(
    image=eval_image,
    volumes={CACHE_ROOT: cache},
    gpu="A10G",  # overridden per-model via .with_options(gpu=...)
    block_network=True,
    restrict_modal_access=True,
    timeout=4 * 60 * 60,
)
def evaluate(
    cache_key: str,
    capabilities: dict,
    modality: list[str],
    max_context: int | None = None,
    tensor_parallel_size: int = 1,
    only: list[str] | None = None,
    seed: int = 0,
) -> dict:
    import asyncio
    import sys

    sys.path.insert(0, "/root")  # so `suites/` is importable

    from keystone.client import OpenAIServerClient, VLLMServer
    from keystone.registry import discover, select
    from keystone.schema import Capabilities, Modality, Status, SuiteResult
    from keystone.suites import SuiteContext

    started = time.monotonic()
    root = Path(MODELS_DIR) / cache_key
    caps = Capabilities.model_validate(capabilities)
    mods = [Modality(m) for m in modality]

    suites = discover(Path("/root/suites"))
    eligible, skipped = select(suites, caps, mods, only=only)

    results: list[dict] = [
        SuiteResult(
            suite_id=sid,
            suite_version="-",
            status=Status.SKIPPED,
            error=reason,
        ).model_dump(mode="json")
        for sid, reason in skipped
    ]

    env = _environment(seed)

    if not eligible:
        return {"suite_results": results, "environment": env, "gpu_seconds": 0.0}

    served_name = cache_key
    scratch = Path("/tmp/scratch")
    scratch.mkdir(parents=True, exist_ok=True)

    with VLLMServer(
        root,
        served_name,
        max_context=max_context,
        tensor_parallel_size=tensor_parallel_size,
    ):
        env["engine_version"] = _pkg_version("vllm")
        client = OpenAIServerClient(served_name, seed=seed)

        async def run_all_suites() -> list[SuiteResult]:
            async def one(s):
                try:
                    return await asyncio.wait_for(
                        s.run(
                            SuiteContext(
                                client=client,
                                model_name=served_name,
                                capabilities=caps,
                                scratch_dir=scratch,
                                assets_dir=Path("/root/suites") / s.manifest.id / "assets",
                                seed=seed,
                            )
                        ),
                        timeout=s.manifest.timeout_s,
                    )
                except Exception as exc:
                    return SuiteResult(
                        suite_id=s.manifest.id,
                        suite_version=s.manifest.version,
                        status=Status.ERROR,
                        error=repr(exc),
                    )

            return await asyncio.gather(*(one(s) for s in eligible))

        completed = asyncio.run(run_all_suites())

    results.extend(r.model_dump(mode="json") for r in completed)
    return {
        "suite_results": results,
        "environment": env,
        "gpu_seconds": round(time.monotonic() - started, 2),
    }


def _pkg_version(name: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return None


def _environment(seed: int) -> dict:
    import platform

    gpu_name, driver = None, None
    try:
        import subprocess

        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        if out:
            gpu_name, driver = (x.strip() for x in out.splitlines()[0].split(",", 1))
    except Exception:
        pass

    return {
        "gpu": gpu_name,
        "gpu_count": int(os.environ.get("MODAL_GPU_COUNT", 1) or 1),
        "driver_version": driver,
        "python_version": platform.python_version(),
        "torch_version": _pkg_version("torch"),
        "engine_version": _pkg_version("vllm"),
        "container_digest": os.environ.get("MODAL_IMAGE_ID"),
        "seed": seed,
        "harness_version": None,
    }


__all__ = ["app", "fetch", "scan", "evaluate"]
