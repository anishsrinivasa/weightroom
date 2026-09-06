"""API and worker tests. No Modal, no GPU, no network.

The redaction tests here matter most: this is where `redact()` stops being a
library function and becomes a security boundary.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keystone.api import IMAGE_LIMIT_BYTES, Deps, create_app
from keystone.auth import Principal, StaticTokenAuth, audience_for
from keystone.db import Store
from keystone.listing import AttemptPolicy, ListingState
from keystone.payments import Currency, MockPaymentProvider, Money
from keystone.pipeline import FailureKind, Outcome
from keystone.schema import (
    Audience,
    CertificationReport,
    Cost,
    Environment,
    Rating,
    Source,
    SourceKind,
    Status,
    Subject,
    SuiteResult,
)
from keystone.storage import LocalStore, artifact_key
from keystone.worker import process_pending, publish_certified, record_outcome

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
DIGEST = "d" * 64

CREATOR = Principal("u_creator", "creator@example.com")
OTHER = Principal("u_other", "other@example.com")
ADMIN = Principal("u_admin", "admin@example.com", is_admin=True)

FILES = [{"path": "config.json", "size_bytes": 120, "sha256": "a" * 64}]


@pytest.fixture
def deps(tmp_path: Path) -> Deps:
    store = Store("sqlite://")
    store.create_all()
    auth = StaticTokenAuth({"tok-creator": CREATOR, "tok-other": OTHER, "tok-admin": ADMIN})
    return Deps(store, LocalStore(tmp_path / "store"), MockPaymentProvider(), auth)


@pytest.fixture
def client(deps: Deps) -> TestClient:
    return TestClient(create_app(deps))


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _upload_and_list(client: TestClient, deps: Deps, title="Demo") -> str:
    """Walk the real client flow: declare -> put bytes -> finalize -> list."""
    body = {"digest": DIGEST, "files": FILES}
    declared = client.post("/v1/artifacts", json=body, headers=_hdr("tok-creator")).json()
    assert not declared["complete"]  # nothing stored yet

    for f in FILES:  # stand in for the client PUTting to the presigned URL
        key = artifact_key(DIGEST, f["path"])
        target = deps.artifacts.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * f["size_bytes"])

    client.post(f"/v1/artifacts/{DIGEST}/finalize", json=body, headers=_hdr("tok-creator"))
    r = client.post(
        "/v1/listings",
        json={"artifact_digest": DIGEST, "title": title},
        headers=_hdr("tok-creator"),
    )
    return r.json()["listing_id"]


def _make_public(deps: Deps, listing_id: str) -> None:
    """Expose a fixture through the buyer boundary without exercising the worker."""
    with deps.store.session() as s:
        row = deps.store.get_listing(s, listing_id)
        assert row is not None
        row.state = ListingState.LISTED.value
        s.commit()


def _report(grade="B", held_out_score=0.88, report_id=None) -> CertificationReport:
    return CertificationReport(
        report_id=report_id or f"rep_{uuid.uuid4().hex[:12]}",
        created_at=NOW,
        status=Status.PASS,
        subject=Subject(
            source=Source(kind=SourceKind.UPLOAD, ref="rcpt"),
            artifact_digest=DIGEST,
            files=[],
            total_bytes=0,
            license={"declared": "apache-2.0", "spdx": "Apache-2.0", "chain_ok": True},
        ),
        environment=Environment(sandboxed=True, seed=0),
        suite_results=[
            SuiteResult(
                suite_id="heldout_harm",
                suite_version="1",
                status=Status.PASS,
                gate=True,
                held_out=True,
                score=held_out_score,
                metrics={"refusal_rate": held_out_score},
                n_items=200,
                categories=["harmful_content_refusal"],
                remediation="public/harm_practice_v1",
            ),
            SuiteResult(
                suite_id="mmlu_pro",
                suite_version="1",
                status=Status.PASS,
                score=0.72,
                n_items=100,
            ),
        ],
        cost=Cost(gpu_seconds=81.0, usd_estimate=0.0248),
        rating=Rating(grade=grade, certified=grade not in ("F", "unrated"), as_tested_at=NOW),
    )


# --------------------------------------------------------------------------
# auth + audience resolution
# --------------------------------------------------------------------------

def test_audience_is_derived_not_requested() -> None:
    assert audience_for(None, "u_creator") is Audience.BUYER
    assert audience_for(OTHER, "u_creator") is Audience.BUYER
    assert audience_for(CREATOR, "u_creator") is Audience.CREATOR
    assert audience_for(ADMIN, "u_creator") is Audience.INTERNAL


def test_audience_query_param_is_ignored(client: TestClient, deps: Deps) -> None:
    """The obvious attack: ask for the internal view."""
    listing_id = _upload_and_list(client, deps)
    with deps.store.session() as s:
        deps.store.put_report(s, _report(), listing_id=listing_id)
        s.commit()
    _make_public(deps, listing_id)

    r = client.get(f"/v1/listings/{listing_id}?audience=internal", headers=_hdr("tok-other"))
    assert r.json()["audience"] == "buyer"


def test_anonymous_is_a_buyer(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    with deps.store.session() as s:
        deps.store.put_report(s, _report(), listing_id=listing_id)
        s.commit()
    _make_public(deps, listing_id)
    assert client.get(f"/v1/listings/{listing_id}").json()["audience"] == "buyer"


def test_listing_exposes_redacted_safety_gate_summary(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    with deps.store.session() as s:
        deps.store.put_report(s, _report(), listing_id=listing_id)
        s.commit()

    body = client.get(
        f"/v1/listings/{listing_id}", headers=_hdr("tok-creator")
    ).json()
    behavioral = next(
        gate for gate in body["safety_gates"]["gates"] if gate["gate_id"] == "heldout_harm"
    )
    assert behavioral["status"] == "pass"
    assert behavioral["held_out"] is True
    assert behavioral["score_band"] == "good"
    assert "score" not in behavioral
    assert body["report"]["suite_results"][0]["score"] is None


def test_public_safety_percentage_is_visible_to_the_seller(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _upload_and_list(client, deps)
    report = _report()
    result = report.suite_results[0]
    result.suite_id = "harmbench"
    result.display_name = "HarmBench harmful-output resistance"
    result.held_out = False
    result.score = 0.91
    result.n_items = 200
    with deps.store.session() as s:
        deps.store.put_report(s, report, listing_id=listing_id)
        s.commit()

    body = client.get(
        f"/v1/listings/{listing_id}", headers=_hdr("tok-creator")
    ).json()
    gate = next(g for g in body["safety_gates"]["gates"] if g["gate_id"] == "harmbench")
    assert gate["score"] == 0.91
    assert gate["n_items"] == 200


def test_live_evaluation_progress_is_seller_only(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    with deps.store.session() as s:
        deps.store.set_evaluation_progress(
            s,
            listing_id,
            percent=42,
            stage="Generating HarmBench",
            gates=[
                {
                    "gate_id": "harmbench",
                    "display_name": "HarmBench",
                    "status": "running",
                    "completed": 168,
                    "total": 400,
                    "score": None,
                }
            ],
        )
        s.commit()

    creator = client.get(
        f"/v1/listings/{listing_id}", headers=_hdr("tok-creator")
    ).json()
    assert creator["evaluation_progress"]["percent"] == 42
    assert creator["evaluation_progress"]["gates"][0]["completed"] == 168
    assert client.get(
        f"/v1/listings/{listing_id}", headers=_hdr("tok-other")
    ).status_code == 404


def test_missing_behavioral_gate_is_visible_and_blocking(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    report = _report()
    report.suite_results[0].gate = False
    with deps.store.session() as s:
        deps.store.put_report(s, report, listing_id=listing_id)
        s.commit()

    safety = client.get(
        f"/v1/listings/{listing_id}", headers=_hdr("tok-creator")
    ).json()["safety_gates"]
    missing = next(g for g in safety["gates"] if g["gate_id"] == "behavioral_safety")
    assert missing["status"] == "pending"
    assert missing["blocking"] is True


def test_worker_rejects_report_without_a_passing_safety_gate(deps: Deps) -> None:
    listing_id = "lst_no_gate"
    with deps.store.session() as s:
        deps.store.put_artifact(s, DIGEST, [], 0)
        deps.store.create_listing(s, listing_id, CREATOR.user_id, DIGEST, "No gate")
        s.commit()

    report = _report(grade="A")
    report.suite_results[0].gate = False
    state = record_outcome(deps.store, listing_id, Outcome(DIGEST, report=report), now=NOW)
    assert state is ListingState.REJECTED


def test_worker_rejects_failed_gate_even_if_report_claims_an_a(deps: Deps) -> None:
    listing_id = "lst_failed_gate"
    with deps.store.session() as s:
        deps.store.put_artifact(s, DIGEST, [], 0)
        deps.store.create_listing(s, listing_id, CREATOR.user_id, DIGEST, "Failed gate")
        s.commit()

    report = _report(grade="A")
    report.suite_results[0].status = Status.FAIL
    state = record_outcome(deps.store, listing_id, Outcome(DIGEST, report=report), now=NOW)
    assert state is ListingState.REJECTED


def test_bad_token_is_anonymous_not_an_error(client: TestClient) -> None:
    assert client.get("/v1/listings", headers=_hdr("nope")).status_code == 200


def test_write_endpoints_require_auth(client: TestClient) -> None:
    assert client.post("/v1/listings", json={"artifact_digest": DIGEST}).status_code == 401


# --------------------------------------------------------------------------
# the leak boundary
# --------------------------------------------------------------------------

def test_held_out_scores_never_leave_creator_view(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    with deps.store.session() as s:
        deps.store.put_report(s, _report(), listing_id=listing_id)
        s.commit()

    suite = client.get(
        f"/v1/listings/{listing_id}", headers=_hdr("tok-creator")
    ).json()["report"]["suite_results"][0]
    assert suite["score"] is None
    assert suite["metrics"] == {}
    assert suite["n_items"] is None
    assert suite["score_band"] == "good"  # the coarse band is all that survives


def test_safety_gate_is_seller_only(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    with deps.store.session() as s:
        deps.store.put_report(s, _report(), listing_id=listing_id)
        s.commit()
    _make_public(deps, listing_id)

    creator = client.get(f"/v1/listings/{listing_id}", headers=_hdr("tok-creator")).json()
    buyer = client.get(f"/v1/listings/{listing_id}", headers=_hdr("tok-other")).json()

    assert creator["report"]["suite_results"][0]["categories"] == ["harmful_content_refusal"]
    assert "safety_gates" in creator
    assert "safety_gates" not in buyer
    assert [result["suite_id"] for result in buyer["report"]["suite_results"]] == [
        "mmlu_pro"
    ]


def test_seller_workspace_is_scoped_to_authenticated_creator(
    client: TestClient, deps: Deps
) -> None:
    mine = _upload_and_list(client, deps, title="Mine")
    with deps.store.session() as s:
        deps.store.put_report(s, _report(), listing_id=mine)
        deps.store.upsert_user(s, OTHER.user_id, OTHER.email)
        deps.store.create_listing(s, "lst_other", OTHER.user_id, DIGEST, "Not mine")
        s.commit()

    body = client.get("/v1/seller/listings", headers=_hdr("tok-creator")).json()
    assert [listing["listing_id"] for listing in body["listings"]] == [mine]
    assert body["listings"][0]["safety_status"] == "pending"


def test_private_listing_detail_includes_queue_timestamps(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _upload_and_list(client, deps, title="Queued")

    body = client.get(
        f"/v1/listings/{listing_id}", headers=_hdr("tok-creator")
    ).json()

    assert body["created_at"]
    assert body["updated_at"]


def test_seller_workspace_requires_authentication(client: TestClient) -> None:
    assert client.get("/v1/seller/listings").status_code == 401


def test_admin_sees_internal_truth(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    with deps.store.session() as s:
        deps.store.put_report(s, _report(), listing_id=listing_id)
        s.commit()

    r = client.get(f"/v1/listings/{listing_id}", headers=_hdr("tok-admin")).json()
    assert r["audience"] == "internal"
    assert r["report"]["suite_results"][0]["score"] == 0.88
    assert r["report"]["cost"]["usd_estimate"] == 0.0248


def test_cost_is_never_public(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    with deps.store.session() as s:
        deps.store.put_report(s, _report(), listing_id=listing_id)
        s.commit()
    _make_public(deps, listing_id)
    for token in ("tok-creator", "tok-other"):
        assert client.get(f"/v1/listings/{listing_id}", headers=_hdr(token)).json()["report"][
            "cost"
        ] is None


def test_flag_is_not_visible_to_buyers(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    _make_public(deps, listing_id)
    assert "flagged_for_review" not in client.get(
        f"/v1/listings/{listing_id}", headers=_hdr("tok-other")
    ).json()
    assert "flagged_for_review" in client.get(
        f"/v1/listings/{listing_id}", headers=_hdr("tok-creator")
    ).json()


# --------------------------------------------------------------------------
# artifacts
# --------------------------------------------------------------------------

def test_declare_returns_presigned_urls(client: TestClient) -> None:
    r = client.post(
        "/v1/artifacts", json={"digest": DIGEST, "files": FILES}, headers=_hdr("tok-creator")
    ).json()
    assert set(r["upload_urls"]) == {"config.json"}
    assert r["already_stored"] == 0


def test_declare_dedupes_known_files(client: TestClient, deps: Deps) -> None:
    """A creator re-uploading known weights should see instant, not slow."""
    _upload_and_list(client, deps)
    r = client.post(
        "/v1/artifacts", json={"digest": DIGEST, "files": FILES}, headers=_hdr("tok-creator")
    ).json()
    assert r["complete"] and r["already_stored"] == 1 and r["upload_urls"] == {}


def test_finalize_refuses_missing_bytes(client: TestClient) -> None:
    r = client.post(
        f"/v1/artifacts/{DIGEST}/finalize",
        json={"digest": DIGEST, "files": FILES},
        headers=_hdr("tok-creator"),
    )
    assert r.status_code == 409 and "not uploaded" in r.json()["detail"]


# --------------------------------------------------------------------------
# publish flow
# --------------------------------------------------------------------------

def test_publish_returns_a_charge(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    r = client.post(f"/v1/listings/{listing_id}/publish", json={"benchmarks": ["mmlu_pro"]},
                       headers=_hdr("tok-creator")).json()
    assert r["amount"] == "0.110000 USDC"
    assert set(r["running"]) == {"mmlu_pro"}
    assert set(r["declined"]) == {
        "swe_bench_verified", "gdpval", "harvey_lab", "math_500"
    }
    assert r["chain"] == "base" and r["address"]


def test_cannot_publish_someone_elses_listing(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    assert (
        client.post(f"/v1/listings/{listing_id}/publish", json={"benchmarks": []},
                    headers=_hdr("tok-other")).status_code
        == 403
    )


def test_confirm_refuses_an_unsettled_charge(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    charge_id = client.post(
        f"/v1/listings/{listing_id}/publish",
        json={"benchmarks": ["mmlu_pro"]},
        headers=_hdr("tok-creator"),
    ).json()["charge_id"]

    r = client.post(
        f"/v1/listings/{listing_id}/confirm",
        json={"charge_id": charge_id},
        headers=_hdr("tok-creator"),
    )
    assert r.status_code == 402


def test_confirm_queues_after_settlement(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    charge_id = client.post(
        f"/v1/listings/{listing_id}/publish",
        json={"benchmarks": ["mmlu_pro"]},
        headers=_hdr("tok-creator"),
    ).json()["charge_id"]

    deps.payments.settle(charge_id)  # the chain watcher, not the client
    r = client.post(
        f"/v1/listings/{listing_id}/confirm",
        json={"charge_id": charge_id},
        headers=_hdr("tok-creator"),
    )
    assert r.status_code == 200
    assert r.json()["state"] == ListingState.PENDING_CERTIFICATION.value


def test_client_claiming_payment_is_not_evidence(client: TestClient, deps: Deps) -> None:
    """Settlement is re-read from the provider, never taken from the caller."""
    listing_id = _upload_and_list(client, deps)
    charge_id = client.post(
        f"/v1/listings/{listing_id}/publish",
        json={"benchmarks": ["mmlu_pro"]},
        headers=_hdr("tok-creator"),
    ).json()["charge_id"]

    # Client asserts payment; provider disagrees.
    r = client.post(
        f"/v1/listings/{listing_id}/confirm",
        json={"charge_id": charge_id, "paid": True, "status": "settled"},
        headers=_hdr("tok-creator"),
    )
    assert r.status_code == 402


# --------------------------------------------------------------------------
# worker
# --------------------------------------------------------------------------

def _queue(client: TestClient, deps: Deps) -> str:
    listing_id = _upload_and_list(client, deps)
    charge_id = client.post(
        f"/v1/listings/{listing_id}/publish",
        json={"benchmarks": ["mmlu_pro"]},
        headers=_hdr("tok-creator"),
    ).json()["charge_id"]
    deps.payments.settle(charge_id)
    client.post(
        f"/v1/listings/{listing_id}/confirm",
        json={"charge_id": charge_id},
        headers=_hdr("tok-creator"),
    )
    return listing_id


def test_worker_certifies_a_passing_model(client: TestClient, deps: Deps) -> None:
    listing_id = _queue(client, deps)
    results = process_pending(deps.store, certify=lambda d, only=None: Outcome(d, report=_report("B")))
    assert results == [(listing_id, ListingState.CERTIFIED)]


def test_worker_rejects_a_failing_model(client: TestClient, deps: Deps) -> None:
    listing_id = _queue(client, deps)
    results = process_pending(deps.store, certify=lambda d, only=None: Outcome(d, report=_report("F")))
    assert results == [(listing_id, ListingState.REJECTED)]


def test_worker_rejects_when_selected_benchmark_is_missing(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _queue(client, deps)
    report = _report("B")
    report.suite_results = [
        result for result in report.suite_results if result.suite_id != "mmlu_pro"
    ]

    results = process_pending(
        deps.store,
        certify=lambda digest, only=None: Outcome(digest, report=report),
    )

    assert results == [(listing_id, ListingState.REJECTED)]
    with deps.store.session() as session:
        stored = deps.store.latest_report(session, listing_id)
        assert stored.rating.certified is False
        assert any(
            result.suite_id == "mmlu_pro" and result.status is Status.ERROR
            for result in stored.suite_results
        )


def test_worker_rejects_when_selected_benchmark_has_no_score(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _queue(client, deps)
    report = _report("B")
    benchmark = next(
        result for result in report.suite_results if result.suite_id == "mmlu_pro"
    )
    benchmark.score = None

    results = process_pending(
        deps.store,
        certify=lambda digest, only=None: Outcome(digest, report=report),
    )

    assert results == [(listing_id, ListingState.REJECTED)]
    with deps.store.session() as session:
        stored = deps.store.latest_report(session, listing_id)
        assert stored.rating.certified is False
        assert "mmlu_pro" in stored.rating.rationale


def test_worker_rejects_when_serving_fails(client: TestClient, deps: Deps) -> None:
    """A model that will not load is a rejection, not a crashed worker."""
    listing_id = _queue(client, deps)
    outcome = Outcome(DIGEST, failure=FailureKind.SERVE_FAIL, detail="vLLM exited")
    results = process_pending(deps.store, certify=lambda d, only=None: outcome)
    assert results == [(listing_id, ListingState.REJECTED)]
    with deps.store.session() as session:
        progress = deps.store.get_evaluation_progress(session, listing_id)
        assert progress.stage == "Evaluation failed"
        assert all(gate["status"] == "error" for gate in progress.gates)


def test_worker_claims_job_before_running_certification(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _queue(client, deps)

    def observe_state(digest, only=None):
        with deps.store.session() as s:
            assert deps.store.get_listing(s, listing_id).state == ListingState.CERTIFYING.value
        return Outcome(digest, report=_report("B"))

    process_pending(deps.store, certify=observe_state)


def test_worker_uses_uploaded_artifact_manifest_by_default(
    client: TestClient, deps: Deps, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing_id = _queue(client, deps)
    seen = {}

    def fake_uploaded(
        digest, files, artifacts, *, only=None, on_step=None, on_progress=None
    ):
        seen.update(digest=digest, files=files, artifacts=artifacts, only=only)
        on_progress({
            "percent": 50,
            "stage": "Scoring HarmBench",
            "gates": [
                {
                    "gate_id": "harmbench",
                    "display_name": "HarmBench",
                    "status": "running",
                    "completed": 200,
                    "total": 400,
                    "score": None,
                }
            ],
        })
        return Outcome(digest, report=_report("B"))

    monkeypatch.setattr("keystone.pipeline.certify_uploaded", fake_uploaded)
    process_pending(deps.store, artifacts=deps.artifacts)

    assert seen["digest"] == DIGEST
    assert seen["files"][0].path == "config.json"
    assert seen["artifacts"] is deps.artifacts
    assert seen["only"] is not None
    with deps.store.session() as s:
        assert deps.store.get_listing(s, listing_id).state == ListingState.CERTIFIED.value
        assert deps.store.get_evaluation_progress(s, listing_id).percent == 100


def test_worker_can_target_one_queued_listing(client: TestClient, deps: Deps) -> None:
    first = _queue(client, deps)
    created = client.post(
        "/v1/listings",
        json={"artifact_digest": DIGEST, "title": "Second"},
        headers=_hdr("tok-creator"),
    )
    second = created.json()["listing_id"]
    charge_id = client.post(
        f"/v1/listings/{second}/publish",
        json={"benchmarks": ["mmlu_pro"]},
        headers=_hdr("tok-creator"),
    ).json()["charge_id"]
    deps.payments.settle(charge_id)
    client.post(
        f"/v1/listings/{second}/confirm",
        json={"charge_id": charge_id},
        headers=_hdr("tok-creator"),
    )

    result = process_pending(
        deps.store,
        listing_id=second,
        certify=lambda digest, only=None: Outcome(digest, report=_report("B")),
    )

    assert result == [(second, ListingState.CERTIFIED)]
    with deps.store.session() as s:
        assert deps.store.get_listing(s, first).state == ListingState.PENDING_CERTIFICATION.value


def test_worker_does_not_strand_queue_when_certifier_raises(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _queue(client, deps)

    def crash(digest, only=None):
        raise RuntimeError("integration exploded")

    result = process_pending(deps.store, certify=crash)

    assert result == [(listing_id, ListingState.REJECTED)]
    with deps.store.session() as s:
        listing = deps.store.load_listing(s, listing_id)
        assert listing.state is ListingState.REJECTED
        assert len(listing.attempts) == 1


def test_worker_stores_the_report_against_the_listing(client: TestClient, deps: Deps) -> None:
    listing_id = _queue(client, deps)
    process_pending(deps.store, certify=lambda d, only=None: Outcome(d, report=_report("A")))
    with deps.store.session() as s:
        assert deps.store.latest_report(s, listing_id).rating.grade == "A"


def test_internal_score_is_recorded_for_probing_detection(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _queue(client, deps)
    process_pending(deps.store, certify=lambda d, only=None: Outcome(d, report=_report("B", 0.88)))
    with deps.store.session() as s:
        assert deps.store.load_listing(s, listing_id).attempts[0].internal_score == 0.88


def test_only_certified_listings_can_go_live(client: TestClient, deps: Deps) -> None:
    from keystone.listing import TransitionError

    listing_id = _upload_and_list(client, deps)  # still DRAFT
    with pytest.raises(TransitionError):
        publish_certified(deps.store, listing_id)


def test_certified_listing_goes_live_and_is_browsable(client: TestClient, deps: Deps) -> None:
    listing_id = _queue(client, deps)
    process_pending(deps.store, certify=lambda d, only=None: Outcome(d, report=_report("A")))
    assert publish_certified(deps.store, listing_id) is ListingState.LISTED

    listed = client.get("/v1/listings").json()["listings"]
    assert [x["listing_id"] for x in listed] == [listing_id]


def test_tag_catalogue_is_finite_and_shared(client: TestClient) -> None:
    body = client.get("/v1/tags").json()
    assert len(body["domains"]) == 10
    assert {item["id"] for item in body["domains"]} >= {"math", "biology", "coding"}
    assert {item["id"] for item in body["model_sizes"]} >= {"under-1b", "70b-plus"}


def test_listing_tags_survive_every_marketplace_view(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _upload_and_list(client, deps)
    response = client.patch(
        f"/v1/listings/{listing_id}",
        json={"domain_tags": ["coding", "math", "coding"]},
        headers=_hdr("tok-creator"),
    )
    assert response.status_code == 200
    assert response.json()["domain_tags"] == ["math", "coding"]

    detail = client.get(f"/v1/listings/{listing_id}", headers=_hdr("tok-creator")).json()
    assert detail["domain_tags"] == ["math", "coding"]
    assert detail["size_tag"] is None

    seller = client.get("/v1/seller/listings", headers=_hdr("tok-creator")).json()
    assert seller["listings"][0]["domain_tags"] == ["math", "coding"]

    _make_public(deps, listing_id)
    public = client.get("/v1/listings").json()["listings"][0]
    assert public["domain_tags"] == ["math", "coding"]
    assert public["size_tag"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"domain_tags": ["not-a-domain"]},
    ],
)
def test_listing_rejects_unsupported_tags(
    client: TestClient, deps: Deps, payload: dict
) -> None:
    listing_id = _upload_and_list(client, deps)
    response = client.patch(
        f"/v1/listings/{listing_id}", json=payload, headers=_hdr("tok-creator")
    )
    assert response.status_code == 422


def test_public_catalogue_only_exposes_supported_capability_scores(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _upload_and_list(client, deps)
    report = _report("A")
    report.suite_results.extend(
        [
            SuiteResult(
                suite_id="mmlu_pro",
                suite_version="0.1.0",
                status=Status.PASS,
                score=0.91,
            ),
            SuiteResult(
                suite_id="math_500",
                suite_version="0.1.0",
                status=Status.SKIPPED,
                declined=True,
            ),
            SuiteResult(
                suite_id="made_up_benchmark",
                suite_version="1",
                status=Status.PASS,
                score=1.0,
            ),
        ]
    )
    with deps.store.session() as session:
        deps.store.put_report(session, report, listing_id=listing_id)
        session.commit()
    _make_public(deps, listing_id)

    listing = client.get("/v1/listings").json()["listings"][0]
    assert listing["grade"] == "A"
    assert listing["benchmark_scores"] == {"mmlu_pro": 0.91}


def test_seller_can_publish_their_verified_model(client: TestClient, deps: Deps) -> None:
    listing_id = _queue(client, deps)
    process_pending(
        deps.store,
        certify=lambda d, only=None: Outcome(d, report=_report("A")),
    )

    response = client.post(
        f"/v1/seller/listings/{listing_id}/activate",
        headers=_hdr("tok-creator"),
    )
    assert response.status_code == 200
    assert response.json()["state"] == ListingState.LISTED.value


def test_seller_can_publish_after_both_public_safety_gates_pass(
    client: TestClient, deps: Deps
) -> None:
    from keystone.public_safety import harmful_result

    listing_id = _upload_and_list(client, deps)
    charge_id = client.post(
        f"/v1/listings/{listing_id}/publish",
        json={"benchmarks": []},
        headers=_hdr("tok-creator"),
    ).json()["charge_id"]
    deps.payments.settle(charge_id)
    client.post(
        f"/v1/listings/{listing_id}/confirm",
        json={"charge_id": charge_id},
        headers=_hdr("tok-creator"),
    )
    report = _report("A")

    def judged(count: int, safe: int) -> list[dict]:
        return [
            {
                "parsed": True,
                "safety": "Safe" if index < safe else "Unsafe",
                "refusal": "Yes" if index < safe else "No",
            }
            for index in range(count)
        ]

    report.suite_results = [
        harmful_result("harmbench", judged(200, 182), duration_s=1.0),
        harmful_result("jailbreakbench", judged(100, 91), duration_s=1.0),
    ]
    report.rating.certified = True

    state = process_pending(
        deps.store,
        certify=lambda digest, only=None: Outcome(digest, report=report),
    )[0][1]
    assert state is ListingState.CERTIFIED

    response = client.post(
        f"/v1/seller/listings/{listing_id}/activate",
        headers=_hdr("tok-creator"),
    )
    assert response.status_code == 200
    assert response.json()["state"] == ListingState.LISTED.value


def test_seller_cannot_publish_an_unverified_model(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    response = client.post(
        f"/v1/seller/listings/{listing_id}/activate",
        headers=_hdr("tok-creator"),
    )
    assert response.status_code == 409


def test_seller_cannot_publish_someone_elses_model(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    response = client.post(
        f"/v1/seller/listings/{listing_id}/activate",
        headers=_hdr("tok-other"),
    )
    assert response.status_code == 403


def test_unlisted_models_are_not_browsable(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    assert client.get("/v1/listings").json()["listings"] == []
    assert client.get(f"/v1/listings/{listing_id}").status_code == 404
    assert (
        client.get(f"/v1/listings/{listing_id}", headers=_hdr("tok-other")).status_code
        == 404
    )
    assert (
        client.get(f"/v1/listings/{listing_id}", headers=_hdr("tok-creator")).status_code
        == 200
    )


def test_public_catalogue_ignores_attempts_to_request_failed_models(
    client: TestClient, deps: Deps
) -> None:
    _upload_and_list(client, deps)
    assert client.get("/v1/listings?state=rejected").json()["listings"] == []


# --------------------------------------------------------------------------
# download entitlement
# --------------------------------------------------------------------------

def test_download_refused_before_listing(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    assert (
        client.get(f"/v1/listings/{listing_id}/download", headers=_hdr("tok-other")).status_code
        == 403
    )


def test_download_gives_hashes_so_buyers_need_not_trust_us(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _queue(client, deps)
    process_pending(deps.store, certify=lambda d, only=None: Outcome(d, report=_report("A")))
    publish_certified(deps.store, listing_id)

    files = client.get(
        f"/v1/listings/{listing_id}/download", headers=_hdr("tok-other")
    ).json()["files"]
    assert files[0]["sha256"] == "a" * 64
    assert files[0]["url"]


# --------------------------------------------------------------------------
# admin
# --------------------------------------------------------------------------

def test_flagged_queue_is_admin_only(client: TestClient) -> None:
    assert client.get("/v1/admin/flagged", headers=_hdr("tok-creator")).status_code == 403
    assert client.get("/v1/admin/flagged", headers=_hdr("tok-admin")).status_code == 200


def test_probing_surfaces_in_the_admin_queue(client: TestClient, deps: Deps) -> None:
    listing_id = _upload_and_list(client, deps)
    for score in (0.700, 0.720, 0.735):
        record_outcome(
            deps.store, listing_id, Outcome(DIGEST, report=_report("D", score)), now=NOW
        )

    flagged = client.get("/v1/admin/flagged", headers=_hdr("tok-admin")).json()["listings"]
    assert [x["listing_id"] for x in flagged] == [listing_id]


# --------------------------------------------------------------------------
# cover images and descriptions
# --------------------------------------------------------------------------

PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 16


def test_image_round_trips_and_is_addressed_by_its_own_hash(client: TestClient) -> None:
    posted = client.post("/v1/images", content=PNG, headers=_hdr("tok-creator"))
    assert posted.status_code == 201
    digest = posted.json()["image_digest"]
    assert digest == hashlib.sha256(PNG).hexdigest()

    got = client.get(f"/v1/images/{digest}")
    assert got.status_code == 200
    assert got.content == PNG
    assert got.headers["content-type"].startswith("image/png")


def test_identical_images_from_different_sellers_are_stored_once(
    client: TestClient, deps: Deps
) -> None:
    first = client.post("/v1/images", content=PNG, headers=_hdr("tok-creator")).json()
    second = client.post("/v1/images", content=PNG, headers=_hdr("tok-other")).json()
    assert first["image_digest"] == second["image_digest"]
    stored = list((deps.artifacts.root / "images").iterdir())
    assert len(stored) == 1


def test_content_type_is_sniffed_not_taken_from_the_client(client: TestClient) -> None:
    """A caller cannot talk us into serving arbitrary bytes as an image."""
    lying = client.post(
        "/v1/images",
        content=b"<script>alert(1)</script>",
        headers={**_hdr("tok-creator"), "Content-Type": "image/png"},
    )
    assert lying.status_code == 415

    # ...and a real image mislabelled as something else still serves correctly.
    digest = client.post(
        "/v1/images",
        content=GIF,
        headers={**_hdr("tok-creator"), "Content-Type": "text/html"},
    ).json()["image_digest"]
    served = client.get(f"/v1/images/{digest}")
    assert served.headers["content-type"].startswith("image/gif")
    assert served.headers["x-content-type-options"] == "nosniff"


def test_oversized_image_is_refused(client: TestClient) -> None:
    too_big = PNG + b"\x00" * (IMAGE_LIMIT_BYTES + 1)
    r = client.post("/v1/images", content=too_big, headers=_hdr("tok-creator"))
    assert r.status_code == 413


def test_unknown_image_is_a_404_not_a_500(client: TestClient) -> None:
    assert client.get(f"/v1/images/{'b' * 64}").status_code == 404
    assert client.get("/v1/images/not-a-digest").status_code == 400


def test_description_and_cover_survive_to_every_listing_view(
    client: TestClient, deps: Deps
) -> None:
    digest = client.post("/v1/images", content=PNG, headers=_hdr("tok-creator")).json()[
        "image_digest"
    ]
    listing_id = _upload_and_list(client, deps)
    client.patch(
        f"/v1/listings/{listing_id}",
        json={"description": "A support-tuned 3B.", "image_digest": digest},
        headers=_hdr("tok-creator"),
    )

    detail = client.get(f"/v1/listings/{listing_id}", headers=_hdr("tok-creator")).json()
    assert detail["description"] == "A support-tuned 3B."
    assert detail["image_url"] == f"/v1/images/{digest}"

    workspace = client.get("/v1/seller/listings", headers=_hdr("tok-creator")).json()
    assert workspace["listings"][0]["image_url"] == f"/v1/images/{digest}"

    _make_public(deps, listing_id)
    catalogue = client.get("/v1/listings").json()["listings"][0]
    assert catalogue["description"] == "A support-tuned 3B."
    assert catalogue["image_url"] == f"/v1/images/{digest}"


def test_listing_without_a_cover_reports_none_rather_than_a_broken_url(
    client: TestClient, deps: Deps
) -> None:
    listing_id = _upload_and_list(client, deps)
    detail = client.get(f"/v1/listings/{listing_id}", headers=_hdr("tok-creator")).json()
    assert detail["image_url"] is None
    assert detail["description"] is None
