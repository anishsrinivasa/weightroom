"""Both payment rails at once.

Real USDC and the simulated chain coexist during development. The properties
that matter: a charge stays on the rail it was created on, and a demo payment
can never be simulated against real funds.
"""

from __future__ import annotations

import pytest

from keystone.payments import ChargeStatus, Currency, DemoChainProvider, Money
from keystone.providers.dual import DEMO, LIVE, DualPaymentProvider, split_rail
from keystone.providers.onchain import OnChainConfig, OnChainProvider

PRICE = Money(39_000_000, Currency.USDC)
RECEIVE = "0x8CF9aABe652ECE5Fe812fCEAF5763A27c7C44F72"


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def dual(clock: Clock) -> DualPaymentProvider:
    live = OnChainProvider(
        OnChainConfig(receive_address=RECEIVE, rpc_url="https://rpc.invalid")
    )
    demo = DemoChainProvider(block_time_s=1.0, required_confirmations=3, clock=clock)
    return DualPaymentProvider(live, demo)


def test_rail_is_recoverable_from_the_id_alone() -> None:
    """No lookup, so routing survives a restart."""
    assert split_rail("demo~ch_1") == (DEMO, "ch_1")
    assert split_rail("live~ch_1") == (LIVE, "ch_1")


def test_an_unprefixed_id_is_treated_as_live() -> None:
    """Charges written before the demo rail existed are real ones."""
    assert split_rail("ch_legacy") == (LIVE, "ch_legacy")


def test_each_rail_quotes_its_own_address(dual: DualPaymentProvider) -> None:
    demo = dual.create_charge(PRICE, "ord_1", metadata={"rail": DEMO})
    live = dual.create_charge(PRICE, "ord_2", metadata={"rail": LIVE})
    assert live.address == RECEIVE
    assert demo.address != RECEIVE


def test_a_charge_reports_which_rail_it_is_on(dual: DualPaymentProvider) -> None:
    charge = dual.create_charge(PRICE, "ord_1", metadata={"rail": DEMO})
    assert charge.metadata["rail"] == DEMO
    assert dual.get_charge(charge.charge_id).metadata["rail"] == DEMO


def test_an_unknown_rail_is_refused(dual: DualPaymentProvider) -> None:
    with pytest.raises(ValueError, match="unknown payment rail"):
        dual.create_charge(PRICE, "ord_1", metadata={"rail": "wishful"})


def test_the_demo_rail_settles_on_request(dual: DualPaymentProvider, clock: Clock) -> None:
    charge = dual.create_charge(PRICE, "ord_1", metadata={"rail": DEMO})
    dual.broadcast(charge.charge_id)
    clock.t += 3
    assert dual.get_charge(charge.charge_id).status is ChargeStatus.SETTLED


def test_the_live_rail_cannot_be_simulated(dual: DualPaymentProvider) -> None:
    """Otherwise "paid" would mean nothing on the rail where it matters."""
    charge = dual.create_charge(PRICE, "ord_1", metadata={"rail": LIVE})
    with pytest.raises(ValueError, match="live rail"):
        dual.broadcast(charge.charge_id)


def test_prefixing_does_not_disturb_the_underlying_provider(
    dual: DualPaymentProvider, clock: Clock
) -> None:
    """The demo provider keys its bookkeeping by the id it issued.

    Renaming that id in place made broadcast() and the confirmation counter
    look at different keys, so a demo charge stayed pending forever.
    """
    charge = dual.create_charge(PRICE, "ord_1", metadata={"rail": DEMO})
    dual.broadcast(charge.charge_id)
    seen = dual.get_charge(charge.charge_id)
    assert seen.confirmations >= 1
    assert seen.status is ChargeStatus.CONFIRMING


def test_payouts_always_go_through_the_real_rail(dual: DualPaymentProvider) -> None:
    from keystone.payments import RefundNotSupported

    with pytest.raises(RefundNotSupported, match="read-only"):
        dual.create_payout("0xcreator", PRICE, "ord_1")
