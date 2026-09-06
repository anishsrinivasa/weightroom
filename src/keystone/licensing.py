"""The redistribution terms a seller attaches, and the buyer's acceptance.

Two kinds, deliberately. A marketplace selling downloadable weights has one
meaningful question to put to a buyer -- may you pass this on? -- and a menu of
SPDX identifiers answers it badly, because most of them were written for source
code and none of them were written for a model a buyer can copy perfectly.

This is distinct from `provenance.LicenseInfo`, which reads what the *upstream*
model card declared and checks the derivation chain. That is a fact about the
artifact. This is a term of sale between two parties on this platform, and it
is the one the buyer is asked to agree to.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LicenseKind(str, Enum):
    NON_DISTRIBUTIVE = "non_distributive"
    FULL_ACCESS = "full_access"


@dataclass(frozen=True)
class LicenseTerms:
    kind: LicenseKind
    display_name: str
    summary: str
    #: Shown in the agreement a buyer must accept before paying. Written to be
    #: read, not skimmed past: short sentences, no defined terms, and the
    #: consequence stated rather than implied.
    agreement: str


NON_DISTRIBUTIVE = LicenseTerms(
    kind=LicenseKind.NON_DISTRIBUTIVE,
    display_name="Non-distributive licence",
    summary=(
        "You may use and modify the model. You may not redistribute or resell "
        "it, or any part of it, including derived weights."
    ),
    agreement=(
        "You may run and modify this model for your own use. You may not "
        "redistribute, republish, resell, or otherwise share the weights or any "
        "part of them, including a modified or derived version.\n\n"
        "Each download is marked so that a copy can be traced back to the "
        "account that obtained it. If a copy of this model is published without "
        "permission, we can identify which purchase it came from, and the "
        "account responsible will permanently lose access to the platform."
    ),
)

FULL_ACCESS = LicenseTerms(
    kind=LicenseKind.FULL_ACCESS,
    display_name="Full access licence",
    summary=(
        "You may use, modify, redistribute, and resell the model or any part "
        "of it freely."
    ),
    agreement=(
        "You may run, modify, redistribute, and resell this model or any part "
        "of it, freely and without further permission from the seller.\n\n"
        "Each download is still marked with the account that obtained it. That "
        "mark identifies the origin of a copy; under these terms it does not "
        "restrict what you may do with it."
    ),
)

TERMS: dict[LicenseKind, LicenseTerms] = {
    NON_DISTRIBUTIVE.kind: NON_DISTRIBUTIVE,
    FULL_ACCESS.kind: FULL_ACCESS,
}

#: What a listing gets when the seller did not choose. The stricter of the two
#: on purpose: granting redistribution rights nobody asked to grant is not a
#: default a marketplace can take back once a buyer has relied on it.
DEFAULT = LicenseKind.NON_DISTRIBUTIVE


def parse(value: str | None) -> LicenseKind:
    """Read a stored or submitted value, falling back to the strict default.

    Unrecognised input resolves to the default rather than raising, because an
    older row written before this field existed is not an error -- and the
    direction of the fallback means a bad value never widens someone's rights.
    """
    if not value:
        return DEFAULT
    try:
        return LicenseKind(value)
    except ValueError:
        return DEFAULT


def terms_for(value: str | None) -> LicenseTerms:
    return TERMS[parse(value)]


def catalogue() -> list[dict]:
    """The choices a seller picks from, for the submission form."""
    return [
        {
            "kind": terms.kind.value,
            "display_name": terms.display_name,
            "summary": terms.summary,
            "agreement": terms.agreement,
            "default": terms.kind is DEFAULT,
        }
        for terms in (NON_DISTRIBUTIVE, FULL_ACCESS)
    ]


def permits_redistribution(value: str | None) -> bool:
    return parse(value) is LicenseKind.FULL_ACCESS


__all__ = [
    "DEFAULT",
    "FULL_ACCESS",
    "NON_DISTRIBUTIVE",
    "TERMS",
    "LicenseKind",
    "LicenseTerms",
    "catalogue",
    "parse",
    "permits_redistribution",
    "terms_for",
]
