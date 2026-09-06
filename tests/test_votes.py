"""Votes are per account, changeable, and countable in one query.

A pair of counters on `listings` would have been smaller and wrong: counters
cannot tell whether someone has already voted, so a vote cannot be changed or
withdrawn and anyone who can call the endpoint twice can run the number up
alone. The row-per-(voter, listing) shape is what makes the number mean
something a buyer can read.
"""

from __future__ import annotations

import pytest

from keystone.db import ArtifactRow, ListingRow, Store, UserRow, VoteRow


@pytest.fixture
def store(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'votes.db'}")
    store.create_all()
    with store.session() as s:
        s.add(ArtifactRow(digest="d" * 64, total_bytes=1, file_manifest=[]))
        for user in ("alice", "bob", "carol"):
            s.add(UserRow(id=user, email=f"{user}@example.com"))
        s.flush()
        for listing in ("lst_a", "lst_b"):
            s.add(ListingRow(
                id=listing, creator_id="alice", artifact_digest="d" * 64,
                state="listed", title=listing, price_minor=0,
            ))
        s.commit()
    return store


def tally(store, listing="lst_a", voter=None):
    with store.session() as s:
        return store.vote_tally(s, [listing], voter_id=voter)[listing]


def test_a_vote_is_counted_once_however_many_times_it_is_cast(store) -> None:
    """The failure a bare counter cannot prevent."""
    with store.session() as s:
        for _ in range(5):
            store.cast_vote(s, voter_id="bob", listing_id="lst_a", value=1)
        s.commit()
    assert tally(store)["up"] == 1
    assert tally(store)["score"] == 1


def test_switching_sides_moves_the_vote_rather_than_adding_one(store) -> None:
    with store.session() as s:
        store.cast_vote(s, voter_id="bob", listing_id="lst_a", value=1)
        s.commit()
        store.cast_vote(s, voter_id="bob", listing_id="lst_a", value=-1)
        s.commit()
    counts = tally(store)
    assert (counts["up"], counts["down"], counts["score"]) == (0, 1, -1)
    with store.session() as s:
        assert s.query(VoteRow).count() == 1


def test_a_vote_can_be_withdrawn(store) -> None:
    with store.session() as s:
        store.cast_vote(s, voter_id="bob", listing_id="lst_a", value=1)
        s.commit()
        store.cast_vote(s, voter_id="bob", listing_id="lst_a", value=0)
        s.commit()
    assert tally(store)["score"] == 0
    with store.session() as s:
        assert s.query(VoteRow).count() == 0


def test_withdrawing_a_vote_that_was_never_cast_is_not_an_error(store) -> None:
    with store.session() as s:
        store.cast_vote(s, voter_id="bob", listing_id="lst_a", value=0)
        s.commit()
    assert tally(store)["score"] == 0


def test_separate_accounts_each_count(store) -> None:
    with store.session() as s:
        store.cast_vote(s, voter_id="bob", listing_id="lst_a", value=1)
        store.cast_vote(s, voter_id="carol", listing_id="lst_a", value=-1)
        s.commit()
    counts = tally(store)
    assert (counts["up"], counts["down"], counts["score"]) == (1, 1, 0)


def test_the_viewer_sees_their_own_vote(store) -> None:
    """So the button can render pressed. Without it the UI cannot tell an
    unvoted listing from one this person already voted on."""
    with store.session() as s:
        store.cast_vote(s, voter_id="bob", listing_id="lst_a", value=1)
        s.commit()
    assert tally(store, voter="bob")["mine"] == 1
    assert tally(store, voter="carol")["mine"] == 0
    assert tally(store)["mine"] == 0


def test_votes_do_not_leak_between_listings(store) -> None:
    with store.session() as s:
        store.cast_vote(s, voter_id="bob", listing_id="lst_a", value=1)
        s.commit()
    assert tally(store, "lst_b")["score"] == 0


def test_the_tally_is_one_query_for_many_listings(store) -> None:
    """The catalogue renders a tally per card. A query per card is how a
    listing page starts costing more than the evaluation did."""
    with store.session() as s:
        store.cast_vote(s, voter_id="bob", listing_id="lst_a", value=1)
        store.cast_vote(s, voter_id="carol", listing_id="lst_b", value=-1)
        s.commit()
        counts = store.vote_tally(s, ["lst_a", "lst_b"], voter_id="bob")
    assert counts["lst_a"]["score"] == 1
    assert counts["lst_b"]["score"] == -1
    assert counts["lst_a"]["mine"] == 1


def test_an_empty_request_asks_nothing(store) -> None:
    with store.session() as s:
        assert store.vote_tally(s, []) == {}
