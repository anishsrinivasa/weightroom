"""Report signing.

A rating nobody can verify is a screenshot. Signing is what lets a buyer --
or a regulator, or a court, a year later -- confirm that the report in their
hands is the one we issued, unmodified, over the exact artifact it names.

Ed25519 over a canonical serialisation of the report with the signature field
excluded. Canonicalisation matters more than the algorithm here: if two honest
parties serialise the same report differently, verification fails and the whole
mechanism is worthless. So the payload is JSON with sorted keys, no whitespace,
and no non-ASCII escaping surprises.

What is signed is the *internal* report. Redacted views are derived, so they do
not verify against this signature -- deliberately. A buyer verifies the digest
and the grade they were shown against a report we publish in full, or they ask
us for the signed original. Signing a redacted view would let anyone with a
creator token mint a differently-redacted "valid" report.
"""

from __future__ import annotations

import abc
import base64
import json
import os
from pathlib import Path

from keystone.schema import CertificationReport, Signature

ALGORITHM = "ed25519"


def canonical_payload(report: CertificationReport) -> bytes:
    """Exact bytes that get signed. Signature field excluded, keys sorted."""
    data = report.model_dump(mode="json", exclude={"signature"})
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class Signer(abc.ABC):
    """Swap in a KMS or HSM without touching callers."""

    @property
    @abc.abstractmethod
    def key_id(self) -> str: ...

    @abc.abstractmethod
    def sign(self, payload: bytes) -> bytes: ...

    @abc.abstractmethod
    def public_key_bytes(self) -> bytes: ...

    def sign_report(self, report: CertificationReport) -> CertificationReport:
        """Return a copy carrying its signature. Never mutates the input."""
        signed = report.model_copy(deep=True)
        signed.signature = None  # sign the unsigned form, always
        raw = self.sign(canonical_payload(signed))
        signed.signature = Signature(
            algorithm=ALGORITHM,
            key_id=self.key_id,
            value=base64.b64encode(raw).decode(),
        )
        return signed


class Ed25519Signer(Signer):
    """Local key. Fine for development; production should use a KMS."""

    def __init__(self, private_key_bytes: bytes, key_id: str | None = None) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        self._key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
        self._key_id = key_id or _fingerprint(self.public_key_bytes())

    @classmethod
    def generate(cls, key_id: str | None = None) -> Ed25519Signer:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
        )

        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        return cls(raw, key_id)

    @classmethod
    def from_env(cls) -> Ed25519Signer | None:
        """KEYSTONE_SIGNING_KEY holds a base64 raw Ed25519 private key."""
        encoded = os.environ.get("KEYSTONE_SIGNING_KEY")
        if not encoded:
            return None
        return cls(base64.b64decode(encoded), os.environ.get("KEYSTONE_SIGNING_KEY_ID"))

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes) -> bytes:
        return self._key.sign(payload)

    def public_key_bytes(self) -> bytes:
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        return self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def private_key_b64(self) -> str:
        """For seeding KEYSTONE_SIGNING_KEY. Never log or transmit this."""
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
        )

        return base64.b64encode(
            self._key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        ).decode()


def _fingerprint(public_key: bytes) -> str:
    import hashlib

    return "ed25519:" + hashlib.sha256(public_key).hexdigest()[:16]


def verify(report: CertificationReport, public_key: bytes) -> bool:
    """True only if this exact report was signed by the holder of that key.

    Any edit to any field -- a bumped grade, a swapped artifact digest, a
    different engine version -- changes the payload and fails verification.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if report.signature is None or report.signature.algorithm != ALGORITHM:
        return False

    unsigned = report.model_copy(deep=True)
    unsigned.signature = None

    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            base64.b64decode(report.signature.value), canonical_payload(unsigned)
        )
        return True
    except (InvalidSignature, ValueError):
        return False


def write_public_key(signer: Signer, path: Path) -> Path:
    """Publish the verification key. This is meant to be public."""
    path.write_text(
        json.dumps(
            {
                "algorithm": ALGORITHM,
                "key_id": signer.key_id,
                "public_key": base64.b64encode(signer.public_key_bytes()).decode(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
