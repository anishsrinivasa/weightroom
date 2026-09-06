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
from keystone.profile import build_profile, detect_modality, parameter_count, pick_resource_class
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
from keystone.tags import model_size_tag


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


def test_parameter_count_comes_from_safetensors_shapes(tmp_path: Path) -> None:
    tensors = {
        "embed.weight": {"dtype": "F16", "shape": [10, 4], "data_offsets": [0, 80]},
        "head.weight": {"dtype": "F16", "shape": [4, 10], "data_offsets": [80, 160]},
        "__metadata__": {"format": "pt"},
    }
    encoded = json.dumps(tensors).encode()
    (tmp_path / "model.safetensors").write_bytes(
        len(encoded).to_bytes(8, "little") + encoded + bytes(160)
    )

    assert parameter_count(tmp_path) == 80
    profile, _, _ = build_profile(tmp_path, 160)
    assert profile.parameter_count == 80


@pytest.mark.parametrize(
    "count,expected",
    [
        (999_999_999, "under-1b"),
        (1_000_000_000, "1b-3b"),
        (3_000_000_000, "1b-3b"),
        (7_000_000_000, "3b-7b"),
        (70_000_000_000, "34b-70b"),
        (70_000_000_001, "70b-plus"),
    ],
)
def test_parameter_count_maps_to_marketplace_range(count: int, expected: str) -> None:
    assert model_size_tag(count) == expected


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
    # Mandatory suites run whatever the creator picked.
    assert {s.manifest.id for s in eligible} == {"stub_safety", "stub_capability"}
    assert {s.suite_id for s in skipped} == {"stub_reasoning"}
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


def _passing_gate() -> SuiteResult:
    return SuiteResult(
        suite_id="harm_gate",
        suite_version="1",
        status=Status.PASS,
        gate=True,
    )


def test_scan_failure_blocks_certification() -> None:
    letter, certified, _ = _grade(
        [ScanResult(scanner="picklescan", status=Status.FAIL)], [_suite(1.0)]
    )
    assert certified is False
    assert letter == "unrated"  # weights never loaded, so nothing was measured


def test_suite_error_blocks_certification() -> None:
    _, certified, rationale = _grade([], [_suite(1.0), _suite(None, Status.ERROR)])
    assert certified is False
    assert "errored" in rationale


def test_missing_safety_gate_blocks_certification() -> None:
    _, certified, rationale = _grade([], [_suite(1.0)])
    assert certified is False
    assert "safety gate" in rationale


def test_a_failed_gate_blocks_certification_but_still_reports_capability() -> None:
    """Capable and unsafe are independent facts; flattening them hides one."""
    gate = SuiteResult(
        suite_id="harm_gate",
        suite_version="1",
        display_name="Harmful-output resistance",
        status=Status.FAIL,
        gate=True,
        score=0.99,
    )
    letter, certified, rationale = _grade([], [_suite(1.0), gate])
    assert certified is False
    assert letter == "A"  # the capability benchmark still scored what it scored
    assert "Harmful-output resistance" in rationale


def test_skipped_safety_gate_blocks_certification() -> None:
    gate = SuiteResult(
        suite_id="harm_gate",
        suite_version="1",
        status=Status.SKIPPED,
        gate=True,
    )
    _, certified, rationale = _grade([], [_suite(1.0), gate])
    assert certified is False
    assert "did not run" in rationale


def test_passing_gate_is_not_averaged_into_capability_grade() -> None:
    gate = SuiteResult(
        suite_id="harm_gate",
        suite_version="1",
        status=Status.PASS,
        gate=True,
        score=0.1,
    )
    letter, certified, _ = _grade([], [_suite(0.95), gate])
    assert certified is True
    assert letter == "A"


def test_gates_alone_certify_without_inventing_a_capability_letter() -> None:
    """The case that used to grade D.

    Nothing was measured about capability, so no letter is honest. "unrated"
    says that; "D" would tell a buyer the model is poor.
    """
    letter, certified, rationale = _grade([], [_passing_gate()])
    assert certified is True
    assert letter == "unrated"
    assert "no capability benchmark" in rationale.lower()


def test_over_refusal_diagnostic_does_not_block_a_safe_model() -> None:
    diagnostic = SuiteResult(
        suite_id="stub_safety",
        suite_version="1",
        status=Status.FAIL,
        score=0.0,
        gate=False,
        # Declared on the result rather than looked up by id, so grading a
        # stored report does not depend on which suites are installed today.
        diagnostic=True,
    )
    grade, certified, rationale = _grade([], [_passing_gate(), diagnostic])
    assert certified is True
    assert grade == "unrated"
    assert "mandatory safety gates passed" in rationale.lower()


def test_a_mandatory_benchmark_still_counts_toward_capability() -> None:
    """Mandatory does not imply diagnostic.

    The capability benchmark is required precisely so every listing has a
    populated product page; excluding it because it is mandatory would leave
    that page saying "not measured" after the seller paid to fill it.
    """
    benchmark = SuiteResult(
        suite_id="stub_capability", suite_version="1",
        status=Status.PASS, score=0.93,
    )
    grade, certified, _ = _grade([], [_passing_gate(), benchmark])
    assert certified is True
    assert grade == "A"


@pytest.mark.parametrize("score,expected", [(0.95, "A"), (0.8, "B"), (0.65, "C"), (0.5, "D"), (0.1, "F")])
def test_capability_cutoffs(score: float, expected: str) -> None:
    letter, certified, _ = _grade([], [_passing_gate(), _suite(score)])
    assert certified is True
    assert letter == expected


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
        rating=Rating(grade="A", certified=True, as_tested_at=datetime.now(timezone.utc)),
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
    grade, certified, rationale = _grade([], [_suite(1.0)], sandboxed=False)
    assert certified is False
    assert grade == "unrated"
    assert "not sandboxed" in rationale.lower()


def test_sandboxed_runs_still_grade() -> None:
    assert _grade([], [_passing_gate(), _suite(1.0)], sandboxed=True)[0] == "A"


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

    assert {r.suite_id for r in ran} == {"stub_safety", "stub_capability"}
    assert {r.suite_id for r in declined} == {"stub_reasoning"}
    # Every offered benchmark is accounted for, run or not.
    assert len(results) == 3


def test_manifest_digest_algorithm_is_pinned() -> None:
    """The browser recomputes this independently, so the algorithm is a contract.

    Sorted by path, one "path:sha256\n" line each, sha256 of the UTF-8 bytes.
    If this value changes, the upload client in web/lib/artifact.ts must change
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


# --------------------------------------------------------------------------
# worker resilience: one bad row must not stall the queue
# --------------------------------------------------------------------------

def _seeded_store(*listings):
    """listings: (listing_id, digest, with_artifact) triples, all queued."""
    from keystone.db import Store
    from keystone.listing import ListingState

    store = Store("sqlite://")
    store.create_all()
    with store.session() as s:
        store.upsert_user(s, "c1", "c@example.com")
        for listing_id, digest, with_artifact in listings:
            if with_artifact:
                store.put_artifact(s, digest, [], 0)
            store.create_listing(s, listing_id, "c1", digest)
            store.get_listing(s, listing_id).state = (
                ListingState.PENDING_CERTIFICATION.value
            )
        s.commit()
    return store


def test_a_missing_artifact_does_not_stall_the_queue() -> None:
    """The orphan is rejected and the healthy listing behind it still runs.

    Reading the manifest off a missing artifact row used to raise outside the
    per-listing guard, killing the pass. Because the orphan stayed pending,
    every later pass died on it too -- a permanent stall.
    """
    from keystone.listing import ListingState
    from keystone.pipeline import Outcome
    from keystone.storage import LocalStore
    from keystone.worker import process_pending

    store = _seeded_store(("l_orphan", "d" * 64, False), ("l_ok", "e" * 64, True))
    results = dict(process_pending(
        store,
        artifacts=LocalStore("./unused-store"),
        certify=None,
    ))

    assert results["l_orphan"] is ListingState.REJECTED
    assert "l_ok" in results  # the healthy listing was still reached


def test_a_missing_artifact_is_named_in_the_taxonomy() -> None:
    from keystone.db import ReportRow  # noqa: F401  (schema import)
    from keystone.pipeline import FailureKind
    from keystone.storage import LocalStore
    from keystone.worker import process_pending

    store = _seeded_store(("l_orphan", "d" * 64, False))
    process_pending(store, artifacts=LocalStore("./unused-store"), certify=None)

    with store.session() as s:
        attempt = store.load_listing(s, "l_orphan").attempts[-1]
    assert attempt.passed is False
    assert FailureKind.MISSING_ARTIFACT.value == "missing_artifact"


def test_claiming_a_listing_is_atomic() -> None:
    """Two workers must not both certify the same listing."""
    from keystone.listing import ListingState

    store = _seeded_store(("l1", "d" * 64, True))
    with store.session() as s:
        first = store.claim_for_certification(s, "l1")
    with store.session() as s:
        second = store.claim_for_certification(s, "l1")

    assert first is True
    assert second is False  # already claimed; the loser skips it
    with store.session() as s:
        assert store.get_listing(s, "l1").state == ListingState.CERTIFYING.value


def test_claiming_a_listing_that_is_not_queued_fails() -> None:
    store = _seeded_store(("l1", "d" * 64, True))
    with store.session() as s:
        store.get_listing(s, "l1").state = "draft"
        s.commit()
    with store.session() as s:
        assert store.claim_for_certification(s, "l1") is False
