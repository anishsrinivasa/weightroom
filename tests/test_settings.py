"""Configuration and the production guard.

Development defaults are convenient and completely unsafe. These tests are the
thing standing between a demo and a deployment that hands away the catalogue.
"""

from __future__ import annotations

import base64

import pytest

from keystone.auth import JWTAuth, StaticTokenAuth
from keystone.payments import DemoChainProvider
from keystone.settings import (
    ConfigError,
    Settings,
    build_artifacts,
    build_auth,
    build_deps,
    build_payments,
    build_signer,
    build_store,
    check_production,
)
from keystone.signing import Ed25519Signer
from keystone.storage import LocalStore, S3Store


def _prod(**kw) -> Settings:
    return Settings(env="production", **kw)


# --------------------------------------------------------------------------
# the guard
# --------------------------------------------------------------------------

def test_development_boots_with_defaults() -> None:
    assert build_deps(Settings()) is not None


def test_production_refuses_every_dev_default() -> None:
    with pytest.raises(ConfigError) as exc:
        build_deps(_prod())
    message = str(exc.value)
    for component in ("database", "artifacts", "signing", "auth", "payments"):
        assert component in message


def test_the_error_says_what_to_set() -> None:
    """A guard that only says 'no' costs an hour of guessing."""
    with pytest.raises(ConfigError) as exc:
        build_deps(_prod())
    message = str(exc.value)
    for var in (
        "DATABASE_URL",
        "KEYSTONE_BUCKET",
        "KEYSTONE_SIGNING_KEY",
        "KEYSTONE_JWKS_URL",
    ):
        assert var in message


@pytest.mark.parametrize(
    "component",
    ["database", "artifacts", "signing", "auth", "payments"],
)
def test_any_single_stand_in_blocks_the_boot(component: str) -> None:
    stand_ins = dict.fromkeys(
        ["database", "artifacts", "signing", "auth", "payments"], False
    )
    stand_ins[component] = True
    with pytest.raises(ConfigError, match=component):
        check_production(stand_ins)


def test_a_fully_configured_production_passes() -> None:
    check_production(dict.fromkeys(
        ["database", "artifacts", "signing", "auth", "payments"], False
    ))


def test_demo_payments_can_never_reach_production() -> None:
    """The sharpest one: this provider settles any charge on request."""
    provider, is_dev = build_payments(Settings())
    assert isinstance(provider, DemoChainProvider)
    assert is_dev is True


# --------------------------------------------------------------------------
# component wiring
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected_driver",
    [
        ("postgres://u:p@host/db", "postgresql+psycopg://"),
        ("postgresql://u:p@host/db", "postgresql+psycopg://"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://"),
    ],
)
def test_bare_postgres_urls_get_a_driver(url: str, expected_driver: str) -> None:
    """Neon and Fly hand out postgres:// URLs SQLAlchemy will not accept."""
    store, is_dev = build_store(Settings(database_url=url))
    assert str(store.engine.url).startswith(expected_driver)
    assert is_dev is False


def test_sqlite_is_flagged_as_a_stand_in() -> None:
    assert build_store(Settings(database_url="sqlite:///x.db"))[1] is True


def test_bucket_selects_object_storage(monkeypatch) -> None:
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "y")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://example.invalid")
    store, is_dev = build_artifacts(Settings(bucket="keystone-artifacts"))
    assert isinstance(store, S3Store) and is_dev is False


def test_no_bucket_falls_back_to_local() -> None:
    store, is_dev = build_artifacts(Settings())
    assert isinstance(store, LocalStore) and is_dev is True
    # base_url is what makes browser uploads work locally.
    assert store.base_url == "/v1/dev-upload"


def test_supplied_signing_key_is_reused(monkeypatch) -> None:
    key = Ed25519Signer.generate()
    monkeypatch.setenv("KEYSTONE_SIGNING_KEY", key.private_key_b64())
    signer, is_dev = build_signer(Settings(signing_key_b64=key.private_key_b64()))
    assert is_dev is False
    assert signer.key_id == key.key_id


def test_generated_signing_key_is_flagged() -> None:
    """A key per boot invalidates every report issued before it."""
    signer, is_dev = build_signer(Settings())
    assert is_dev is True
    assert signer.key_id.startswith("ed25519:")


def test_jwt_auth_when_fully_configured() -> None:
    auth, is_dev = build_auth(Settings(
        jwks_url="https://idp.invalid/jwks.json",
        jwt_issuer="https://idp.invalid",
        jwt_audience="keystone",
    ))
    assert isinstance(auth, JWTAuth) and is_dev is False


def test_partial_jwt_config_does_not_half_enable_auth() -> None:
    """Missing an audience must not silently fall back to accepting anything."""
    auth, is_dev = build_auth(Settings(jwks_url="https://idp.invalid/jwks.json"))
    assert isinstance(auth, StaticTokenAuth) and is_dev is True


def test_dev_tokens_parse_including_the_admin_flag() -> None:
    auth, _ = build_auth(Settings(
        dev_tokens="t1:u1:a@example.com,t2:u2:b@example.com:admin"
    ))
    assert auth.principal_for("t1").is_admin is False
    assert auth.principal_for("t2").is_admin is True
    assert auth.principal_for("nope") is None


def test_malformed_dev_token_entries_are_skipped() -> None:
    auth, _ = build_auth(Settings(dev_tokens="broken,t1:u1:a@example.com"))
    assert auth.principal_for("t1") is not None
    assert auth.principal_for("broken") is None


# --------------------------------------------------------------------------
# dev-only endpoints
# --------------------------------------------------------------------------

def test_dev_endpoints_are_absent_without_their_dev_backends() -> None:
    """demo-pay and dev-upload exist only because the dev backends do.

    Both are gated on the component type, so a production wiring cannot expose
    them even by accident.
    """
    from fastapi import FastAPI

    from keystone.api import Deps, create_app
    from keystone.db import Store
    from keystone.payments import MockPaymentProvider

    store = Store("sqlite://")
    store.create_all()
    app: FastAPI = create_app(Deps(
        store,
        LocalStore("./unused-store"),          # no base_url -> no receiver
        MockPaymentProvider(),                 # not the demo chain
        StaticTokenAuth({}),
    ))
    paths = {r.path for r in app.routes if hasattr(r, "methods")}
    assert not any("dev-upload" in p for p in paths)
    assert not any("demo-pay" in p for p in paths)


def test_dev_endpoints_appear_with_dev_backends() -> None:
    from keystone.api import Deps, create_app
    from keystone.db import Store

    store = Store("sqlite://")
    store.create_all()
    app = create_app(Deps(
        store,
        LocalStore("./unused-store", base_url="/v1/dev-upload"),
        DemoChainProvider(),
        StaticTokenAuth({}),
    ))
    paths = {r.path for r in app.routes if hasattr(r, "methods")}
    assert any("dev-upload" in p for p in paths)
    assert any("demo-pay" in p for p in paths)
