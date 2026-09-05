"""Offline tests for everything that does not need Modal or a GPU.

Run: .venv/Scripts/python.exe -m pytest -q
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from keystone.pipeline import grade as _grade
from keystone.ingest import build_subject, hash_tree, manifest_digest
from keystone.profile import build_profile, detect_modality, pick_resource_class
from keystone.registry import discover, select
from keystone.scan import run_all
from keystone.schema import (
    Capabilities,
    CertificationReport,
    Cost,
    Modality,
    Rating,
    ScanResult,
    Status,
    SuiteResult,
)
from keystone.suites import ModelClient, SuiteContext


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def text_model(tmp_path: Path) -> Path:
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "architectures": ["LlamaForCausalLM"],
                "model_type": "llama",
                "torch_dtype": "bfloat16",
                "max_position_embeddings": 8192,
            }
        )
    )
    (tmp_path / "tokenizer_config.json").write_text(
        json.dumps({"chat_template": "{{ messages }}"})
    )
    (tmp_path / "model.safetensors").write_bytes(b"\x00" * 4096)
    return tmp_path


class FakeClient(ModelClient):
    """Stands in for a served model so suites are testable with no GPU."""

    def __init__(self, reply: str = "yes") -> None:
        self.reply = reply
        self.calls = 0

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        self.calls += 1
        return self.reply

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        self.calls += 1
        return self.reply


# --------------------------------------------------------------------------
# ingest
# --------------------------------------------------------------------------

def test_manifest_digest_is_order_independent(text_model: Path) -> None:
    files = hash_tree(text_model)
    assert manifest_digest(files) == manifest_digest(list(reversed(files)))


def test_digest_changes_when_content_changes(text_model: Path) -> None:
    before = manifest_digest(hash_tree(text_model))
    (text_model / "model.safetensors").write_bytes(b"\x01" * 4096)
    assert manifest_digest(hash_tree(text_model)) != before


def test_subject_totals(text_model: Path) -> None:
    subject = build_subject("acme/demo", text_model, "abc123")
    assert subject.total_bytes == sum(f.size_bytes for f in subject.files)
    assert subject.modality == [Modality.TEXT]


# --------------------------------------------------------------------------
# profile  (SEAM 1 and SEAM 3)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "config,expected_vision",
    [
        ({"architectures": ["LlamaForCausalLM"], "model_type": "llama"}, False),
        ({"architectures": ["LlavaForConditionalGeneration"], "model_type": "llava"}, True),
        ({"architectures": ["X"], "model_type": "x", "vision_config": {}}, True),
        ({"architectures": ["Qwen2VLForConditionalGeneration"], "model_type": "qwen2_vl"}, True),
    ],
)
def test_vision_detection(config: dict, expected_vision: bool) -> None:
    assert (Modality.IMAGE in detect_modality(config)) is expected_vision


def test_resource_ladder_is_monotonic() -> None:
    sizes = [1, 10, 25, 50, 120, 400]
    picks = [pick_resource_class(s * 1024**3) for s in sizes]
    assert picks[0] == "A10G"
    assert picks[-1].endswith(":4")


def test_profile_records_chat_template(text_model: Path) -> None:
    profile, caps, mods = build_profile(text_model, 4096)
    assert profile.chat_template_source == "tokenizer_config"
    assert profile.chat_template_sha256  # pinned for reproducibility
    assert caps.chat is True
    assert caps.vision is False
    assert profile.processor is None  # SEAM 3 stays null for text
    assert mods == [Modality.TEXT]


def test_absurd_context_length_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"architectures": ["LlamaForCausalLM"], "max_position_embeddings": 10**12})
    )
    profile, _, _ = build_profile(tmp_path, 1024)
    assert profile.max_context is None


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------

def test_clean_safetensors_passes(text_model: Path) -> None:
    results = run_all(text_model)
    assert all(r.status is not Status.FAIL for r in results)


def test_pickle_only_weights_warn(tmp_path: Path) -> None:
    (tmp_path / "pytorch_model.bin").write_bytes(b"not really a pickle")
    hygiene = next(r for r in run_all(tmp_path) if r.scanner == "format_hygiene")
    assert hygiene.status is Status.WARN


# --------------------------------------------------------------------------
# registry gating  (SEAM 1 and SEAM 5)
# --------------------------------------------------------------------------

def test_stub_suites_are_discoverable() -> None:
    ids = {s.manifest.id for s in discover()}
    assert {"stub_capability", "stub_reasoning", "stub_safety"} <= ids


def test_vision_suite_is_gated_out_of_a_text_model() -> None:
    from keystone.suites import SuiteManifest

    class VisionSuite:
        manifest = SuiteManifest(
            id="vision_probe",
            version="0.1.0",
            modality=[Modality.TEXT, Modality.IMAGE],
            required_capabilities=["vision"],
        )

        async def run(self, ctx: SuiteContext) -> SuiteResult:  # pragma: no cover
            raise AssertionError("must never run against a text model")

    eligible, skipped = select([VisionSuite()], Capabilities(), [Modality.TEXT])
    assert eligible == []
    assert skipped[0].suite_id == "vision_probe"
    # Ineligible is not the same as declined -- the creator did not choose this.
    assert skipped[0].declined is False


def test_only_filter_declines_the_rest() -> None:
    """Unselected optional suites are declined, and say so."""
    suites = discover()
    eligible, skipped = select(suites, Capabilities(), [Modality.TEXT], only=[])
    # stub_safety is mandatory, so it runs whatever the creator picked.
    assert [s.manifest.id for s in eligible] == ["stub_safety"]
    assert {s.suite_id for s in skipped} == {"stub_capability", "stub_reasoning"}
    assert all(s.declined for s in skipped)


def test_mandatory_suites_ignore_the_selection() -> None:
    """A creator cannot decline safety by leaving it out of the list."""
    suites = discover()
    eligible, _ = select(suites, Capabilities(), [Modality.TEXT], only=["stub_capability"])
    assert "stub_safety" in [s.manifest.id for s in eligible]


# --------------------------------------------------------------------------
# suites run against a fake client
# --------------------------------------------------------------------------

def _run_suite(suite_id: str, reply: str) -> SuiteResult:
    suite = next(s for s in discover() if s.manifest.id == suite_id)
    ctx = SuiteContext(
        client=FakeClient(reply),
        model_name="fake",
        capabilities=Capabilities(),
        scratch_dir=Path("."),
        assets_dir=Path("."),
    )
    return asyncio.run(suite.run(ctx))


def test_safety_suite_flags_over_refusal() -> None:
    result = _run_suite("stub_safety", "I can't help with that.")
    assert result.status is Status.FAIL
    assert result.metrics["false_refusal_rate"] == 1.0
    assert result.findings


def test_safety_suite_passes_a_helpful_model() -> None:
    result = _run_suite("stub_safety", "Sure -- here is how that works.")
    assert result.status is Status.PASS
    assert result.score == 1.0


def test_capability_suite_scores() -> None:
    assert _run_suite("stub_capability", "paris").score == 0.25


# --------------------------------------------------------------------------
# rating
# --------------------------------------------------------------------------

def _suite(score: float, status: Status = Status.PASS) -> SuiteResult:
    return SuiteResult(suite_id="s", suite_version="1", status=status, score=score)


def test_scan_failure_forces_f() -> None:
    grade, _ = _grade([ScanResult(scanner="picklescan", status=Status.FAIL)], [_suite(1.0)])
    assert grade == "F"


def test_suite_error_means_unrated() -> None:
    grade, _ = _grade([], [_suite(1.0), _suite(None, Status.ERROR)])
    assert grade == "unrated"


@pytest.mark.parametrize("score,expected", [(0.95, "A"), (0.8, "B"), (0.65, "C"), (0.5, "D"), (0.1, "F")])
def test_grade_cutoffs(score: float, expected: str) -> None:
    grade, _ = _grade([], [_suite(score)])
    assert grade == expected


# --------------------------------------------------------------------------
# report round-trip
# --------------------------------------------------------------------------

def test_report_validates_against_generated_schema(text_model: Path) -> None:
    subject = build_subject("acme/demo", text_model, "abc123")
    profile, caps, _ = build_profile(text_model, 4096)
    report = CertificationReport(
        report_id="r1",
        created_at=datetime.now(timezone.utc),
        status=Status.PASS,
        subject=subject,
        serving_profile=profile,
        capabilities=caps,
        scans=run_all(text_model),
        suite_results=[_suite(1.0)],
        cost=Cost(gpu_seconds=12.0),
        rating=Rating(grade="A", as_tested_at=datetime.now(timezone.utc)),
    )
    round_tripped = CertificationReport.model_validate_json(report.model_dump_json())
    assert round_tripped.subject.artifact_digest == subject.artifact_digest
    assert round_tripped.rating.grade == "A"


def test_checked_in_schema_is_current() -> None:
    """Guards the shared contract from drifting out of sync with the code."""
    on_disk = json.loads(Path("schemas/report.schema.json").read_text(encoding="utf-8"))
    assert on_disk == CertificationReport.model_json_schema()


# --------------------------------------------------------------------------
# smoke path -- free local runs, and the guardrails that keep them honest
# --------------------------------------------------------------------------

def test_unsandboxed_runs_are_never_graded() -> None:
    """A smoke run must not be mistakable for a certification."""
    grade, rationale = _grade([], [_suite(1.0)], sandboxed=False)
    assert grade == "unrated"
    assert "not sandboxed" in rationale.lower()


def test_sandboxed_runs_still_grade() -> None:
    assert _grade([], [_suite(1.0)], sandboxed=True)[0] == "A"


def test_environment_defaults_to_sandboxed() -> None:
    from keystone.schema import Environment

    assert Environment().sandboxed is True


def test_held_out_suites_are_identified(tmp_path: Path) -> None:
    """Names the suites that must never reach an external endpoint."""
    from keystone.run import held_out_suites

    suite_dir = tmp_path / "secret_probe"
    suite_dir.mkdir()
    (suite_dir / "suite.py").write_text(
        "from keystone.suites import SuiteManifest\n"
        "class S:\n"
        "    manifest = SuiteManifest(id='secret_probe', version='1', held_out=True)\n"
        "    async def run(self, ctx): ...\n"
        "SUITE = S()\n"
    )
    assert held_out_suites(tmp_path) == ["secret_probe"]


def test_public_suites_are_not_flagged_as_held_out() -> None:
    from keystone.registry import SUITES_ROOT
    from keystone.run import held_out_suites

    assert held_out_suites(SUITES_ROOT) == []  # both stubs are public


def test_run_suites_shares_the_sandboxed_code_path() -> None:
    """The smoke path and the real runner must not drift apart."""
    from keystone.registry import SUITES_ROOT
    from keystone.run import run_suites

    results = run_suites(
        FakeClient("Sure, here you go."),
        model_name="fake",
        capabilities=Capabilities(),
        modality=[Modality.TEXT],
        suites_root=SUITES_ROOT,
        scratch_dir=Path("."),
    )
    assert {r.suite_id for r in results} == {
        "stub_capability",
        "stub_reasoning",
        "stub_safety",
    }
    assert all(r.status is not Status.ERROR for r in results)


def test_declined_benchmarks_appear_in_the_results() -> None:
    """A benchmark you can silently omit is one you can hide a bad result behind."""
    from keystone.registry import SUITES_ROOT
    from keystone.run import run_suites

    results = run_suites(
        FakeClient("yes"),
        model_name="fake",
        capabilities=Capabilities(),
        modality=[Modality.TEXT],
        suites_root=SUITES_ROOT,
        scratch_dir=Path("."),
        only=[],  # creator picked nothing optional
    )
    ran = [r for r in results if r.status is not Status.SKIPPED]
    declined = [r for r in results if r.declined]

    assert [r.suite_id for r in ran] == ["stub_safety"]  # mandatory still ran
    assert {r.suite_id for r in declined} == {"stub_capability", "stub_reasoning"}
    # Every offered benchmark is accounted for, run or not.
    assert len(results) == 3


def test_manifest_digest_algorithm_is_pinned() -> None:
    """The browser recomputes this independently, so the algorithm is a contract.

    Sorted by path, one "path:sha256\n" line each, sha256 of the UTF-8 bytes.
    If this value changes, the upload client in static/index.html must change
    with it or every upload will be declared under the wrong digest.
    """
    from keystone.schema import FileEntry

    files = [
        FileEntry(path="model.safetensors", size_bytes=4096, sha256="b" * 64),
        FileEntry(path="config.json", size_bytes=120, sha256="a" * 64),
    ]
    assert manifest_digest(files) == (
        "9f51a3e20eaa31068289daf1a6e0845c0f738576335573c7fa8550b9d4d73962"
    )
