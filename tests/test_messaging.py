"""Direct messages between a buyer and a seller about one listing.

Three properties the shape has to guarantee, none of which are obvious from the
tables alone: one thread per pair per listing however it was opened, deletion
that actually deletes, and expiry that measures inactivity rather than age.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from keystone.db import ArtifactRow, ListingRow, MessageRow, Store, ThreadRow, UserRow


@pytest.fixture
def store():
    store = Store("sqlite://")
    store.create_all()
    with store.session() as s:
        s.add(ArtifactRow(digest="d" * 64, total_bytes=1, file_manifest=[]))
        for user in ("seller", "buyer", "other"):
            s.add(UserRow(id=user, email=f"{user}@example.com"))
        s.flush()
        for listing in ("lst_a", "lst_b"):
            s.add(ListingRow(
                id=listing, creator_id="seller", artifact_digest="d" * 64,
                state="listed", title=listing, price_minor=1,
            ))
        s.commit()
    return store


def test_either_side_opening_finds_the_same_thread(store) -> None:
    """The failure this prevents: two people start a conversation and each
    talks into a thread the other never sees."""
    with store.session() as s:
        first = store.open_thread(s, listing_id="lst_a", a="buyer", b="seller")
        second = store.open_thread(s, listing_id="lst_a", a="seller", b="buyer")
        s.commit()
        assert first.id == second.id
        assert s.query(ThreadRow).count() == 1


def test_the_same_pair_gets_a_thread_per_listing(store) -> None:
    """Scoped to a listing, because "about which model?" is the first thing
    either side would otherwise have to establish."""
    with store.session() as s:
        a = store.open_thread(s, listing_id="lst_a", a="buyer", b="seller")
        b = store.open_thread(s, listing_id="lst_b", a="buyer", b="seller")
        s.commit()
        assert a.id != b.id


def test_messages_come_back_oldest_first(store) -> None:
    with store.session() as s:
        thread = store.open_thread(s, listing_id="lst_a", a="buyer", b="seller")
        store.post_message(s, thread=thread, sender_id="buyer", body="does it run on 8GB?")
        store.post_message(s, thread=thread, sender_id="seller", body="yes")
        s.commit()
        bodies = [m.body for m in store.messages_in(s, thread.id)]
    assert bodies == ["does it run on 8GB?", "yes"]


def test_threads_are_listed_most_recently_active_first(store) -> None:
    with store.session() as s:
        old = store.open_thread(s, listing_id="lst_a", a="buyer", b="seller")
        new = store.open_thread(s, listing_id="lst_b", a="buyer", b="seller")
        old.last_message_at = datetime(2026, 1, 1)
        new.last_message_at = datetime(2026, 9, 1)
        s.commit()
        assert [t.id for t in store.threads_for(s, "buyer")] == [new.id, old.id]


def test_a_thread_only_appears_for_its_participants(store) -> None:
    with store.session() as s:
        store.open_thread(s, listing_id="lst_a", a="buyer", b="seller")
        s.commit()
        assert store.threads_for(s, "other") == []
        assert len(store.threads_for(s, "seller")) == 1


def test_sending_marks_the_thread_read_for_the_sender(store) -> None:
    """Otherwise a sender sees an unread badge for their own message."""
    with store.session() as s:
        thread = store.open_thread(s, listing_id="lst_a", a="buyer", b="seller")
        store.post_message(s, thread=thread, sender_id="buyer", body="hello")
        s.commit()
        assert thread.lower_read_at is not None or thread.upper_read_at is not None
        sender_read = (
            thread.lower_read_at if thread.lower_id == "buyer" else thread.upper_read_at
        )
        recipient_read = (
            thread.upper_read_at if thread.lower_id == "buyer" else thread.lower_read_at
        )
        assert sender_read is not None
        assert recipient_read is None


def test_deleting_a_thread_removes_its_messages(store) -> None:
    """Deleted for both sides. A store that keeps a copy the sender believes is
    gone is worse than one with no delete at all."""
    with store.session() as s:
        thread = store.open_thread(s, listing_id="lst_a", a="buyer", b="seller")
        store.post_message(s, thread=thread, sender_id="buyer", body="hello")
        s.commit()
        store.delete_thread(s, thread)
        s.commit()
        assert s.query(ThreadRow).count() == 0
        assert s.query(MessageRow).count() == 0


def test_expiry_measures_inactivity_not_age(store) -> None:
    """An old conversation still being used must not be cut off mid-sentence."""
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    with store.session() as s:
        stale = store.open_thread(s, listing_id="lst_a", a="buyer", b="seller")
        live = store.open_thread(s, listing_id="lst_b", a="buyer", b="seller")
        # Both created long ago; only one has recent activity.
        stale.created_at = now - timedelta(days=200)
        live.created_at = now - timedelta(days=200)
        stale.last_message_at = now - timedelta(days=31)
        live.last_message_at = now - timedelta(days=2)
        s.commit()

        assert store.expire_threads(s, now=now) == 1
        s.commit()
        remaining = [t.id for t in store.threads_for(s, "buyer")]
    assert remaining == [live.id]


def test_expiry_takes_the_messages_with_it(store) -> None:
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    with store.session() as s:
        thread = store.open_thread(s, listing_id="lst_a", a="buyer", b="seller")
        store.post_message(s, thread=thread, sender_id="buyer", body="hello")
        thread.last_message_at = now - timedelta(days=45)
        s.commit()
        store.expire_threads(s, now=now)
        s.commit()
        assert s.query(MessageRow).count() == 0


def test_a_thread_on_the_retention_boundary_survives(store) -> None:
    """Strictly older than the window, so a thread that is exactly at it is
    kept -- an off-by-one here silently deletes a day of conversations."""
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    with store.session() as s:
        thread = store.open_thread(s, listing_id="lst_a", a="buyer", b="seller")
        thread.last_message_at = now - timedelta(days=store.DM_RETENTION_DAYS)
        s.commit()
        assert store.expire_threads(s, now=now) == 0
