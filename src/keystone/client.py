"""ModelClient implementations and the ephemeral vLLM server that backs them.

The server is internal and short-lived: it exists only so suites have something
to talk to during a certification job, and it is torn down when the job ends.
Nothing here is ever exposed to a buyer -- Keystone sells downloadable
artifacts, not inference.

Why a server and not vLLM's offline batch API: continuous batching happens
regardless of how the harness writes its loop, multi-turn red-team probes work
naturally, and standard eval frameworks speak this protocol unmodified. If the
suites turn out to be uniformly single-turn and batch-shaped, add an
OfflineBatchClient behind the same interface -- no suite has to change.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from keystone.suites import ModelClient

_WEIGHT_SUFFIXES = {".safetensors", ".bin", ".pt", ".pth", ".gguf", ".ckpt"}


def _weight_bytes(root: Path) -> int:
    try:
        return sum(
            p.stat().st_size
            for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in _WEIGHT_SUFFIXES
        )
    except OSError:
        return 0


_HOST = "127.0.0.1"
_PORT = 8000
_BASE_URL = f"http://{_HOST}:{_PORT}/v1"


class OpenAIServerClient(ModelClient):
    """Talks to the local vLLM OpenAI-compatible server."""

    def __init__(self, model_name: str, base_url: str = _BASE_URL, seed: int = 0) -> None:
        self.model_name = model_name
        self.base_url = base_url
        self.seed = seed
        # One client per event loop, created on first use in that loop.
        #
        # An AsyncOpenAI holds an httpx connection pool bound to the loop it
        # was built in. Callers here run batches through repeated
        # `asyncio.run(...)`, and each of those closes its loop -- so a client
        # built once and reused across them eventually reaches for a socket
        # attached to a dead loop and fails with a bare "Connection error",
        # part-way through a run rather than at the start.
        self._per_loop: dict[object, object] = {}

    @property
    def _client(self):
        import asyncio

        from openai import AsyncOpenAI

        loop = asyncio.get_running_loop()
        client = self._per_loop.get(loop)
        if client is None:
            client = AsyncOpenAI(
                base_url=self.base_url, api_key="not-used", max_retries=2
            )
            self._per_loop[loop] = client
        return client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> str:
        resp = await self._client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=self.seed,
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    async def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> str:
        resp = await self._client.completions.create(
            model=self.model_name,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=self.seed,
            **kwargs,
        )
        return resp.choices[0].text or ""


# Fraction over raw weights for KV cache and activations. Must match the
# assumption `profile.pick_resource_class` makes when it sizes the card, or the
# two decisions contradict: the picker chose a GPU believing we would use most
# of it, and this told vLLM to use three quarters.
VRAM_HEADROOM = 1.4

# vLLM is never told to claim the whole card: some is already gone to the CUDA
# context and the allocator needs slack. The picker has to know this figure,
# because a card it sizes on raw capacity is a card the server cannot fill.
MAX_CLAIM = 0.95


def card_bytes() -> int | None:
    """Total VRAM on the device we actually landed on, or None off-GPU.

    Queried rather than inferred from the resource class. The ladder is our
    idea of what Modal gave us; this is what is really there, and a claim
    computed from the wrong number is how a model that fits fails to load.
    """
    try:
        import torch

        if torch.cuda.is_available():
            return int(torch.cuda.get_device_properties(0).total_memory)
    except Exception:
        pass
    return None


def memory_utilisation(weight_bytes: int, total_bytes: int | None = None) -> float:
    """How much of the card vLLM may claim for weights plus KV cache.

    Two pressures, in tension, which is why this is not a constant.

    Left alone, vLLM takes ~90% and fills it with KV blocks. For a small model
    each block is tiny, so the block *count* becomes enormous -- and
    FlexAttention's physical-to-logical mapping table, sized by that count, can
    then need more memory than the card has. A 6 MB model asking for a 20 GiB
    index table on a 22 GiB GPU is the failure mode the lower tiers prevent.

    But a large model needs nearly the whole card just to hold its weights. A
    14.2 GiB checkpoint given 75% of a 22 GiB A10G has 2.3 GiB left for KV
    cache and activations, and vLLM refuses to start. That is not a tuning
    problem, it is the floor being below the weights.

    So the size-scaled claim is a floor, not a ceiling: whatever the weights
    demand wins when it is larger.
    """
    gib = weight_bytes / 1024**3
    if gib < 1:
        scaled = 0.20
    elif gib < 4:
        scaled = 0.45
    elif gib < 16:
        scaled = 0.75
    else:
        scaled = 0.90

    if not total_bytes:
        return scaled
    needed = weight_bytes * VRAM_HEADROOM / total_bytes
    return min(MAX_CLAIM, max(scaled, needed))


class VLLMServer:
    """Context manager around a vLLM subprocess.

    Deliberately small: this is ~40 lines of process management, not a plane of
    its own.
    """

    def __init__(
        self,
        model_path: Path,
        served_name: str,
        *,
        max_context: int | None = None,
        tensor_parallel_size: int = 1,
        startup_timeout_s: int = 900,
        weight_bytes: int | None = None,
    ) -> None:
        self.model_path = model_path
        self.served_name = served_name
        self.max_context = max_context
        self.tensor_parallel_size = tensor_parallel_size
        if weight_bytes is None:
            weight_bytes = _weight_bytes(model_path)
        self.gpu_memory_utilization = memory_utilisation(weight_bytes, card_bytes())
        self.startup_timeout_s = startup_timeout_s
        self.proc: subprocess.Popen | None = None
        self.log_path = Path("/tmp/vllm.log")

    def _command(self) -> list[str]:
        cmd = [
            sys.executable,
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            str(self.model_path),
            "--served-model-name",
            self.served_name,
            "--host",
            _HOST,
            "--port",
            str(_PORT),
            "--tensor-parallel-size",
            str(self.tensor_parallel_size),
            "--gpu-memory-utilization",
            f"{self.gpu_memory_utilization:.2f}",
        ]
        if self.max_context:
            # Cap context so a model advertising 1M tokens does not fail to
            # allocate a KV cache on a 24GB card.
            cmd += ["--max-model-len", str(min(self.max_context, 16384))]
        return cmd

    def __enter__(self) -> str:
        env = dict(os.environ)
        env.setdefault("HF_HUB_OFFLINE", "1")  # egress is blocked anyway; fail loudly
        # Egress is blocked, so telemetry cannot succeed -- it can only hang or
        # fail engine init. Off explicitly rather than by accident.
        env.setdefault("VLLM_NO_USAGE_STATS", "1")
        env.setdefault("DO_NOT_TRACK", "1")
        # flashinfer JIT-compiles sampling kernels on first use, which needs the
        # full CUDA toolkit (nvcc) and sometimes the network. Both are absent by
        # design here. The native sampler is deterministic, needs neither, and
        # is plenty for evaluation -- we are measuring behaviour, not chasing
        # serving throughput.
        env.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")
        self._log = self.log_path.open("wb")
        self.proc = subprocess.Popen(
            self._command(), stdout=self._log, stderr=subprocess.STDOUT, env=env
        )
        self._wait_ready()
        return _BASE_URL

    def _wait_ready(self) -> None:
        import urllib.error
        import urllib.request

        health = f"http://{_HOST}:{_PORT}/health"
        deadline = time.monotonic() + self.startup_timeout_s
        while time.monotonic() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(
                    f"vLLM exited with code {self.proc.returncode}\n{self._tail_log()}"
                )
            try:
                with urllib.request.urlopen(health, timeout=5) as resp:
                    if resp.status == 200:
                        return
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
            time.sleep(3)
        raise TimeoutError(
            f"vLLM did not become ready in {self.startup_timeout_s}s\n{self._tail_log()}"
        )

    def _tail_log(self, n: int = 250) -> str:
        try:
            lines = self.log_path.read_text(errors="replace").splitlines()
            return "\n".join(lines[-n:])
        except OSError:
            return "(no log)"

    def __exit__(self, *exc: object) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        with contextlib.suppress(Exception):
            self._log.close()


async def gather_bounded(coros: list, limit: int = 32) -> list:
    """Run suite work concurrently but bounded, so one suite cannot starve others."""
    sem = asyncio.Semaphore(limit)

    async def _run(c):
        async with sem:
            return await c

    return await asyncio.gather(*(_run(c) for c in coros), return_exceptions=True)
