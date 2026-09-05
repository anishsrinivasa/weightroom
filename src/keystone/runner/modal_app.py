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

# Pinned: validated end to end on 2026-09-05 against Qwen2.5-0.5B-Instruct on
# an A10G. A rating is only defensible if it reproduces, and a floating engine
# version breaks that. Bump deliberately, and re-validate when you do.
VLLM_SPEC = "vllm==0.28.0"

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


# Public models need no HuggingFace token, so none is required by default --
# a first run should not depend on secret setup. For gated repos (Llama et al.)
# create the secret and point at it:
#
#   modal secret create huggingface HF_TOKEN=hf_...
#   export KEYSTONE_HF_SECRET=huggingface
#
_HF_SECRET_NAME = os.environ.get("KEYSTONE_HF_SECRET", "")
_HF_SECRETS = [modal.Secret.from_name(_HF_SECRET_NAME)] if _HF_SECRET_NAME else []


# ---------------------------------------------------------------------------
# fetch: network ON, no untrusted execution
# ---------------------------------------------------------------------------

@app.function(
    image=fetch_image,
    volumes={CACHE_ROOT: cache},
    timeout=4 * 60 * 60,
    secrets=_HF_SECRETS,
    # TODO: raise ephemeral_disk once we know the tier limit; big
    # checkpoints will need it. Default is fine for small models.
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
    import sys

    sys.path.insert(0, "/root")  # so `suites/` is importable

    from keystone.client import OpenAIServerClient, VLLMServer
    from keystone.run import run_suites
    from keystone.schema import Capabilities, Modality

    started = time.monotonic()
    root = Path(MODELS_DIR) / cache_key
    caps = Capabilities.model_validate(capabilities)
    mods = [Modality(m) for m in modality]
    suites_root = Path("/root/suites")

    env = _environment(seed)

    served_name = cache_key
    with VLLMServer(
        root,
        served_name,
        max_context=max_context,
        tensor_parallel_size=tensor_parallel_size,
    ):
        env["engine_version"] = _pkg_version("vllm")
        results = run_suites(
            OpenAIServerClient(served_name, seed=seed),
            model_name=served_name,
            capabilities=caps,
            modality=mods,
            suites_root=suites_root,
            scratch_dir=Path("/tmp/scratch"),
            only=only,
            seed=seed,
        )

    return {
        "suite_results": [r.model_dump(mode="json") for r in results],
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
        "sandboxed": True,
    }


__all__ = ["app", "fetch", "scan", "evaluate"]
