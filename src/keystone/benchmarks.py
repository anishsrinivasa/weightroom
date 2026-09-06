"""The benchmark menu: what a creator may choose, and what it costs.

Capability benchmarks are a la carte. A creator picks what is worth paying to
demonstrate, and price scales with the selection because GPU time does.

Safety is not on the menu. A creator who could decline the safety check would,
and a badge that only appears when the seller expects to pass it means nothing.
Mandatory suites are always run and always billed.

The anti-hiding rule sits here too: **every offered benchmark appears in the
report**, run or declined. A benchmark a seller can silently omit is a
benchmark they can hide a bad result behind, so declining is a visible choice
rather than an absence.
"""

from __future__ import annotations

from dataclasses import dataclass

from keystone.payments import Currency, Money
from keystone.suites import Suite


@dataclass(frozen=True)
class MenuItem:
    suite_id: str
    display_name: str
    version: str
    mandatory: bool
    gate: bool
    diagnostic: bool
    price: Money
    held_out: bool
    description: str

    def as_dict(self) -> dict:
        return {
            "suite_id": self.suite_id,
            "display_name": self.display_name,
            "version": self.version,
            "mandatory": self.mandatory,
            "gate": self.gate,
            "diagnostic": self.diagnostic,
            "price": str(self.price),
            "price_minor": self.price.amount_minor,
            "held_out": self.held_out,
            "description": self.description,
        }


def menu(suites: list[Suite], currency: Currency = Currency.USDC) -> list[MenuItem]:
    """What a creator can choose from, mandatory items first.

    Internal suites are omitted entirely. A conditioning probe is not a
    benchmark a creator declines -- it is the instrument that sets their safety
    bar, and naming it here would be the first step to sandbagging it. They
    still run: `normalise_selection` folds in every mandatory suite whether or
    not it appeared on this list.
    """
    items = [
        MenuItem(
            suite_id=s.manifest.id,
            display_name=s.manifest.name,
            version=s.manifest.version,
            mandatory=s.manifest.mandatory,
            gate=s.manifest.gate,
            diagnostic=s.manifest.diagnostic,
            price=Money(s.manifest.price_minor, currency),
            held_out=s.manifest.held_out,
            description=s.manifest.description,
        )
        for s in suites
        if not s.manifest.internal
    ]
    return sorted(items, key=lambda i: (not i.mandatory, i.display_name.lower()))


def mandatory_ids(suites: list[Suite]) -> list[str]:
    return [s.manifest.id for s in suites if s.manifest.mandatory]


def optional_ids(suites: list[Suite]) -> list[str]:
    return [s.manifest.id for s in suites if not s.manifest.mandatory]


def internal_ids(suites: list[Suite]) -> set[str]:
    """Instruments the creator is not allowed to know about."""
    return {s.manifest.id for s in suites if s.manifest.internal}


def creator_visible(suites: list[Suite], ids: list[str]) -> list[str]:
    """Filter a suite-id list for anything shown to a creator.

    `normalise_selection` returns everything that will *run*, internal probes
    included, because the worker needs the full set. Handing that list back
    names the instrument that sets their safety bar.
    """
    hidden = internal_ids(suites)
    return [i for i in ids if i not in hidden]


class SelectionError(ValueError):
    pass


def normalise_selection(suites: list[Suite], selected: list[str] | None) -> list[str]:
    """Resolve a creator's picks into the set that will actually run.

    Mandatory suites are folded in whether or not they were asked for, so a
    client cannot omit safety by omitting it from the list. An unknown id is an
    error rather than a silent no-op -- quietly dropping a benchmark someone
    thought they were buying is worse than refusing the request.
    """
    known = {s.manifest.id for s in suites}
    chosen = list(dict.fromkeys(selected or []))  # de-dupe, keep order

    unknown = [c for c in chosen if c not in known]
    if unknown:
        raise SelectionError(f"unknown benchmarks: {', '.join(sorted(unknown))}")

    required = mandatory_ids(suites)
    return list(dict.fromkeys(required + chosen))


def quote(
    suites: list[Suite], selected: list[str] | None, currency: Currency = Currency.USDC
) -> Money:
    """Price the run. Mandatory items are always included and always billed."""
    running = set(normalise_selection(suites, selected))
    total = sum(s.manifest.price_minor for s in suites if s.manifest.id in running)
    return Money(total, currency)


def declined_ids(suites: list[Suite], selected: list[str] | None) -> list[str]:
    """Everything on offer that the creator chose not to run.

    Internal suites were never on offer, so they cannot have been declined --
    and listing them here would name them just as loudly as the menu would.
    """
    running = set(normalise_selection(suites, selected))
    return [
        s.manifest.id
        for s in suites
        if s.manifest.id not in running and not s.manifest.internal
    ]
