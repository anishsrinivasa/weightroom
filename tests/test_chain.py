"""Simulated-chain payment tests.

The demo provider has to behave like a real one or the demo teaches the wrong
thing: a charge only settles once enough blocks have confirmed it, and the
caller never gets to assert that a payment happened.
"""

from __future__ import annotations

import pytest

from keystone.payments import ChargeStatus, Currency, DemoChainProvider, Money

PRICE = Money(25_000_000, Currency.USDC)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def blocks(self, n: int, block_time: float = 2.0) -> None:
        self.t += n * block_time


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def chain(clock: FakeClock) -> DemoChainProvider:
    return DemoChainProvider(block_time_s=2.0, required_confirmations=3, clock=clock)


def test_new_charge_has_an_address_and_no_payment(chain: DemoChainProvider) -> None:
    charge = chain.create_charge(PRICE, "ord_1")
    assert charge.status is ChargeStatus.PENDING
    assert charge.chain == "base"
    assert charge.address.startswith("0x")
    assert charge.tx_hash is None
    assert charge.confirmations == 0


def test_nothing_settles_without_a_broadcast(chain: DemoChainProvider, clock: FakeClock) -> None:
    charge = chain.create_charge(PRICE, "ord_1")
    clock.blocks(100)
    assert chain.get_charge(charge.charge_id).status is ChargeStatus.PENDING


def test_confirmations_accrue_block_by_block(
    chain: DemoChainProvider, clock: FakeClock
) -> None:
    charge = chain.create_charge(PRICE, "ord_1")
    chain.broadcast(charge.charge_id)

    seen = []
    for _ in range(3):
        c = chain.get_charge(charge.charge_id)
        seen.append((c.confirmations, c.status))
        clock.blocks(1)

    assert [n for n, _ in seen] == [1, 2, 3]
    assert [s for _, s in seen] == [
        ChargeStatus.CONFIRMING,
        ChargeStatus.CONFIRMING,
        ChargeStatus.SETTLED,
    ]


def test_broadcast_assigns_a_transaction(chain: DemoChainProvider) -> None:
    charge = chain.create_charge(PRICE, "ord_1")
    assert chain.broadcast(charge.charge_id).tx_hash.startswith("0x")


def test_confirmations_never_exceed_the_requirement(
    chain: DemoChainProvider, clock: FakeClock
) -> None:
    charge = chain.create_charge(PRICE, "ord_1")
    chain.broadcast(charge.charge_id)
    clock.blocks(50)
    c = chain.get_charge(charge.charge_id)
    assert c.confirmations == c.required_confirmations


def test_settled_at_is_not_rewritten(chain: DemoChainProvider, clock: FakeClock) -> None:
    charge = chain.create_charge(PRICE, "ord_1")
    chain.broadcast(charge.charge_id)
    clock.blocks(3)
    first = chain.get_charge(charge.charge_id).settled_at
    clock.blocks(10)
    assert chain.get_charge(charge.charge_id).settled_at == first


def test_underpayment_never_settles(chain: DemoChainProvider, clock: FakeClock) -> None:
    """Funds arrive, blocks pass, and the charge still does not clear."""
    charge = chain.create_charge(PRICE, "ord_1")
    chain.broadcast(charge.charge_id, Money(1_000_000, Currency.USDC))
    clock.blocks(20)

    c = chain.get_charge(charge.charge_id)
    assert c.status is ChargeStatus.CONFIRMING
    assert not c.is_settled
    assert chain.received(charge.charge_id) == Money(1_000_000, Currency.USDC)


def test_overpayment_settles(chain: DemoChainProvider, clock: FakeClock) -> None:
    charge = chain.create_charge(PRICE, "ord_1")
    chain.broadcast(charge.charge_id, Money(30_000_000, Currency.USDC))
    clock.blocks(3)
    assert chain.get_charge(charge.charge_id).is_settled


def test_received_is_none_before_broadcast(chain: DemoChainProvider) -> None:
    charge = chain.create_charge(PRICE, "ord_1")
    assert chain.received(charge.charge_id) is None


def test_broadcasting_twice_does_not_restart_the_clock(
    chain: DemoChainProvider, clock: FakeClock
) -> None:
    charge = chain.create_charge(PRICE, "ord_1")
    chain.broadcast(charge.charge_id)
    clock.blocks(3)
    assert chain.get_charge(charge.charge_id).is_settled
    chain.broadcast(charge.charge_id)  # a duplicate wallet send
    assert chain.get_charge(charge.charge_id).is_settled


def test_payouts_settle(chain: DemoChainProvider) -> None:
    payout = chain.create_payout("0xcreator", Money(10_000_000, Currency.USDC), "ord_1")
    assert payout.status is ChargeStatus.SETTLED and payout.tx_hash
