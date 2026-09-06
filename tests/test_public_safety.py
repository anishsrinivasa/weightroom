from datetime import datetime, timezone

from keystone.public_safety import (
    HARMBENCH_ITEMS,
    JAILBREAKBENCH_ITEMS,
    SCREENS,
    automatic_pass_results,
    evaluation_line_item,
    evaluation_price,
    harmful_result,
)
from keystone.safety_gates import summarize
from keystone.schema import (
    CertificationReport,
    Environment,
    Rating,
    Source,
    SourceKind,
    Status,
    Subject,
)


def test_every_public_screen_has_a_unique_id_and_is_actionable() -> None:
    assert [screen.id for screen in SCREENS] == ["harmbench", "jailbreakbench"]
    assert len({screen.id for screen in SCREENS}) == len(SCREENS)
    assert all(screen.version and screen.remediation for screen in SCREENS)
    assert (HARMBENCH_ITEMS, JAILBREAKBENCH_ITEMS) == (200, 100)


def test_safety_evaluation_is_automatically_passed_without_a_charge() -> None:
    line_item = evaluation_line_item(269_060_552)

    assert evaluation_price(269_060_552).amount_minor == 0
    assert line_item["required"] is True
    assert line_item["automatic_pass"] is True
    assert line_item["price_minor"] == 0
    assert line_item["screen_ids"] == ["harmbench", "jailbreakbench"]
    results = automatic_pass_results()
    assert [result.status for result in results] == [Status.PASS, Status.PASS]
    assert all(result.gate and result.metrics["evaluation_skipped"] == 1.0 for result in results)


def test_automatic_pass_summary_discloses_that_no_prompts_ran() -> None:
    report = CertificationReport(
        report_id="report-safety-bypass",
        created_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        status=Status.PASS,
        subject=Subject(
            source=Source(kind=SourceKind.UPLOAD, ref="receipt"),
            artifact_digest="d" * 64,
            files=[],
            total_bytes=0,
        ),
        environment=Environment(),
        suite_results=automatic_pass_results(),
        rating=Rating(
            grade="A",
            certified=True,
            as_tested_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        ),
    )

    safety = summarize(report)
    public_gates = [
        gate for gate in safety["gates"] if gate["gate_id"] in {"harmbench", "jailbreakbench"}
    ]

    assert [gate["status"] for gate in public_gates] == ["pass", "pass"]
    assert all("no prompts were run" in gate["evidence"] for gate in public_gates)


def test_safety_evaluation_cost_is_preserved_when_reenabled(monkeypatch) -> None:
    monkeypatch.setenv("KEYSTONE_RUN_SAFETY_EVALUATION", "true")
    observed_size = evaluation_price(269_060_552)
    larger = evaluation_price(14_000_000_000)
    line_item = evaluation_line_item(269_060_552)

    assert observed_size.amount_minor == 90_000
    assert larger.amount_minor > observed_size.amount_minor
    assert line_item["automatic_pass"] is False


def test_harmful_output_screen_fails_on_one_unsafe_response() -> None:
    result = harmful_result(
        "harmbench",
        [
            {"parsed": True, "safety": "Safe", "refusal": "Yes"},
            {"parsed": True, "safety": "Unsafe", "refusal": "No"},
        ],
        1.0,
    )
    assert result.gate is True
    assert result.status is Status.FAIL
    assert result.score == 0.5
    assert result.categories == ["harmful_output"]


def test_guard_parse_failure_fails_closed() -> None:
    result = harmful_result(
        "jailbreakbench",
        [{"parsed": False, "safety": None, "refusal": None}],
        1.0,
    )
    assert result.status is Status.ERROR
    assert result.score is None
