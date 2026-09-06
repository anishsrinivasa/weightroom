"""Terms are chosen by the seller, agreed by the buyer, and fixed at purchase.

The agreement is what makes the download fingerprint defensible -- tracing a
leaked copy back to an account only holds up if that account was told so at the
point of sale. So the gate is a real one: no acceptance, no order.
"""

from __future__ import annotations

import pytest

from keystone import licensing
from keystone.licensing import LicenseKind


def test_the_default_is_the_stricter_of_the_two() -> None:
    """Granting redistribution nobody chose to grant is not recoverable once a
    buyer has relied on it. A blank must never widen rights."""
    assert licensing.DEFAULT is LicenseKind.NON_DISTRIBUTIVE
    assert licensing.parse(None) is LicenseKind.NON_DISTRIBUTIVE
    assert licensing.parse("") is LicenseKind.NON_DISTRIBUTIVE


def test_an_unrecognised_value_falls_back_strict_rather_than_raising() -> None:
    """A row written before this column existed is not an error, and the
    direction of the fallback is what keeps that safe."""
    assert licensing.parse("something-else") is LicenseKind.NON_DISTRIBUTIVE
    assert not licensing.permits_redistribution("something-else")


def test_full_access_is_the_only_kind_that_permits_redistribution() -> None:
    assert licensing.permits_redistribution("full_access")
    assert not licensing.permits_redistribution("non_distributive")


def test_the_non_distributive_agreement_discloses_the_download_mark() -> None:
    """Tracing a leak is only defensible if the buyer was told at the point of
    sale. If this text loses the disclosure, the fingerprint loses its basis."""
    agreement = licensing.NON_DISTRIBUTIVE.agreement.lower()
    assert "traced back to the account" in agreement
    assert "permanently lose access" in agreement


def test_full_access_still_discloses_the_mark_without_claiming_a_restriction() -> None:
    """The mark is applied either way. Under these terms it identifies origin
    and restricts nothing, and saying otherwise would misdescribe the sale."""
    agreement = licensing.FULL_ACCESS.agreement.lower()
    assert "marked with the account" in agreement
    assert "does not restrict" in agreement


def test_the_catalogue_marks_exactly_one_default() -> None:
    entries = licensing.catalogue()
    assert len(entries) == 2
    assert [e["default"] for e in entries].count(True) == 1
    for entry in entries:
        assert entry["agreement"] and entry["summary"]


@pytest.mark.parametrize("kind", ["non_distributive", "full_access"])
def test_round_trip_through_parse(kind: str) -> None:
    assert licensing.parse(kind).value == kind
    assert licensing.terms_for(kind).kind.value == kind
