"""Configuration: environment in, wired application out.

Development defaults are convenient and completely unsafe. Static tokens are
guessable, the demo payment provider settles on request, local disk vanishes
when a container restarts, and an ephemeral signing key silently invalidates
every report issued before the last deploy.

So production does not fall back to any of them. `KEYSTONE_ENV=production`
refuses to boot with a dev default in place and names the variable to set. A
platform whose whole product is verification cannot ship with a payment
provider that anyone can talk into settling.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from keystone.api import Deps, create_app
from keystone.auth import Authenticator, JWTAuth, Principal, StaticTokenAuth
from keystone.db import Store
from keystone.payments import DemoChainProvider, PaymentProvider
from keystone.signing import Ed25519Signer, Signer
from keystone.storage import ArtifactStore, LocalStore, S3Store


class ConfigError(RuntimeError):
    """Something required in production is missing or is a dev stand-in."""


def _flag(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    env: str = "development"

    database_url: str = "sqlite:///keystone.db"
    bucket: str = ""
    signing_key_b64: str = ""

    jwks_url: str = ""
    jwt_issuer: str = ""
    jwt_audience: str = ""

    # Comma-separated token:user:email[:admin] triples. Development only.
    dev_tokens: str = "dev-creator:u_creator:creator@example.com,dev-admin:u_admin:admin@example.com:admin"

    chain: str = "base"
    block_time_s: float = 1.5
    confirmations: int = 3

    @property
    def is_production(self) -> bool:
        return self.env.lower() in ("production", "prod")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=_flag("KEYSTONE_ENV", "development"),
            database_url=_flag("DATABASE_URL", "sqlite:///keystone.db"),
            bucket=_flag("KEYSTONE_BUCKET") or _flag("BUCKET_NAME"),
            signing_key_b64=_flag("KEYSTONE_SIGNING_KEY"),
            jwks_url=_flag("KEYSTONE_JWKS_URL"),
            jwt_issuer=_flag("KEYSTONE_JWT_ISSUER"),
            jwt_audience=_flag("KEYSTONE_JWT_AUDIENCE"),
            dev_tokens=_flag("KEYSTONE_DEV_TOKENS", cls.dev_tokens),
            chain=_flag("KEYSTONE_CHAIN", "base"),
        )


# --------------------------------------------------------------------------
# component builders -- each returns (component, is_dev_stand_in)
# --------------------------------------------------------------------------

def build_store(s: Settings) -> tuple[Store, bool]:
    url = s.database_url
    # SQLAlchemy needs the driver named; Neon and friends hand out bare
    # postgres:// URLs.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return Store(url), url.startswith("sqlite")


def build_artifacts(s: Settings) -> tuple[ArtifactStore, bool]:
    if s.bucket:
        return S3Store(s.bucket), False
    # base_url makes browser uploads work against the local dev receiver.
    return LocalStore(Path(".keystone-store"), base_url="/v1/dev-upload"), True


def build_signer(s: Settings) -> tuple[Signer, bool]:
    if s.signing_key_b64:
        return Ed25519Signer.from_env(), False
    # A fresh key per boot means every previously issued report stops
    # verifying. Fine locally, catastrophic in production.
    return Ed25519Signer.generate(), True


def build_auth(s: Settings) -> tuple[Authenticator, bool]:
    if s.jwks_url and s.jwt_issuer and s.jwt_audience:
        return JWTAuth(s.jwks_url, s.jwt_issuer, s.jwt_audience), False

    tokens: dict[str, Principal] = {}
    for entry in filter(None, (e.strip() for e in s.dev_tokens.split(","))):
        parts = entry.split(":")
        if len(parts) < 3:
            continue
        token, user_id, email, *rest = parts
        tokens[token] = Principal(user_id, email, is_admin="admin" in rest)
    return StaticTokenAuth(tokens), True


def build_payments(s: Settings) -> tuple[PaymentProvider, bool]:
    """A real processor when configured, the simulated chain otherwise."""
    from keystone.providers.coinbase_commerce import from_env as coinbase_from_env
    from keystone.providers.hosted_checkout import from_env as hosted_from_env
    from keystone.providers.onchain import from_env as onchain_from_env

    demo = DemoChainProvider(
        chain=s.chain,
        block_time_s=s.block_time_s,
        required_confirmations=s.confirmations,
    )

    # On-chain first: it needs no account, holds no keys, and takes no fee.
    for build in (onchain_from_env, coinbase_from_env, hosted_from_env):
        provider = build()
        if provider is None:
            continue
        if s.is_production:
            return provider, False
        # Outside production, keep the simulated rail alongside the real one so
        # the demo stays clickable while real payments are being exercised.
        # The production guard still refuses to ship this pairing.
        from keystone.providers.dual import DualPaymentProvider

        return DualPaymentProvider(provider, demo), False
    return demo, True


# --------------------------------------------------------------------------
# the guard
# --------------------------------------------------------------------------

_REMEDY = {
    "database": "DATABASE_URL must point at Postgres; SQLite on an ephemeral disk loses every listing on restart.",
    "artifacts": "KEYSTONE_BUCKET (plus R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY) must be set; uploaded weights have no upstream to re-fetch from.",
    "signing": "KEYSTONE_SIGNING_KEY must be set; a key generated at boot makes every previously issued report fail verification.",
    "auth": "KEYSTONE_JWKS_URL, KEYSTONE_JWT_ISSUER and KEYSTONE_JWT_AUDIENCE must be set; static tokens are guessable and grant admin.",
    "payments": "A real payment provider must be wired; the demo provider settles any charge on request, so anyone could take the catalogue for free.",
}


def check_production(stand_ins: dict[str, bool]) -> None:
    """Refuse to start with development stand-ins in production."""
    unsafe = [name for name, is_dev in stand_ins.items() if is_dev]
    if not unsafe:
        return
    lines = [f"  - {name}: {_REMEDY[name]}" for name in unsafe]
    raise ConfigError(
        "KEYSTONE_ENV=production but development stand-ins are in use:\n"
        + "\n".join(lines)
    )


def build_deps(settings: Settings | None = None) -> Deps:
    s = settings or Settings.from_env()

    store, dev_db = build_store(s)
    artifacts, dev_artifacts = build_artifacts(s)
    signer, dev_signer = build_signer(s)
    auth, dev_auth = build_auth(s)
    payments, dev_payments = build_payments(s)

    if s.is_production:
        check_production({
            "database": dev_db,
            "artifacts": dev_artifacts,
            "signing": dev_signer,
            "auth": dev_auth,
            "payments": dev_payments,
        })

    store.create_all()
    return Deps(store, artifacts, payments, auth, signer=signer)


def app():
    """ASGI entry point. `uvicorn keystone.settings:app --factory`."""
    return create_app(build_deps())
