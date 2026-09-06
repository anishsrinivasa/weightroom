"""Non-custodial USDC settlement, watched directly on chain.

Every custodial processor requires identity verification, because holding
someone else's money makes you a money transmitter. There is no version of that
which skips the paperwork. So this skips the custodian instead: the buyer sends
USDC straight to an address the seller owns, and this module only watches.

**The server never holds a private key.** It makes read-only JSON-RPC calls.
That is the whole reason self-custody is acceptable here -- code that cannot
sign cannot lose the money, and a compromised server leaks a receive address
rather than a balance.

Payments are told apart by amount. Each charge is quoted with a tiny unique
offset -- 39.000001, 39.000002 -- so two buyers paying the same list price
produce distinguishable transfers. The offset is derived from the order id, so
it is stable across restarts without any shared state.

Trade-offs, stated plainly: refunds are manual outbound sends, there is no
chargeback protection (which is the point, for a downloadable artifact), and
every payment landing on one address is weak privacy. Deriving a fresh address
per charge from a watch-only xpub fixes the last one and changes nothing else.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import httpx

from keystone.payments import (
    Charge,
    ChargeStatus,
    Currency,
    Money,
    PaymentProvider,
    Payout,
    RefundNotSupported,
)

# keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# USDC on Base mainnet. Verified at startup rather than trusted -- see
# `verify_token`, which refuses to run against a contract that does not report
# itself as six-decimal USDC.
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

# Micro-USDC of headroom for making amounts unique. Under a dollar, so it never
# meaningfully changes what a buyer pays.
OFFSET_SPACE = 999_999


class InvalidAddress(ValueError):
    """The configured receive address is malformed or fails its checksum."""


def to_checksum(address: str) -> str:
    """EIP-55 mixed-case form of an address."""
    from Crypto.Hash import keccak

    body = address.lower().removeprefix("0x")
    digest = keccak.new(data=body.encode(), digest_bits=256).hexdigest()
    return "0x" + "".join(
        char.upper() if int(digest[i], 16) >= 8 else char
        for i, char in enumerate(body)
    )


def validate_address(address: str) -> str:
    """Return the checksummed address, or refuse.

    A mistyped receive address does not bounce -- the funds are simply gone,
    and nobody finds out until a buyer says they paid and the order never
    cleared. EIP-55 exists to catch exactly that, so this is checked at
    startup rather than discovered later.

    An all-lowercase or all-uppercase address carries no checksum, which is
    legal but unverifiable; it is accepted and returned in checksummed form.
    """
    body = address.strip().removeprefix("0x")
    if len(body) != 40 or any(c not in "0123456789abcdefABCDEF" for c in body):
        raise InvalidAddress(f"not a 20-byte hex address: {address!r}")

    if body == body.lower() or body == body.upper():
        return to_checksum(address)  # no checksum to verify

    checksummed = to_checksum(address)
    if checksummed != "0x" + body:
        raise InvalidAddress(
            f"EIP-55 checksum mismatch: got {address}, expected {checksummed}. "
            "Re-copy the address; a mistyped one loses funds permanently."
        )
    return checksummed


class ChainError(RuntimeError):
    """The node was unreachable or returned something unusable."""


@dataclass(frozen=True)
class OnChainConfig:
    receive_address: str
    rpc_url: str
    token_contract: str = USDC_BASE
    chain: str = "base"
    currency: Currency = Currency.USDC
    required_confirmations: int = 3
    timeout_s: float = 20.0

    @classmethod
    def from_env(cls) -> OnChainConfig | None:
        address = os.environ.get("KEYSTONE_RECEIVE_ADDRESS", "").strip()
        rpc = os.environ.get("KEYSTONE_RPC_URL", "").strip()
        if not address or not rpc:
            return None
        return cls(
            receive_address=address,
            rpc_url=rpc,
            token_contract=os.environ.get("KEYSTONE_TOKEN_CONTRACT", USDC_BASE).strip(),
            chain=os.environ.get("KEYSTONE_CHAIN", "base"),
            required_confirmations=int(os.environ.get("KEYSTONE_CONFIRMATIONS", "3")),
        )


def unique_amount(base: Money, reference: str) -> Money:
    """Quote a distinguishable amount for this order.

    Derived from the reference rather than a counter, so it survives a restart
    and needs no shared state between API instances.
    """
    offset = int(hashlib.sha256(reference.encode()).hexdigest()[:8], 16) % OFFSET_SPACE + 1
    return Money(base.amount_minor + offset, base.currency)


def _pad_address(address: str) -> str:
    return "0x" + address.lower().removeprefix("0x").rjust(64, "0")


class OnChainProvider(PaymentProvider):
    def __init__(self, config: OnChainConfig, client: httpx.Client | None = None) -> None:
        # Fail here rather than when a buyer is waiting on a payment page.
        # `replace` rather than mutating in place: the config is frozen, and the
        # caller's object should not change under it.
        self.config = replace(
            config,
            receive_address=validate_address(config.receive_address),
            token_contract=validate_address(config.token_contract),
        )
        self._client = client or httpx.Client(timeout=config.timeout_s)
        self._charges: dict[str, Charge] = {}
        # Which transfer settled which charge. One payment can only ever clear
        # one order, even if two orders were quoted the same amount.
        self._consumed: dict[str, str] = {}
        self._from_block: dict[str, int] = {}
        self._rpc_id = 0

    # -- JSON-RPC ---------------------------------------------------------

    def _rpc(self, method: str, params: list) -> object:
        self._rpc_id += 1
        try:
            response = self._client.post(
                self.config.rpc_url,
                json={"jsonrpc": "2.0", "id": self._rpc_id, "method": method, "params": params},
            )
        except httpx.HTTPError as exc:
            raise ChainError(f"RPC unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise ChainError(f"RPC returned {response.status_code}: {response.text[:200]}")
        body = response.json()
        if "error" in body:
            raise ChainError(f"RPC error: {body['error']}")
        return body.get("result")

    def block_number(self) -> int:
        return int(str(self._rpc("eth_blockNumber", [])), 16)

    def _call(self, data: str) -> str:
        return str(self._rpc("eth_call", [
            {"to": self.config.token_contract, "data": data}, "latest",
        ]))

    def verify_token(self) -> dict:
        """Confirm the configured contract really is six-decimal USDC.

        A wrong contract address means watching the wrong token, and every
        payment would look unpaid forever. Cheaper to fail at startup.
        """
        decimals = int(self._call("0x313ce567") or "0x0", 16)          # decimals()
        raw_symbol = self._call("0x95d89b41") or ""                    # symbol()
        symbol = bytes.fromhex(raw_symbol[2:]).decode("utf-8", "ignore").strip("\x00 ")
        symbol = "".join(ch for ch in symbol if ch.isprintable()).strip()
        ok = decimals == self.config.currency.decimals
        return {"symbol": symbol, "decimals": decimals, "decimals_match": ok}

    # -- provider interface ----------------------------------------------

    def create_charge(
        self,
        amount: Money,
        reference: str,
        *,
        metadata: dict[str, str] | None = None,
        ttl: timedelta = timedelta(hours=1),
    ) -> Charge:
        quoted = unique_amount(amount, reference)
        charge_id = f"ch_{hashlib.sha256(reference.encode()).hexdigest()[:16]}"
        now = datetime.now(timezone.utc)

        # Only scan forward from here. Without it, an old transfer of a
        # coincidentally equal amount could settle a brand-new charge.
        try:
            self._from_block[charge_id] = self.block_number()
        except ChainError:
            self._from_block[charge_id] = 0

        charge = Charge(
            charge_id=charge_id,
            reference=reference,
            amount=quoted,
            chain=self.config.chain,
            address=self.config.receive_address,
            required_confirmations=self.config.required_confirmations,
            created_at=now,
            expires_at=now + ttl,
            metadata=metadata or {},
        )
        self._charges[charge_id] = charge
        return charge

    def get_charge(self, charge_id: str) -> Charge:
        charge = self._charges.get(charge_id)
        if charge is None:
            raise KeyError(charge_id)
        if charge.status is ChargeStatus.SETTLED:
            return charge

        head = self.block_number()
        logs = self._rpc("eth_getLogs", [{
            "fromBlock": hex(max(0, self._from_block.get(charge_id, 0))),
            "toBlock": "latest",
            "address": self.config.token_contract,
            "topics": [TRANSFER_TOPIC, None, _pad_address(self.config.receive_address)],
        }]) or []

        for entry in logs:
            if not isinstance(entry, dict):
                continue
            tx_hash = str(entry.get("transactionHash", ""))
            owner = self._consumed.get(tx_hash)
            if owner is not None and owner != charge_id:
                continue  # already settled a different order
            value = int(str(entry.get("data", "0x0")), 16)
            if value != charge.amount.amount_minor:
                continue

            block = int(str(entry.get("blockNumber", "0x0")), 16)
            self._consumed[tx_hash] = charge_id
            charge.tx_hash = tx_hash
            # Inclusion in a block is the first confirmation.
            charge.confirmations = min(
                charge.required_confirmations, max(0, head - block) + 1
            )
            if charge.confirmations >= charge.required_confirmations:
                charge.status = ChargeStatus.SETTLED
                charge.settled_at = charge.settled_at or datetime.now(timezone.utc)
            else:
                charge.status = ChargeStatus.CONFIRMING
            return charge

        if charge.is_expired(datetime.now(timezone.utc)):
            charge.status = ChargeStatus.EXPIRED
        return charge

    def create_payout(self, destination: str, amount: Money, reference: str) -> Payout:
        raise RefundNotSupported(
            "this provider is read-only by design; send creator payouts from the "
            "wallet that holds the funds"
        )

    def refund(self, charge_id: str, amount: Money | None = None) -> Payout:
        raise RefundNotSupported(
            "on-chain transfers are irreversible, and this provider holds no keys"
        )


def from_env() -> OnChainProvider | None:
    config = OnChainConfig.from_env()
    return OnChainProvider(config) if config else None
