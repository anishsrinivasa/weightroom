"""Two payment rails at once: real USDC on Base, and the simulated chain.

Temporary by design. A demo needs to be clickable in seconds, and a real
integration needs to be exercised with real funds, and during development you
want both without restarting anything.

Charges carry their rail in the id, so routing a later `get_charge` needs no
lookup and survives a restart. That prefix is the only thing this class adds --
each underlying provider is untouched, and deleting this file when the demo
rail retires leaves the real one working exactly as it does now.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from keystone.payments import Charge, Money, PaymentProvider, Payout

DEMO = "demo"
LIVE = "live"
_SEPARATOR = "~"


def split_rail(charge_id: str) -> tuple[str, str]:
    """(rail, underlying id). An unprefixed id is assumed live."""
    rail, separator, rest = charge_id.partition(_SEPARATOR)
    if separator and rail in (DEMO, LIVE):
        return rail, rest
    return LIVE, charge_id


class DualPaymentProvider(PaymentProvider):
    def __init__(
        self,
        live: PaymentProvider,
        demo: PaymentProvider,
        *,
        default_rail: str = DEMO,
    ) -> None:
        self._rails = {LIVE: live, DEMO: demo}
        self.default_rail = default_rail

    # -- routing ----------------------------------------------------------

    def rail_for(self, charge_id: str) -> PaymentProvider:
        rail, _ = split_rail(charge_id)
        return self._rails[rail]

    @property
    def live(self) -> PaymentProvider:
        return self._rails[LIVE]

    @property
    def demo(self) -> PaymentProvider:
        return self._rails[DEMO]

    # -- provider interface ----------------------------------------------

    def create_charge(
        self,
        amount: Money,
        reference: str,
        *,
        metadata: dict[str, str] | None = None,
        ttl: timedelta = timedelta(hours=1),
    ) -> Charge:
        metadata = dict(metadata or {})
        rail = metadata.pop("rail", self.default_rail)
        if rail not in self._rails:
            raise ValueError(f"unknown payment rail: {rail!r}")

        charge = self._rails[rail].create_charge(
            amount, reference, metadata=metadata, ttl=ttl
        )
        # A copy, never a mutation: the underlying provider keys its own
        # bookkeeping by the id on the object it created, and renaming that in
        # place makes its broadcast and confirmation tracking miss each other.
        return replace(
            charge,
            charge_id=f"{rail}{_SEPARATOR}{charge.charge_id}",
            metadata={**charge.metadata, "rail": rail},
        )

    def get_charge(self, charge_id: str) -> Charge:
        rail, inner = split_rail(charge_id)
        charge = self._rails[rail].get_charge(inner)
        # Hand back the prefixed id so callers keep routing correctly.
        return replace(
            charge, charge_id=charge_id, metadata={**charge.metadata, "rail": rail}
        )

    def create_payout(self, destination: str, amount: Money, reference: str) -> Payout:
        # Payouts are not a demo concern; they always go through the real rail,
        # which refuses them if it holds no keys.
        return self.live.create_payout(destination, amount, reference)

    def refund(self, charge_id: str, amount: Money | None = None) -> Payout:
        rail, inner = split_rail(charge_id)
        return self._rails[rail].refund(inner, amount)

    # -- demo driver ------------------------------------------------------

    def broadcast(self, charge_id: str, amount: Money | None = None) -> Charge:
        """Simulate a wallet paying. Refuses on the live rail, where it would
        be a lie -- real funds have to actually move."""
        rail, inner = split_rail(charge_id)
        if rail != DEMO:
            raise ValueError("cannot simulate a payment on the live rail")
        charge = self.demo.broadcast(inner, amount)
        return replace(charge, charge_id=charge_id)

    def received(self, charge_id: str) -> Money | None:
        rail, inner = split_rail(charge_id)
        getter = getattr(self._rails[rail], "received", None)
        return getter(inner) if getter else None
