"""The contract between the platform (Anish) and the eval harness (safety side).

The platform guarantees: a live model behind `ModelClient`, a scratch dir, an
assets dir, and no network. The harness guarantees: a `SuiteResult` that
validates against the shared schema.

Neither side imports the other's internals.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from keystone.schema import Capabilities, Modality, SuiteResult


# --------------------------------------------------------------------------
# How a suite reaches the model
# --------------------------------------------------------------------------

class ModelClient(abc.ABC):
    """Swappable transport to the model under test.

    The MVP implementation is an ephemeral local vLLM OpenAI server. An offline
    batch implementation can drop in behind this same interface if suites turn
    out to be single-turn and batch-shaped -- see `OpenAIServerClient` vs a
    future `OfflineBatchClient`. Suites must not care which is in use.
    """

    @abc.abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> str:
        """Single exchange. Safe to call concurrently -- the server batches."""

    @abc.abstractmethod
    async def complete(
        self, prompt: str, *, max_tokens: int = 512, temperature: float = 0.0, **kwargs: Any
    ) -> str:
        ...


@dataclass
class SuiteContext:
    """Everything a suite is handed. Nothing else is reachable."""

    client: ModelClient
    model_name: str
    capabilities: Capabilities
    scratch_dir: Path
    assets_dir: Path  # SEAM 2: suite-owned blobs, staged before egress is cut
    seed: int = 0


# --------------------------------------------------------------------------
# What a suite declares about itself
# --------------------------------------------------------------------------

@dataclass
class SuiteManifest:
    id: str
    version: str
    display_name: str = ""
    # Mandatory suites cannot be declined. Safety is not a menu item: a
    # creator who could opt out of the safety check would, and the badge would
    # then mean nothing.
    mandatory: bool = False
    # Run this suite against the declared base model too and report the
    # difference. The suite is unchanged and never knows -- the platform runs
    # it twice and diffs, so authoring stays single-model.
    differential: bool = False
    # Gates are fail-closed and do not get averaged into a capability grade.
    # A mandatory benchmark need not be a gate (for example, a marketplace may
    # always report a quality diagnostic), so this is deliberately separate.
    gate: bool = False
    # What running this costs the creator, in USDC minor units. Price scales
    # with what they pick rather than being flat, because GPU time does.
    price_minor: int = 0
    modality: list[Modality] = field(default_factory=lambda: [Modality.TEXT])  # SEAM 1
    required_capabilities: list[str] = field(default_factory=list)
    resource_class: str | None = None
    timeout_s: int = 1800
    assets: list[str] = field(default_factory=list)  # SEAM 2
    held_out: bool = False
    description: str = ""

    @property
    def name(self) -> str:
        return self.display_name or self.id

    def is_eligible(self, caps: Capabilities, modality: list[Modality]) -> tuple[bool, str]:
        """Gate before the model is ever loaded."""
        missing = [c for c in self.required_capabilities if not getattr(caps, c, False)]
        if missing:
            return False, f"missing capabilities: {', '.join(missing)}"
        unsupported = [m for m in self.modality if m not in modality]
        if unsupported:
            return False, f"model lacks modality: {', '.join(m.value for m in unsupported)}"
        return True, ""


@runtime_checkable
class Suite(Protocol):
    """Implemented on the harness side. Discovered by manifest id."""

    manifest: SuiteManifest

    async def run(self, ctx: SuiteContext) -> SuiteResult: ...
