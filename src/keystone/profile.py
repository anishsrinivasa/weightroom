"""Derive a ServingProfile from a model's own config files.

This is the plane that quietly decides whether a rating is correct. A wrong
chat template does not raise -- it just makes the model look worse than it is,
and we would be signing our name to that. So every derived value is recorded in
the report, and every unknown is explicit rather than guessed.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from keystone.schema import Capabilities, Modality, ServingProfile

# Rough VRAM headroom over raw weight bytes (KV cache + activations).
_VRAM_HEADROOM = 1.35

# (usable_gpu_bytes, modal_gpu_spec)
_RESOURCE_LADDER: list[tuple[int, str]] = [
    (22 * 1024**3, "A10G"),
    (39 * 1024**3, "A100-40GB"),
    (79 * 1024**3, "A100-80GB"),
    (158 * 1024**3, "A100-80GB:2"),
    (316 * 1024**3, "A100-80GB:4"),
]

# Vision-shaped models are detected and refused in the LLM MVP rather than
# half-working their way into a signed report. See SEAM 1.
_VISION_MARKERS = ("vision", "llava", "idefics", "paligemma", "qwen2vl", "qwen2_5_vl", "internvl")

_WEIGHT_SUFFIXES = {".safetensors", ".bin", ".pt", ".pth", ".gguf", ".ckpt"}

# Attention kernels need at least this much per head. Models below it exist --
# `tiny-random-*` test fixtures use 4 -- and they load in transformers but no
# serving stack will run them.
MIN_HEAD_DIM = 16


def head_dimension(config: dict) -> int | None:
    """Per-head width, stated or derived. None when the config does not say."""
    stated = config.get("head_dim")
    if isinstance(stated, int) and stated > 0:
        return stated
    hidden = config.get("hidden_size")
    heads = config.get("num_attention_heads")
    if isinstance(hidden, int) and isinstance(heads, int) and heads > 0:
        return hidden // heads
    return None


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def weight_bytes(root: Path) -> int:
    return sum(
        p.stat().st_size
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in _WEIGHT_SUFFIXES
    )


def parameter_count(root: Path) -> int | None:
    """Count checkpoint parameters from safetensors headers without loading weights.

    Tensor shapes are authoritative and independent of dtype or quantization.
    Malformed files are left for the scanner to reject; this metadata pass
    simply reports unknown instead of guessing from byte size.
    """
    total = 0
    seen: set[str] = set()
    found = False
    try:
        for path in sorted(root.rglob("*.safetensors")):
            with path.open("rb") as handle:
                header_size = int.from_bytes(handle.read(8), "little")
                if header_size <= 0 or header_size > 100 * 1024 * 1024:
                    return None
                header = json.loads(handle.read(header_size))
            for name, tensor in header.items():
                if name == "__metadata__" or name in seen:
                    continue
                shape = tensor.get("shape") if isinstance(tensor, dict) else None
                if not isinstance(shape, list) or not all(
                    isinstance(dimension, int) and dimension >= 0 for dimension in shape
                ):
                    return None
                total += math.prod(shape)
                seen.add(name)
                found = True
    except (OSError, json.JSONDecodeError, AttributeError):
        return None

    if found:
        return total

    config = _load_json(root / "config.json")
    stated = config.get("num_parameters")
    return stated if isinstance(stated, int) and stated >= 0 else None


def detect_modality(config: dict) -> list[Modality]:
    """SEAM 1. Text-only in the MVP; vision models are detected, not guessed at."""
    arch = " ".join(config.get("architectures") or []).lower()
    model_type = str(config.get("model_type", "")).lower()
    blob = arch + " " + model_type
    if "vision_config" in config or any(m in blob for m in _VISION_MARKERS):
        return [Modality.TEXT, Modality.IMAGE]
    return [Modality.TEXT]


def pick_resource_class(total_weight_bytes: int) -> str:
    need = int(total_weight_bytes * _VRAM_HEADROOM)
    for capacity, spec in _RESOURCE_LADDER:
        if need <= capacity:
            return spec
    return _RESOURCE_LADDER[-1][1]


def build_profile(
    local: Path, total_weight_bytes: int
) -> tuple[ServingProfile, Capabilities, list[Modality]]:
    config = _load_json(local / "config.json")
    tok_config = _load_json(local / "tokenizer_config.json")

    modality = detect_modality(config)

    template = tok_config.get("chat_template")
    if isinstance(template, list):  # some repos ship a list of named templates
        template = json.dumps(template, sort_keys=True)

    max_ctx = (
        config.get("max_position_embeddings")
        or config.get("n_positions")
        or tok_config.get("model_max_length")
    )
    if not isinstance(max_ctx, int) or max_ctx > 10_000_000:  # sentinel garbage
        max_ctx = None

    quant = (config.get("quantization_config") or {}).get("quant_method")
    architectures = config.get("architectures") or []

    profile = ServingProfile(
        engine="vllm",
        architecture=architectures[0] if architectures else None,
        dtype=config.get("torch_dtype"),
        quantization=quant,
        max_context=max_ctx,
        chat_template_source="tokenizer_config" if template else "none",
        chat_template_sha256=(
            hashlib.sha256(template.encode()).hexdigest() if template else None
        ),
        resource_class=pick_resource_class(total_weight_bytes),
        parameter_count=parameter_count(local),
        head_dim=head_dimension(config),
        processor=None,  # SEAM 3: populated for VLMs
    )

    caps = Capabilities(
        chat=bool(template),
        completions=True,
        logprobs=True,  # vLLM exposes these; suites may require them
        max_context=max_ctx,
        vision=Modality.IMAGE in modality,
    )
    return profile, caps, modality
