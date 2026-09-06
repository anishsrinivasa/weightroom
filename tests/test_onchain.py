"""Non-custodial on-chain settlement.

The properties that matter: only a real transfer of the exact quoted amount
settles a charge, one payment can never clear two orders, and the provider
cannot move funds even if it wanted to.
"""

from __future__ import annotations

import httpx
import pytest

from keystone.payments import ChargeStatus, Currency, Money, RefundNotSupported
from keystone.providers.onchain import (
    TRANSFER_TOPIC,
    ChainError,
    OnChainConfig,
    OnChainProvider,
    unique_amount,
)

RECEIVE = "0x1111111111111111111111111111111111111111"
TOKEN = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
PRICE = Money(39_000_000, Currency.USDC)


class FakeChain:
    """A node that answers the three calls the provider makes."""

    def __init__(self, head: int = 1_000, decimals: int = 6, symbol: str = "USDC") -> None:
        self.head = head
        self.decimals = decimals
        self.symbol = symbol
        self.logs: list[dict] = []
        self.calls: list[str] = []

    def transfer(self, amount_minor: int, block: int, tx: str) -> None:
        self.logs.append({
            "transactionHash": tx,
            "blockNumber": hex(block),
            "data": hex(amount_minor),
        })

    def __call__(self, request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        method = body["method"]
        self.calls.append(method)

        if method == "eth_blockNumber":
            result = hex(self.head)
        elif method == "eth_getLogs":
            params = body["params"][0]
            assert params["topics"][0] == TRANSFER_TOPIC
            start = int(params["fromBlock"], 16)
            result = [e for e in self.logs if int(e["blockNumber"], 16) >= start]
        elif method == "eth_call":
            data = body["params"][0]["data"]
            if data == "0x313ce567":  # decimals()
                result = hex(self.decimals)
            else:  # symbol()
                result = "0x" + self.symbol.encode().hex()
        else:
            return httpx.Response(200, json={"jsonrpc": "2.0", "error": {"message": method}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


def make(chain: FakeChain, **overrides) -> OnChainProvider:
    settings = {
        "receive_address": RECEIVE,
        "rpc_url": "https://rpc.invalid",
        "token_contract": TOKEN,
        "required_confirmations": 3,
    }
    settings.update(overrides)
    config = OnChainConfig(**settings)
    return OnChainProvider(
        config, client=httpx.Client(transport=httpx.MockTransport(chain))
    )


# --------------------------------------------------------------------------
# telling payments apart
# --------------------------------------------------------------------------

def test_each_order_is_quoted_a_distinguishable_amount() -> None:
    a = unique_amount(PRICE, "ord_1")
    b = unique_amount(PRICE, "ord_2")
    assert a != b
    assert a.amount_minor > PRICE.amount_minor
    assert b.amount_minor > PRICE.amount_minor


def test_the_offset_never_costs_the_buyer_a_dollar() -> None:
    quoted = unique_amount(PRICE, "ord_1")
    assert quoted.amount_minor - PRICE.amount_minor <= 999_999


def test_the_same_order_is_always_quoted_the_same_amount() -> None:
    """Derived from the reference, so it survives a restart with no state."""
    assert unique_amount(PRICE, "ord_1") == unique_amount(PRICE, "ord_1")


# --------------------------------------------------------------------------
# settlement
# --------------------------------------------------------------------------

def test_a_new_charge_is_unpaid_and_names_the_receive_address() -> None:
    provider = make(FakeChain())
    charge = provider.create_charge(PRICE, "ord_1")
    assert charge.status is ChargeStatus.PENDING
    assert charge.address == RECEIVE
    assert charge.chain == "base"


def test_nothing_settles_without_a_transfer() -> None:
    provider = make(FakeChain())
    charge = provider.create_charge(PRICE, "ord_1")
    assert provider.get_charge(charge.charge_id).status is ChargeStatus.PENDING


def test_an_exact_transfer_confirms_then_settles() -> None:
    chain = FakeChain(head=1_000)
    provider = make(chain)
    charge = provider.create_charge(PRICE, "ord_1")

    chain.transfer(charge.amount.amount_minor, block=1_001, tx="0xaaa")
    chain.head = 1_001
    seen = provider.get_charge(charge.charge_id)
    assert seen.status is ChargeStatus.CONFIRMING and seen.confirmations == 1

    chain.head = 1_003
    settled = provider.get_charge(charge.charge_id)
    assert settled.status is ChargeStatus.SETTLED
    assert settled.tx_hash == "0xaaa"


def test_a_wrong_amount_does_not_settle() -> None:
    """The offset is the whole mechanism; an amount that is off by one is not this order."""
    chain = FakeChain()
    provider = make(chain)
    charge = provider.create_charge(PRICE, "ord_1")
    chain.transfer(charge.amount.amount_minor - 1, block=1_001, tx="0xaaa")
    chain.head = 1_010
    assert provider.get_charge(charge.charge_id).status is ChargeStatus.PENDING


def test_underpayment_does_not_settle() -> None:
    chain = FakeChain()
    provider = make(chain)
    charge = provider.create_charge(PRICE, "ord_1")
    chain.transfer(1_000_000, block=1_001, tx="0xaaa")
    chain.head = 1_010
    assert not provider.get_charge(charge.charge_id).is_settled


def test_one_payment_cannot_settle_two_orders() -> None:
    """Two orders quoted the same amount must not both clear on one transfer."""
    chain = FakeChain()
    provider = make(chain)
    first = provider.create_charge(PRICE, "ord_1")
    second = provider.create_charge(PRICE, "ord_2")
    # Force the collision the offsets are meant to prevent.
    second.amount = first.amount

    chain.transfer(first.amount.amount_minor, block=1_001, tx="0xaaa")
    chain.head = 1_010

    assert provider.get_charge(first.charge_id).is_settled
    assert not provider.get_charge(second.charge_id).is_settled


def test_transfers_before_the_charge_existed_are_ignored() -> None:
    """Otherwise an old payment of a coincidentally equal amount clears a new order."""
    chain = FakeChain(head=1_000)
    chain.transfer(PRICE.amount_minor + 1, block=500, tx="0xold")
    provider = make(chain)
    charge = provider.create_charge(PRICE, "ord_1")
    chain.head = 1_010
    assert provider.get_charge(charge.charge_id).status is ChargeStatus.PENDING


def test_a_settled_charge_stops_querying_the_chain() -> None:
    chain = FakeChain()
    provider = make(chain)
    charge = provider.create_charge(PRICE, "ord_1")
    chain.transfer(charge.amount.amount_minor, block=1_001, tx="0xaaa")
    chain.head = 1_010
    provider.get_charge(charge.charge_id)

    before = len(chain.calls)
    provider.get_charge(charge.charge_id)
    assert len(chain.calls) == before


def test_an_unreachable_node_raises_rather_than_settling() -> None:
    def down(request):
        raise httpx.ConnectError("no route")

    provider = OnChainProvider(
        OnChainConfig(receive_address=RECEIVE, rpc_url="https://rpc.invalid"),
        client=httpx.Client(transport=httpx.MockTransport(down)),
    )
    charge = provider.create_charge(PRICE, "ord_1")
    with pytest.raises(ChainError, match="unreachable"):
        provider.get_charge(charge.charge_id)


# --------------------------------------------------------------------------
# the contract we are watching
# --------------------------------------------------------------------------

def test_token_verification_reports_symbol_and_decimals() -> None:
    token = make(FakeChain()).verify_token()
    assert token["symbol"] == "USDC"
    assert token["decimals"] == 6
    assert token["decimals_match"] is True


def test_a_wrong_contract_is_detected() -> None:
    """Watching an 18-decimal token would make every payment look unpaid."""
    token = make(FakeChain(decimals=18, symbol="WETH")).verify_token()
    assert token["decimals_match"] is False


# --------------------------------------------------------------------------
# it cannot move money
# --------------------------------------------------------------------------

def test_payouts_are_refused() -> None:
    provider = make(FakeChain())
    with pytest.raises(RefundNotSupported, match="read-only"):
        provider.create_payout("0xcreator", PRICE, "ord_1")


def test_refunds_are_refused() -> None:
    provider = make(FakeChain())
    with pytest.raises(RefundNotSupported):
        provider.refund("ch_1")


def test_the_provider_holds_no_key_material() -> None:
    """A compromised server should leak a receive address, not a balance."""
    provider = make(FakeChain())
    blob = repr(vars(provider)) + repr(vars(provider.config))
    for word in ("private", "secret", "mnemonic", "seed", "privkey"):
        assert word not in blob.lower()


# --------------------------------------------------------------------------
# receive address validation
# --------------------------------------------------------------------------

def test_a_valid_checksummed_address_is_accepted() -> None:
    from keystone.providers.onchain import validate_address

    address = "0x8CF9aABe652ECE5Fe812fCEAF5763A27c7C44F72"
    assert validate_address(address) == address


def test_a_single_wrong_character_is_caught() -> None:
    """A mistyped address does not bounce; the funds are simply gone."""
    from keystone.providers.onchain import InvalidAddress, validate_address

    # Same address with one hex digit changed -- still valid hex, wrong checksum.
    with pytest.raises(InvalidAddress, match="checksum"):
        validate_address("0x8CF9aABe652ECE5Fe812fCEAF5763A27c7C44F73")


def test_case_only_damage_is_caught() -> None:
    from keystone.providers.onchain import InvalidAddress, validate_address

    with pytest.raises(InvalidAddress, match="checksum"):
        validate_address("0x8cF9aABe652ECE5Fe812fCEAF5763A27c7C44F72")


def test_an_unchecksummed_address_is_normalised_not_rejected() -> None:
    """All-lowercase is legal and carries no checksum to verify."""
    from keystone.providers.onchain import validate_address

    assert validate_address("0x8cf9aabe652ece5fe812fceaf5763a27c7c44f72") == (
        "0x8CF9aABe652ECE5Fe812fCEAF5763A27c7C44F72"
    )


@pytest.mark.parametrize(
    "bad",
    ["0x123", "", "not-an-address", "0x" + "z" * 40, "0x" + "a" * 41],
)
def test_malformed_addresses_are_rejected(bad: str) -> None:
    from keystone.providers.onchain import InvalidAddress, validate_address

    with pytest.raises(InvalidAddress):
        validate_address(bad)


def test_a_bad_address_fails_at_construction_not_at_payment_time() -> None:
    from keystone.providers.onchain import InvalidAddress

    with pytest.raises(InvalidAddress):
        make(FakeChain(), receive_address="0xnope")
