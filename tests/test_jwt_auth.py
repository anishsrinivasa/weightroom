"""Token verification, against real signatures rather than a mock.

Audience became optional so Clerk's session tokens can be accepted at all --
they carry `azp` and have no `aud`, and requiring `aud` rejected every one of
them, which reads to a user as "signing in silently does nothing".

Optional is the risk. These assert that nothing else was loosened on the way:
signature, issuer, expiry and algorithm are all still refusals, and where a
provider *does* mint an audience it is still checked.
"""

from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from keystone.auth import JWTAuth

ISSUER = "https://real-egret-6523.clerk.accounts.dev"
ORIGIN = "https://weightroom-gvelamoor.fly.dev"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-key"


def _token(key=_KEY, **claims) -> str:
    payload = {
        "sub": "user_abc123",
        "iss": ISSUER,
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
        **claims,
    }
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": KID})


class _FakeJWKS:
    """Stands in for the network fetch only. The key and signature are real."""

    def __init__(self, key):
        self._key = key.public_key()

    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": self._key})()


@pytest.fixture
def auth(monkeypatch):
    def build(**kwargs):
        instance = JWTAuth.__new__(JWTAuth)
        instance._jwks = _FakeJWKS(_KEY)
        instance._issuer = kwargs.get("issuer", ISSUER)
        instance._audience = kwargs.get("audience", "")
        instance._authorized_parties = tuple(kwargs.get("authorized_parties", ()))
        instance._admin_subjects = frozenset(kwargs.get("admin_subjects", ()))
        instance._admin_claim = "keystone_admin"
        instance._email_claim = "email"
        instance._leeway = 30
        return instance

    return build


# -- the change ------------------------------------------------------------

def test_a_clerk_shaped_token_without_an_audience_is_accepted(auth) -> None:
    """`sub`, `iss`, `exp`, `azp` and no `aud` -- what Clerk actually mints."""
    principal = auth().principal_for(_token(azp=ORIGIN, sid="sess_1"))
    assert principal is not None
    assert principal.user_id == "user_abc123"


def test_an_audience_is_still_required_when_one_is_configured(auth) -> None:
    """Providers that mint `aud` must still have it checked -- optional means
    'not every provider has one', not 'skip the check'."""
    checker = auth(audience="keystone-api")
    assert checker.principal_for(_token()) is None
    assert checker.principal_for(_token(aud="keystone-api")) is not None


def test_the_wrong_audience_is_refused(auth) -> None:
    checker = auth(audience="keystone-api")
    assert checker.principal_for(_token(aud="some-other-app")) is None


def test_an_unlisted_authorized_party_is_refused(auth) -> None:
    """With no `aud`, `azp` is what stops a token minted for another origin on
    this same instance being replayed here."""
    checker = auth(authorized_parties=(ORIGIN,))
    assert checker.principal_for(_token(azp="https://evil.example")) is None
    assert checker.principal_for(_token(azp=ORIGIN)) is not None


def test_no_configured_parties_means_no_azp_check(auth) -> None:
    """A deployment that has not named its origins has made no claim about
    them. Inventing one would reject every token."""
    assert auth().principal_for(_token(azp="https://anything.example")) is not None


# -- everything that must still refuse -------------------------------------

def test_a_token_from_another_issuer_is_refused(auth) -> None:
    """The issuer pins the Clerk instance, which is what makes a missing
    audience tolerable: another tenant's token fails here first."""
    assert auth().principal_for(_token(iss="https://someone-else.clerk.accounts.dev")) is None


def test_a_token_signed_by_the_wrong_key_is_refused(auth) -> None:
    assert auth().principal_for(_token(key=_OTHER_KEY)) is None


def test_an_expired_token_is_refused(auth) -> None:
    assert auth().principal_for(_token(exp=int(time.time()) - 3600)) is None


def test_a_token_with_no_subject_is_refused(auth) -> None:
    """`sub` is the account. Without it there is nobody to be."""
    stripped = jwt.encode(
        {"iss": ISSUER, "exp": int(time.time()) + 300},
        _KEY, algorithm="RS256", headers={"kid": KID},
    )
    assert auth().principal_for(stripped) is None


def test_an_unsigned_token_is_refused(auth) -> None:
    """`alg: none` is the oldest downgrade there is, and the algorithm list is
    fixed precisely so it cannot be negotiated."""
    unsigned = jwt.encode(
        {"sub": "user_abc123", "iss": ISSUER, "exp": int(time.time()) + 300},
        key="", algorithm="none",
    )
    assert auth().principal_for(unsigned) is None


def test_garbage_is_anonymous_rather_than_an_error(auth) -> None:
    """Browsing stays open, and we never leak why a token was rejected."""
    for junk in ("", "not-a-token", "a.b.c", json.dumps({"sub": "x"})):
        assert auth().principal_for(junk) is None


def test_admin_comes_from_the_provider_not_the_client(auth) -> None:
    """The claim is signed by the identity provider. A client that could set
    it would grant itself the internal report view."""
    assert auth().principal_for(_token()).is_admin is False
    assert auth().principal_for(_token(keystone_admin=True)).is_admin is True
    # Truthy-but-not-true must not pass: the check is identity, not coercion.
    assert auth().principal_for(_token(keystone_admin="yes")).is_admin is False


def test_admin_can_also_come_from_the_deployments_own_list(auth) -> None:
    """A signed claim needs provider-side setup, and a marketplace still has to
    be able to act on a listing whose creator no longer exists. Both routes are
    controlled by us; neither is settable by a client."""
    assert auth(admin_subjects=("user_abc123",)).principal_for(_token()).is_admin is True
    assert auth(admin_subjects=("user_someone_else",)).principal_for(_token()).is_admin is False
