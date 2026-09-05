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

import hashlib
import os
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import modal

# Pinned: validated end to end on 2026-09-05 against Qwen2.5-0.5B-Instruct on
# an A10G. A rating is only defensible if it reproduces, and a floating engine
# version breaks that. Bump deliberately, and re-validate when you do.
VLLM_SPEC = "vllm==0.28.0"

APP_NAME = "keystone"
CACHE_ROOT = "/cache"
MODELS_DIR = f"{CACHE_ROOT}/models"
VOLUME_MODELS_DIR = "/models"

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


def _upload_cache_key(digest: str) -> str:
    return f"upload@{digest}"


def _safe_upload_path(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or any(
        part in ("", ".", "..") for part in candidate.parts
    ):
        raise ValueError(f"unsafe artifact path: {relative!r}")
    target = root.joinpath(*candidate.parts)
    if not target.is_relative_to(root):
        raise ValueError(f"artifact path escapes cache root: {relative!r}")
    return target


def _matches(path: Path, expected_size: int, expected_sha256: str) -> bool:
    if not path.is_file() or path.stat().st_size != expected_size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest() == expected_sha256


def _download_verified(
    url: str,
    target: Path,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    """Stream one presigned object with a strict size cap and atomic publish."""
    partial = target.with_name(f".{target.name}.partial")
    digest = hashlib.sha256()
    received = 0
    try:
        with urlopen(url, timeout=120) as response, partial.open("wb") as output:
            while chunk := response.read(8 * 1024 * 1024):
                received += len(chunk)
                if received > expected_size:
                    raise ValueError(f"artifact exceeds declared size: {target.name}")
                digest.update(chunk)
                output.write(chunk)
        if received != expected_size or digest.hexdigest() != expected_sha256:
            raise ValueError(f"artifact integrity check failed: {target.name}")
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _inspect_upload(digest: str, manifest: list[dict], dest: Path, cached: bool) -> dict:
    from keystone.ingest import manifest_digest
    from keystone.profile import build_profile, weight_bytes
    from keystone.provenance import build_license_info, extract_lineage
    from keystone.schema import FileEntry, Source, SourceKind, Subject

    files = [FileEntry.model_validate(item) for item in manifest]
    actual_digest = manifest_digest(files)
    if actual_digest != digest:
        raise ValueError(
            f"artifact manifest digest mismatch: expected {digest}, got {actual_digest}"
        )
    for entry in files:
        if not _matches(
            _safe_upload_path(dest, entry.path), entry.size_bytes, entry.sha256
        ):
            raise ValueError(f"artifact integrity check failed: {entry.path}")

    subject = Subject(
        source=Source(kind=SourceKind.UPLOAD, ref=f"artifact:{digest}", revision=digest),
        artifact_digest=digest,
        files=files,
        total_bytes=sum(entry.size_bytes for entry in files),
    )
    wbytes = weight_bytes(dest)
    profile, caps, modality = build_profile(dest, wbytes)
    subject.modality = modality
    subject.lineage = extract_lineage(dest)
    subject.license, verdict = build_license_info(dest, subject.lineage)

    return {
        "cache_key": _upload_cache_key(digest),
        "cached": cached,
        "subject": subject.model_dump(mode="json"),
        "serving_profile": profile.model_dump(mode="json"),
        "capabilities": caps.model_dump(mode="json"),
        "weight_bytes": wbytes,
        "sellable": verdict.sellable,
        "cpu_seconds": 0.0,
        "bytes_transferred": 0 if cached else subject.total_bytes,
    }


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

    # Provenance and licence: two of the four things a certificate claims to
    # establish. Derived from what the artifact itself declares, never assumed.
    from keystone.provenance import build_license_info, extract_lineage

    subject.lineage = extract_lineage(dest)
    subject.license, verdict = build_license_info(dest, subject.lineage)

    return {
        "cache_key": key,
        "cached": cached,
        "subject": subject.model_dump(mode="json"),
        "serving_profile": profile.model_dump(mode="json"),
        "capabilities": caps.model_dump(mode="json"),
        "weight_bytes": wbytes,
        "sellable": verdict.sellable,
        "cpu_seconds": round(time.monotonic() - started, 2),
        "bytes_transferred": 0 if cached else subject.total_bytes,
    }


@app.function(
    image=fetch_image,
    volumes={CACHE_ROOT: cache},
    timeout=4 * 60 * 60,
)
def fetch_upload(digest: str, manifest: list[dict]) -> dict:
    """Materialize a seller upload in the Modal cache and verify every byte.

    Production workers pass short-lived object-store URLs. A local worker can
    pre-stage the same cache directory through Modal's client API and omit the
    URLs. In both cases this function distrusts the cache and rechecks the
    manifest before any scanner or model server sees the files.
    """
    started = time.monotonic()
    key = _upload_cache_key(digest)
    dest = Path(MODELS_DIR) / key
    entries = [dict(item) for item in manifest]

    cached = bool(entries) and all(
        _matches(
            _safe_upload_path(dest, str(item["path"])),
            int(item["size_bytes"]),
            str(item["sha256"]),
        )
        for item in entries
    )

    if not cached:
        urls = {str(item["path"]): item.get("url") for item in entries}
        if not all(urls.values()):
            raise FileNotFoundError("uploaded artifact is not staged in the Modal cache")

        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
        for item in entries:
            relative = str(item["path"])
            url = str(item["url"])
            if urlparse(url).scheme != "https":
                raise ValueError("artifact download URLs must use HTTPS")
            target = _safe_upload_path(dest, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            _download_verified(
                url,
                target,
                expected_size=int(item["size_bytes"]),
                expected_sha256=str(item["sha256"]),
            )
        cache.commit()

    result = _inspect_upload(digest, entries, dest, cached)
    result["cpu_seconds"] = round(time.monotonic() - started, 2)
    return result


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
