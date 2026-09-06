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
import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import modal

from keystone.public_safety import (
    GUARD_REF,
    GUARD_REVISION,
    HARMBENCH_ITEMS,
    HARMBENCH_REVISION,
    JAILBREAKBENCH_ITEMS,
    JAILBREAKBENCH_REVISION,
)

# Pinned: validated end to end on 2026-09-05 against Qwen2.5-0.5B-Instruct on
# an A10G. A rating is only defensible if it reproduces, and a floating engine
# version breaks that. Bump deliberately, and re-validate when you do.
VLLM_SPEC = "vllm==0.28.0"

APP_NAME = "keystone"
CACHE_ROOT = "/cache"
MODELS_DIR = f"{CACHE_ROOT}/models"
VOLUME_MODELS_DIR = "/models"
PUBLIC_SAFETY_ROOT = f"{CACHE_ROOT}/public-safety"
PUBLIC_SAFETY_ASSETS = f"{PUBLIC_SAFETY_ROOT}/assets.json"
PUBLIC_BENCHMARK_ASSETS_ROOT = "/tmp/public-benchmark-assets"

MMLU_PRO_REVISION = "b189ec765aa7ed75c8acfea42df31fdae71f97be"
MMLU_PRO_ITEMS = 12_032
MATH_500_REVISION = "6e4ed1a2a79af7d8630a6b768ec859cb5af4d3be"
SWE_BENCH_VERIFIED_REVISION = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
SWE_BENCH_VERIFIED_ITEMS = 500
GDPVAL_REVISION = "a3848a2a812d5d4d0f08003fac3c8eac40805962"
GDPVAL_ITEMS = 220
HARVEY_LAB_REVISION = "1da4750171bc5a534960b3d82d15ba7fd2cf653f"
HARVEY_LAB_TASK_FILES = 1_760
BENCHMARK_JUDGE_REF = "Qwen/Qwen3-4B-Instruct-2507"
BENCHMARK_JUDGE_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"

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

_eval_base = modal.Image.debian_slim(python_version=_PY).pip_install(
    VLLM_SPEC,
    "openai>=1.60",
    "pydantic>=2.10",
)


def _with_local_source(image: modal.Image) -> modal.Image:
    """Attach our package and suites. Must come after every pip_install:
    Modal rebuilds the whole image if a layer follows a local-file add."""
    return image.add_local_python_source(*_local_src).add_local_dir(
        Path.cwd() / "suites",
        remote_path="/root/suites",
    )


eval_image = _with_local_source(_eval_base)

# The safety path fetches public datasets and an open judge model before the
# sandbox closes, so it needs packages the serving image does not. This was an
# alias for eval_image, which left `datasets` missing -- every certification
# died at the prefetch step, and the failure carried no report to explain why.
safety_eval_image = _with_local_source(
    _eval_base.pip_install(
        "datasets>=2.20",
        "requests>=2.32",
        "huggingface_hub>=1.0",
        "hf_transfer",
        "math-verify==0.8.0",
        "inspect-ai==0.3.263",
        "inspect-evals[swe-bench,gdpval]==0.19.0",
        "inspect-sandboxes==0.5.0",
        "openpyxl>=3.1",
        "pypdf>=5.1",
        "python-docx>=1.1",
        "python-pptx>=1.0",
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
# public safety assets: network ON, no seller code or weights loaded
# ---------------------------------------------------------------------------

def _safety_cache_env() -> dict[str, str]:
    return {
        "HF_HOME": f"{PUBLIC_SAFETY_ROOT}/huggingface",
        "HF_DATASETS_CACHE": f"{PUBLIC_SAFETY_ROOT}/huggingface/datasets",
        "XDG_CACHE_HOME": f"{PUBLIC_SAFETY_ROOT}/xdg",
    }


@app.function(
    image=safety_eval_image,
    volumes={CACHE_ROOT: cache},
    timeout=4 * 60 * 60,
    cpu=4,
    restrict_modal_access=True,
)
def prefetch_public_safety_assets() -> dict:
    """Cache every pinned public dataset plus the independent open judge.

    This function deliberately has egress but never imports or loads the model
    under test.  ``evaluate`` consumes this cache with network access blocked.
    """
    import csv
    import io
    import zipfile

    import requests
    from datasets import load_dataset
    from huggingface_hub import snapshot_download

    cache.reload()
    os.environ.update(_safety_cache_env())
    root = Path(PUBLIC_SAFETY_ROOT)
    root.mkdir(parents=True, exist_ok=True)

    def get_text(url: str) -> str:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        return response.text

    guard_dir = root / f"Qwen3Guard-Gen-0.6B@{GUARD_REVISION}"
    if not guard_dir.exists():
        snapshot_download(GUARD_REF, revision=GUARD_REVISION, local_dir=guard_dir)

    benchmark_judge_dir = root / f"Qwen3-4B-Instruct-2507@{BENCHMARK_JUDGE_REVISION}"
    if not benchmark_judge_dir.exists():
        snapshot_download(
            BENCHMARK_JUDGE_REF,
            revision=BENCHMARK_JUDGE_REVISION,
            local_dir=benchmark_judge_dir,
        )

    harmbench_url = (
        "https://raw.githubusercontent.com/centerforaisafety/HarmBench/"
        f"{HARMBENCH_REVISION}/data/behavior_datasets/harmbench_behaviors_text_all.csv"
    )
    harmbench_rows = list(csv.DictReader(io.StringIO(get_text(harmbench_url))))
    harmbench = [
        {
            "prompt": row["Behavior"],
            "category": row["SemanticCategory"],
            "functional_category": row["FunctionalCategory"],
        }
        for row in harmbench_rows
        if row["FunctionalCategory"] == "standard"
    ]
    if len(harmbench) != HARMBENCH_ITEMS:
        raise RuntimeError(
            f"pinned HarmBench set changed: expected {HARMBENCH_ITEMS}, got {len(harmbench)}"
        )

    jbb_rows = load_dataset(
        "JailbreakBench/JBB-Behaviors",
        "behaviors",
        split="harmful",
        revision=JAILBREAKBENCH_REVISION,
    )
    jailbreakbench = [
        {"prompt": row["Goal"], "category": row["Category"]}
        for row in jbb_rows
    ]
    if len(jailbreakbench) != JAILBREAKBENCH_ITEMS:
        raise RuntimeError(
            "pinned JailbreakBench set changed: "
            f"expected {JAILBREAKBENCH_ITEMS}, got {len(jailbreakbench)}"
        )

    mmlu_rows = load_dataset(
        "TIGER-Lab/MMLU-Pro",
        split="test",
        revision=MMLU_PRO_REVISION,
    )
    mmlu_pro = [
        {
            "id": str(row["question_id"]),
            "question": row["question"],
            "options": list(row["options"]),
            "answer": row["answer"],
            "category": row["category"],
        }
        for row in mmlu_rows
    ]
    if len(mmlu_pro) != MMLU_PRO_ITEMS:
        raise RuntimeError(
            "pinned MMLU-Pro changed: "
            f"expected {MMLU_PRO_ITEMS}, got {len(mmlu_pro)}"
        )

    math_rows = load_dataset(
        "HuggingFaceH4/MATH-500",
        split="test",
        revision=MATH_500_REVISION,
    )
    math_500 = [
        {
            "id": row["unique_id"],
            "problem": row["problem"],
            "answer": row["answer"],
            "subject": row["subject"],
            "level": row["level"],
        }
        for row in math_rows
    ]
    if len(math_500) != 500:
        raise RuntimeError(f"pinned MATH-500 changed: expected 500, got {len(math_500)}")

    swe_bench_verified = load_dataset(
        "princeton-nlp/SWE-bench_Verified",
        split="test",
        revision=SWE_BENCH_VERIFIED_REVISION,
    )
    if len(swe_bench_verified) != SWE_BENCH_VERIFIED_ITEMS:
        raise RuntimeError(
            "pinned SWE-bench Verified changed: "
            f"expected {SWE_BENCH_VERIFIED_ITEMS}, got {len(swe_bench_verified)}"
        )

    gdpval_rows = load_dataset(
        "openai/gdpval",
        split="train",
        revision=GDPVAL_REVISION,
    )
    if len(gdpval_rows) != GDPVAL_ITEMS:
        raise RuntimeError(
            f"pinned GDPval changed: expected {GDPVAL_ITEMS}, got {len(gdpval_rows)}"
        )
    gdpval_dir = root / f"gdpval@{GDPVAL_REVISION}"
    if not gdpval_dir.exists():
        snapshot_download(
            "openai/gdpval",
            repo_type="dataset",
            revision=GDPVAL_REVISION,
            local_dir=gdpval_dir,
            allow_patterns=[
                "README.md",
                "data/**",
                "reference_files/**",
                "deliverable_files/**",
            ],
        )

    harvey_dir = root / f"harvey-labs@{HARVEY_LAB_REVISION}"
    if not harvey_dir.exists():
        archive_url = (
            "https://codeload.github.com/harveyai/harvey-labs/zip/"
            f"{HARVEY_LAB_REVISION}"
        )
        staging = root / f".harvey-labs-{HARVEY_LAB_REVISION}"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        archive_path = staging / "harvey-labs.zip"
        with requests.get(archive_url, timeout=300, stream=True) as response:
            response.raise_for_status()
            with archive_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(staging)
        archive_path.unlink()
        extracted = staging / f"harvey-labs-{HARVEY_LAB_REVISION}"
        if not extracted.is_dir():
            raise RuntimeError("pinned Harvey LAB archive has an unexpected layout")
        extracted.replace(harvey_dir)
        shutil.rmtree(staging, ignore_errors=True)
    harvey_tasks = list((harvey_dir / "tasks").rglob("task.json"))
    if len(harvey_tasks) != HARVEY_LAB_TASK_FILES:
        raise RuntimeError(
            "pinned Harvey LAB changed: "
            f"expected {HARVEY_LAB_TASK_FILES} task files, got {len(harvey_tasks)}"
        )

    assets = {
        "revisions": {
            "guard": GUARD_REVISION,
            "harmbench": HARMBENCH_REVISION,
            "jailbreakbench": JAILBREAKBENCH_REVISION,
            "swe_bench_verified": SWE_BENCH_VERIFIED_REVISION,
            "gdpval": GDPVAL_REVISION,
            "harvey_lab": HARVEY_LAB_REVISION,
            "benchmark_judge": BENCHMARK_JUDGE_REVISION,
        },
        "guard_dir": str(guard_dir),
        "benchmark_judge_dir": str(benchmark_judge_dir),
        "gdpval_dir": str(gdpval_dir),
        "harvey_lab_dir": str(harvey_dir),
        "suites": {
            "harmbench": harmbench,
            "jailbreakbench": jailbreakbench,
        },
        "benchmarks": {
            "mmlu_pro": mmlu_pro,
            "math_500": math_500,
        },
    }
    Path(PUBLIC_SAFETY_ASSETS).write_text(json.dumps(assets), encoding="utf-8")
    cache.commit()
    return {
        "guard_revision": GUARD_REVISION,
        "counts": {name: len(rows) for name, rows in assets["suites"].items()},
        "benchmark_counts": {
            **{name: len(rows) for name, rows in assets["benchmarks"].items()},
            "swe_bench_verified": len(swe_bench_verified),
            "gdpval": len(gdpval_rows),
            "harvey_lab": len(harvey_tasks),
        },
    }


@app.function(
    image=safety_eval_image,
    volumes={CACHE_ROOT: cache},
    block_network=True,
    restrict_modal_access=False,
    timeout=4 * 60 * 60,
)
def smoke_agent_sandboxes() -> dict[str, str]:
    """Operational preflight for the two document-producing benchmark images."""
    from keystone.runner.inspect_benchmarks import smoke_agent_sandboxes as smoke

    cache.reload()
    assets = json.loads(Path(PUBLIC_SAFETY_ASSETS).read_text(encoding="utf-8"))
    return smoke(harvey_root=Path(assets["harvey_lab_dir"]))


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


def _parse_guard(text: str) -> dict:
    safety = re.search(r"Safety:\s*(Safe|Unsafe|Controversial)", text, re.I)
    refusal = re.search(r"Refusal:\s*(Yes|No)", text, re.I)
    return {
        "safety": safety.group(1).title() if safety else None,
        "refusal": refusal.group(1).title() if refusal else None,
        "parsed": bool(safety),
    }


# ---------------------------------------------------------------------------
# evaluate: network OFF, GPU, weights get loaded here
# ---------------------------------------------------------------------------

@app.function(
    image=safety_eval_image,
    volumes={CACHE_ROOT: cache},
    gpu="A10G",  # overridden per-model via .with_options(gpu=...)
    block_network=True,
    # Inspect's trusted controller needs Modal API access to create one
    # networkless Sandbox per agent task. Uploaded artifacts are SafeTensors
    # only and were scanned before reaching this function; the model never
    # receives credentials or direct process access.
    restrict_modal_access=False,
    timeout=24 * 60 * 60,
)
def evaluate(
    cache_key: str,
    capabilities: dict,
    modality: list[str],
    max_context: int | None = None,
    tensor_parallel_size: int = 1,
    only: list[str] | None = None,
    seed: int = 0,
):
    """Wrapper that guarantees a failure is readable by the caller.

    Modal serialises a raised exception as an object. A torch or datasets
    exception cannot be reconstructed by a client that does not import those
    packages, so the caller gets "could not deserialize remote exception" and
    the real message is destroyed in transit. Flattening the traceback to text
    here means it always survives.
    """
    import traceback

    try:
        yield from _evaluate(
            cache_key,
            capabilities,
            modality,
            max_context,
            tensor_parallel_size,
            only,
            seed,
        )
    except Exception as exc:  # noqa: BLE001 - losing this is the whole problem
        report = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )[-6000:]

        # A connection error means the server went away, and why it went away
        # is in its log rather than in this traceback. Without the tail the
        # caller sees "Connection error." and learns nothing.
        try:
            log = Path("/tmp/vllm.log").read_text(errors="replace").splitlines()
            report += "\n--- vllm log tail ---\n" + "\n".join(log[-40:])
        except OSError:
            pass
        raise RuntimeError(report) from None


def _evaluate(
    cache_key: str,
    capabilities: dict,
    modality: list[str],
    max_context: int | None = None,
    tensor_parallel_size: int = 1,
    only: list[str] | None = None,
    seed: int = 0,
):
    import asyncio
    import sys

    sys.path.insert(0, "/root")  # so `suites/` is importable

    from keystone.client import OpenAIServerClient, VLLMServer
    from keystone.public_safety import BY_ID, harmful_result
    from keystone.runner.inspect_benchmarks import (
        run_gdpval_generation,
        run_harvey_lab_generation,
        run_swe_bench_verified,
        score_rubric_run,
    )
    from keystone.run import run_suites
    from keystone.schema import Capabilities, Modality

    cache.reload()
    started = time.monotonic()
    os.environ.update(_safety_cache_env())
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    root = Path(MODELS_DIR) / cache_key
    caps = Capabilities.model_validate(capabilities)
    mods = [Modality(m) for m in modality]
    suites_root = Path("/root/suites")

    env = _environment(seed)
    assets = json.loads(Path(PUBLIC_SAFETY_ASSETS).read_text(encoding="utf-8"))
    benchmark_assets_root = Path(PUBLIC_BENCHMARK_ASSETS_ROOT)
    for suite_id, rows in assets.get("benchmarks", {}).items():
        suite_assets = benchmark_assets_root / suite_id
        suite_assets.mkdir(parents=True, exist_ok=True)
        (suite_assets / "tasks.json").write_text(
            json.dumps(rows), encoding="utf-8"
        )
    suite_ids = ("harmbench", "jailbreakbench")
    total_work = 2 * sum(len(assets["suites"][suite_id]) for suite_id in suite_ids)
    completed_work = 0
    gate_work = {suite_id: 0 for suite_id in suite_ids}
    gate_scores: dict[str, float | None] = {suite_id: None for suite_id in suite_ids}
    gate_status = {suite_id: "pending" for suite_id in suite_ids}

    def progress(stage: str) -> dict:
        percent = 10 + round(85 * completed_work / total_work) if total_work else 95
        return {
            "type": "progress",
            "percent": min(95, percent),
            "stage": stage,
            "gates": [
                {
                    "gate_id": suite_id,
                    "display_name": BY_ID[suite_id].display_name,
                    "status": gate_status[suite_id],
                    "completed": gate_work[suite_id],
                    "total": 2 * len(assets["suites"][suite_id]),
                    "score": gate_scores[suite_id],
                }
                for suite_id in suite_ids
            ],
        }

    batch_size = 16
    served_name = cache_key
    generated: dict[str, list[dict]] = {}
    pending_rubric_runs = []
    suite_started = {suite_id: time.monotonic() for suite_id in suite_ids}
    with VLLMServer(
        root,
        served_name,
        max_context=max_context,
        tensor_parallel_size=tensor_parallel_size,
    ):
        env["engine_version"] = _pkg_version("vllm")
        client = OpenAIServerClient(served_name, seed=seed)
        selected_public = sorted(
            set(only or []).intersection(assets.get("benchmarks", {}))
        )
        if selected_public:
            yield {
                **progress(
                    "Running selected capability benchmarks: "
                    + ", ".join(selected_public)
                ),
                "percent": 10,
            }
        results = run_suites(
            client,
            model_name=served_name,
            capabilities=caps,
            modality=mods,
            suites_root=suites_root,
            scratch_dir=Path("/tmp/scratch"),
            assets_root=benchmark_assets_root,
            only=only,
            seed=seed,
        )
        if "swe_bench_verified" in set(only or []):
            yield {
                **progress("Running SWE-bench Verified in isolated Modal sandboxes"),
                "percent": 10,
            }
            results.append(
                run_swe_bench_verified(
                    served_model_name=served_name,
                    artifact_digest=served_name,
                    log_dir=Path("/tmp/inspect-logs/swe-bench-verified"),
                )
            )
        if "gdpval" in set(only or []):
            yield {
                **progress("Running GDPval agents in isolated Modal sandboxes"),
                "percent": 10,
            }
            pending_rubric_runs.append(
                run_gdpval_generation(
                    served_model_name=served_name,
                    artifact_digest=served_name,
                    dataset_root=Path(assets["gdpval_dir"]),
                    log_dir=Path("/tmp/inspect-logs/gdpval"),
                )
            )
        if "harvey_lab" in set(only or []):
            yield {
                **progress("Running Harvey LAB agents in isolated Modal sandboxes"),
                "percent": 10,
            }
            pending_rubric_runs.append(
                run_harvey_lab_generation(
                    served_model_name=served_name,
                    artifact_digest=served_name,
                    dataset_root=Path(assets["harvey_lab_dir"]),
                    log_dir=Path("/tmp/inspect-logs/harvey-lab"),
                )
            )
        for suite_id in suite_ids:
            generated[suite_id] = []
            rows = assets["suites"][suite_id]
            for offset in range(0, len(rows), batch_size):
                batch = rows[offset : offset + batch_size]

                async def generate_batch() -> list[dict]:
                    async def generate(row: dict) -> dict:
                        response = await client.chat(
                            [{"role": "user", "content": row["prompt"]}],
                            max_tokens=256,
                            temperature=0.0,
                        )
                        return {**row, "response": response}

                    return list(await asyncio.gather(*(generate(row) for row in batch)))

                generated[suite_id].extend(asyncio.run(generate_batch()))
                completed = len(batch)
                completed_work += completed
                gate_work[suite_id] += completed
                gate_status[suite_id] = "running"
                yield progress(f"Generating {BY_ID[suite_id].display_name}")

    if pending_rubric_runs:
        yield {
            **progress("Scoring generated work products with the pinned rubric judge"),
            "percent": 10,
        }
        with VLLMServer(
            Path(assets["benchmark_judge_dir"]),
            "benchmark-judge",
            max_context=32_768,
        ):
            rubric_judge = OpenAIServerClient("benchmark-judge", seed=seed)
            for pending_run in pending_rubric_runs:
                results.append(asyncio.run(score_rubric_run(pending_run, rubric_judge)))

    with VLLMServer(Path(assets["guard_dir"]), "safety-judge", max_context=4096):
        judge = OpenAIServerClient("safety-judge", seed=seed)
        for suite_id in suite_ids:
            outcomes: list[dict] = []
            rows = generated[suite_id]
            for offset in range(0, len(rows), batch_size):
                batch = rows[offset : offset + batch_size]

                async def grade_batch() -> list[dict]:
                    async def grade(row: dict) -> dict:
                        raw = await judge.chat(
                            [
                                {"role": "user", "content": row["prompt"]},
                                {"role": "assistant", "content": row["response"]},
                            ],
                            max_tokens=96,
                            temperature=0.0,
                        )
                        return _parse_guard(raw)

                    return list(await asyncio.gather(*(grade(row) for row in batch)))

                outcomes.extend(asyncio.run(grade_batch()))
                completed = len(batch)
                completed_work += completed
                gate_work[suite_id] += completed
                yield progress(f"Scoring {BY_ID[suite_id].display_name}")

            result = harmful_result(
                suite_id,
                outcomes,
                round(time.monotonic() - suite_started[suite_id], 2),
            )
            results.append(result)
            gate_scores[suite_id] = result.score
            gate_status[suite_id] = result.status.value
            yield progress(f"Completed {BY_ID[suite_id].display_name}")

    yield {
        "type": "result",
        "payload": {
            "suite_results": [r.model_dump(mode="json") for r in results],
            "environment": env,
            "gpu_seconds": round(time.monotonic() - started, 2),
        },
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


@app.local_entrypoint(name="validate-safety")
def validate_safety(
    cache_key: str,
    gpu: str = "A10G",
    max_context: int = 4096,
) -> None:
    """Run the production evaluation graph against an already-cached model."""
    assets = prefetch_public_safety_assets.remote()
    result = None
    for event in evaluate.with_options(gpu=gpu).remote_gen(
        cache_key,
        {"chat": True, "completions": True, "max_context": max_context},
        ["text"],
        max_context,
        1,
        None,
        0,
    ):
        if event.get("type") == "progress":
            print(
                f"{event['percent']:>3}%  {event['stage']}",
                flush=True,
            )
        elif event.get("type") == "result":
            result = event["payload"]
    if result is None:
        raise RuntimeError("Modal evaluation ended without a result")
    summary = {
        "assets": assets,
        "gpu_seconds": result["gpu_seconds"],
        "suites": [
            {
                "id": item["suite_id"],
                "status": item["status"],
                "score": item.get("score"),
                "n_items": item.get("n_items"),
                "error": item.get("error"),
            }
            for item in result["suite_results"]
        ],
    }
    print(json.dumps(summary, indent=2))


__all__ = [
    "app",
    "evaluate",
    "fetch",
    "fetch_upload",
    "prefetch_public_safety_assets",
    "scan",
]
