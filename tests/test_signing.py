"""Signing and JWT auth tests.

A rating nobody can verify is a screenshot. These tests are about the property
that makes a report evidence: any edit to any field breaks the signature.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from keystone.auth import JWTAuth, Principal
from keystone.schema import (
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
from keystone.signing import Ed25519Signer, canonical_payload, verify, write_public_key

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _report(grade: str = "B", digest: str = "d" * 64) -> CertificationReport:
    return CertificationReport(
        report_id="rep_1",
        created_at=NOW,
        status=Status.PASS,
        subject=Subject(
            source=Source(kind=SourceKind.UPLOAD, ref="rcpt"),
            artifact_digest=digest,
            files=[],
            total_bytes=0,
        ),
        environment=Environment(sandboxed=True, seed=0, engine_version="0.28.0"),
        suite_results=[
            SuiteResult(suite_id="s", suite_version="1", status=Status.PASS, score=0.9)
        ],
        cost=Cost(gpu_seconds=81.0),
        rating=Rating(grade=grade, as_tested_at=NOW),
    )


@pytest.fixture
def signer() -> Ed25519Signer:
    return Ed25519Signer.generate()


# --------------------------------------------------------------------------
# canonicalisation -- the part that actually decides whether this works
# --------------------------------------------------------------------------

def test_payload_is_stable_across_equal_reports() -> None:
    """Two honest parties must serialise the same report identically."""
    assert canonical_payload(_report()) == canonical_payload(_report())


def test_payload_ignores_the_signature_field(signer: Ed25519Signer) -> None:
    report = _report()
    signed = signer.sign_report(report)
    assert canonical_payload(report) == canonical_payload(signed)


def test_payload_keys_are_sorted() -> None:
    data = json.loads(canonical_payload(_report()))
    assert list(data) == sorted(data)


# --------------------------------------------------------------------------
# signing
# --------------------------------------------------------------------------

def test_signed_report_verifies(signer: Ed25519Signer) -> None:
    signed = signer.sign_report(_report())
    assert signed.signature is not None
    assert signed.signature.algorithm == "ed25519"
    assert verify(signed, signer.public_key_bytes())


def test_signing_does_not_mutate_the_input(signer: Ed25519Signer) -> None:
    report = _report()
    signer.sign_report(report)
    assert report.signature is None


def test_unsigned_report_does_not_verify(signer: Ed25519Signer) -> None:
    assert not verify(_report(), signer.public_key_bytes())


def test_another_key_does_not_verify(signer: Ed25519Signer) -> None:
    signed = signer.sign_report(_report())
    assert not verify(signed, Ed25519Signer.generate().public_key_bytes())


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: setattr(r.rating, "grade", "A"), id="bumped_grade"),
        pytest.param(
            lambda r: setattr(r.subject, "artifact_digest", "e" * 64), id="swapped_artifact"
        ),
        pytest.param(
            lambda r: setattr(r.environment, "engine_version", "0.29.0"), id="engine_version"
        ),
        pytest.param(lambda r: setattr(r.suite_results[0], "score", 1.0), id="raised_score"),
        pytest.param(lambda r: setattr(r.environment, "sandboxed", False), id="sandbox_flag"),
        pytest.param(lambda r: setattr(r, "report_id", "rep_2"), id="report_id"),
    ],
)
def test_any_edit_breaks_the_signature(signer: Ed25519Signer, mutate) -> None:
    """This is the whole point: a tampered report is detectably tampered."""
    signed = signer.sign_report(_report())
    assert verify(signed, signer.public_key_bytes())
    mutate(signed)
    assert not verify(signed, signer.public_key_bytes())


def test_forged_signature_fails(signer: Ed25519Signer) -> None:
    signed = signer.sign_report(_report())
    signed.signature.value = base64.b64encode(b"\x00" * 64).decode()
    assert not verify(signed, signer.public_key_bytes())


def test_key_id_is_derived_from_the_public_key(signer: Ed25519Signer) -> None:
    assert signer.key_id.startswith("ed25519:")
    restored = Ed25519Signer(base64.b64decode(signer.private_key_b64()))
    assert restored.key_id == signer.key_id


def test_key_survives_a_round_trip_through_env(signer: Ed25519Signer, monkeypatch) -> None:
    """Production sets KEYSTONE_SIGNING_KEY so reports stay verifiable across restarts."""
    monkeypatch.setenv("KEYSTONE_SIGNING_KEY", signer.private_key_b64())
    restored = Ed25519Signer.from_env()
    signed = signer.sign_report(_report())
    assert restored is not None
    assert verify(signed, restored.public_key_bytes())


def test_from_env_is_none_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("KEYSTONE_SIGNING_KEY", raising=False)
    assert Ed25519Signer.from_env() is None


def test_published_key_is_usable(signer: Ed25519Signer, tmp_path) -> None:
    path = write_public_key(signer, tmp_path / "keystone.pub.json")
    published = json.loads(path.read_text())
    signed = signer.sign_report(_report())
    assert verify(signed, base64.b64decode(published["public_key"]))


# --------------------------------------------------------------------------
# worker integration
# --------------------------------------------------------------------------

def test_worker_stores_a_verifiable_report(tmp_path, signer: Ed25519Signer) -> None:
    from keystone.db import Store
    from keystone.pipeline import Outcome
    from keystone.worker import record_outcome

    store = Store("sqlite://")
    store.create_all()
    with store.session() as s:
        store.upsert_user(s, "c1", "c@example.com")
        store.put_artifact(s, "d" * 64, [], 0)
        store.create_listing(s, "l1", "c1", "d" * 64)
        s.commit()

    record_outcome(store, "l1", Outcome("d" * 64, report=_report()), signer=signer, now=NOW)

    with store.session() as s:
        stored = store.latest_report(s, "l1")

    assert verify(stored, signer.public_key_bytes())


# --------------------------------------------------------------------------
# JWT auth
# --------------------------------------------------------------------------

@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks_auth(rsa_key, monkeypatch, **kw) -> JWTAuth:
    """JWTAuth with the JWKS fetch stubbed out -- no network in tests."""
    auth = JWTAuth("https://idp.invalid/.well-known/jwks.json", "https://idp.invalid",
                   "keystone", **kw)

    class _Key:
        key = rsa_key.public_key()

    monkeypatch.setattr(auth._jwks, "get_signing_key_from_jwt", lambda _t: _Key())
    return auth


def _token(rsa_key, **overrides) -> str:
    claims = {
        "sub": "user_123",
        "email": "someone@example.com",
        "iss": "https://idp.invalid",
        "aud": "keystone",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    claims.update(overrides)
    pem = rsa_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    return jwt.encode(claims, pem, algorithm="RS256")


def test_valid_token_yields_a_principal(rsa_key, monkeypatch) -> None:
    auth = _jwks_auth(rsa_key, monkeypatch)
    p = auth.principal_for(_token(rsa_key))
    assert p == Principal("user_123", "someone@example.com", is_admin=False)


def test_admin_comes_from_the_provider_claim(rsa_key, monkeypatch) -> None:
    auth = _jwks_auth(rsa_key, monkeypatch)
    assert auth.principal_for(_token(rsa_key, keystone_admin=True)).is_admin


def test_admin_claim_must_be_exactly_true(rsa_key, monkeypatch) -> None:
    """Truthy is not enough -- privilege escalation should need the real value."""
    auth = _jwks_auth(rsa_key, monkeypatch)
    assert not auth.principal_for(_token(rsa_key, keystone_admin="yes")).is_admin


def test_expired_token_is_rejected(rsa_key, monkeypatch) -> None:
    auth = _jwks_auth(rsa_key, monkeypatch)
    stale = _token(rsa_key, exp=datetime.now(timezone.utc) - timedelta(hours=1))
    assert auth.principal_for(stale) is None


def test_wrong_issuer_is_rejected(rsa_key, monkeypatch) -> None:
    auth = _jwks_auth(rsa_key, monkeypatch)
    assert auth.principal_for(_token(rsa_key, iss="https://evil.invalid")) is None


def test_wrong_audience_is_rejected(rsa_key, monkeypatch) -> None:
    auth = _jwks_auth(rsa_key, monkeypatch)
    assert auth.principal_for(_token(rsa_key, aud="someone-else")) is None


def test_unsigned_alg_none_token_is_rejected(rsa_key, monkeypatch) -> None:
    """The classic JWT attack."""
    auth = _jwks_auth(rsa_key, monkeypatch)
    forged = jwt.encode(
        {"sub": "attacker", "iss": "https://idp.invalid", "aud": "keystone",
         "exp": datetime.now(timezone.utc) + timedelta(hours=1), "keystone_admin": True},
        key="", algorithm="none",
    )
    assert auth.principal_for(forged) is None


def test_token_signed_by_another_key_is_rejected(rsa_key, monkeypatch) -> None:
    auth = _jwks_auth(rsa_key, monkeypatch)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert auth.principal_for(_token(other)) is None


def test_garbage_is_anonymous_not_an_exception(rsa_key, monkeypatch) -> None:
    auth = _jwks_auth(rsa_key, monkeypatch)
    assert auth.principal_for("not-a-token") is None


def test_missing_subject_is_rejected(rsa_key, monkeypatch) -> None:
    auth = _jwks_auth(rsa_key, monkeypatch)
    assert auth.principal_for(_token(rsa_key, sub="")) is None
