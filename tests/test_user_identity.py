"""Two accounts whose tokens carry no email must not collide.

`users.email` is unique, and an identity provider is not obliged to put an
email in its token -- Clerk's session tokens do not. Every principal then
arrives with the same empty string: the first account to act claims it and the
second violates the constraint.

The failure is worse than it sounds. It does not appear until a *second* person
uses the site, so it survives every test a single account can run, and it
presents to that second person as the site being broken for them specifically.
"""

from __future__ import annotations

import pytest

from keystone.db import Store, UserRow


@pytest.fixture
def store():
    store = Store("sqlite://")
    store.create_all()
    return store


def test_two_accounts_with_no_email_both_persist(store) -> None:
    """The bug: a seller uploads, then a different buyer cannot purchase."""
    with store.session() as s:
        store.upsert_user(s, "user_seller", "")
        store.upsert_user(s, "user_buyer", "")
        s.commit()
    with store.session() as s:
        assert s.query(UserRow).count() == 2


def test_the_placeholder_is_unique_per_account(store) -> None:
    with store.session() as s:
        seller = store.upsert_user(s, "user_seller", "")
        buyer = store.upsert_user(s, "user_buyer", "")
        s.commit()
        assert seller.email != buyer.email


def test_the_placeholder_can_never_be_a_real_address(store) -> None:
    """`.invalid` is reserved by RFC 2606. A placeholder that could collide
    with somebody's actual address would be worse than the collision it fixes,
    and one that looks real invites someone to email it."""
    with store.session() as s:
        row = store.upsert_user(s, "user_x", "")
        s.commit()
        assert row.email.endswith("@accounts.invalid")


def test_a_real_email_is_kept_as_given(store) -> None:
    with store.session() as s:
        row = store.upsert_user(s, "user_x", "person@example.com")
        s.commit()
        assert row.email == "person@example.com"


def test_whitespace_is_not_an_email(store) -> None:
    with store.session() as s:
        row = store.upsert_user(s, "user_x", "   ")
        s.commit()
        assert row.email.endswith("@accounts.invalid")


def test_a_placeholder_heals_when_a_real_address_arrives(store) -> None:
    """A JWT template that starts supplying `email` should upgrade existing
    rows rather than leaving them permanently synthetic."""
    with store.session() as s:
        store.upsert_user(s, "user_x", "")
        s.commit()
    with store.session() as s:
        row = store.upsert_user(s, "user_x", "person@example.com")
        s.commit()
        assert row.email == "person@example.com"


def test_a_token_that_stops_carrying_an_email_does_not_blank_the_row(store) -> None:
    """Guarded on a real value, so a provider change cannot erase what we know."""
    with store.session() as s:
        store.upsert_user(s, "user_x", "person@example.com")
        s.commit()
    with store.session() as s:
        row = store.upsert_user(s, "user_x", "")
        s.commit()
        assert row.email == "person@example.com"


def test_repeat_visits_do_not_create_duplicates(store) -> None:
    with store.session() as s:
        for _ in range(3):
            store.upsert_user(s, "user_x", "")
        s.commit()
    with store.session() as s:
        assert s.query(UserRow).count() == 1
