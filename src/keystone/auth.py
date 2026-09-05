"""Who is asking, and therefore what they may see.

The single rule this module exists to enforce: **audience is derived from the
authenticated principal, never supplied by the caller.** An `?audience=internal`
query parameter would hand any visitor the held-out scores that the entire
redaction layer exists to protect.

The token store here is a development stand-in. Real auth (Clerk, Supabase)
drops in behind `Authenticator` without anything downstream changing.
"""

from __future__ import annotations

import abc
import hmac
from dataclasses import dataclass

from keystone.schema import Audience


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    is_admin: bool = False


class Authenticator(abc.ABC):
    @abc.abstractmethod
    def principal_for(self, token: str) -> Principal | None:
        """Return the principal for a bearer token, or None if it is not valid."""


class StaticTokenAuth(Authenticator):
    """Development only. Tokens in memory, compared in constant time."""

    def __init__(self, tokens: dict[str, Principal] | None = None) -> None:
        self._tokens = dict(tokens or {})

    def add(self, token: str, principal: Principal) -> None:
        self._tokens[token] = principal

    def principal_for(self, token: str) -> Principal | None:
        for known, principal in self._tokens.items():
            if hmac.compare_digest(known, token):
                return principal
        return None


def audience_for(principal: Principal | None, listing_creator_id: str | None) -> Audience:
    """Resolve what this caller may see of a report.

    Anonymous and unrelated callers are buyers. The creator of the listing sees
    the creator view -- coarse bands plus failing categories, never per-item
    detail. Only staff see internal truth.
    """
    if principal is None:
        return Audience.BUYER
    if principal.is_admin:
        return Audience.INTERNAL
    if listing_creator_id is not None and principal.user_id == listing_creator_id:
        return Audience.CREATOR
    return Audience.BUYER


class JWTAuth(Authenticator):
    """Verifies tokens from a real identity provider (Clerk, Supabase, Auth0).

    Everything here is a refusal by default. Signature, issuer, audience and
    expiry are all checked, the algorithm list is fixed so `alg: none` and
    HMAC-confusion attacks cannot downgrade us, and admin comes from a claim the
    identity provider controls -- never from anything a client can set.

    Keys are fetched from the provider's JWKS endpoint and cached.
    """

    _ALGORITHMS = ("RS256", "RS512", "ES256", "ES384")

    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        audience: str,
        *,
        admin_claim: str = "keystone_admin",
        email_claim: str = "email",
        leeway_s: int = 30,
    ) -> None:
        from jwt import PyJWKClient

        self._jwks = PyJWKClient(jwks_url, cache_keys=True)
        self._issuer = issuer
        self._audience = audience
        self._admin_claim = admin_claim
        self._email_claim = email_claim
        self._leeway = leeway_s

    def principal_for(self, token: str) -> Principal | None:
        import jwt

        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self._ALGORITHMS),
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except Exception:
            # An invalid token is anonymous, not an error. Browsing stays open,
            # and we never leak why a token was rejected.
            return None

        subject = claims.get("sub")
        if not subject:
            return None

        return Principal(
            user_id=str(subject),
            email=str(claims.get(self._email_claim, "")),
            is_admin=claims.get(self._admin_claim) is True,
        )
