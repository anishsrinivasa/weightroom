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
