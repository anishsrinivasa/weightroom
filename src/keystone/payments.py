"""Payment rail, behind an interface.

Crypto first: no chargebacks, which matters when the product is a file that can
be copied infinitely and reclaimed never. Card networks side with the buyer on
digital goods, so a card rail would make "download then dispute" a free attack.

Fiat is not optional forever, though. Procurement at a bank or a defence agency
pays by PO and wire and cannot send USDC, so the sovereign tier the strategy is
built around needs an invoice rail eventually. Hence the interface: swapping or
adding a provider should be a config change, not a refactor.

Money is always integer minor units. Never floats, and never assume two
decimals -- USDC has six, and getting that wrong is a 10,000x error.
"""

from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class Currency(str, Enum):
    USD = "USD"
    USDC = "USDC"
    USDT = "USDT"

    @property
    def decimals(self) -> int:
        return {"USD": 2, "USDC": 6, "USDT": 6}[self.value]

    @property
    def is_stablecoin(self) -> bool:
        return self in (Currency.USDC, Currency.USDT)


@dataclass(frozen=True)
class Money:
    """Integer minor units plus the currency that defines what a unit is."""

    amount_minor: int
    currency: Currency = Currency.USDC

    def __post_init__(self) -> None:
        if not isinstance(self.amount_minor, int):
            raise TypeError("amount_minor must be int; floats lose money")
        if self.amount_minor < 0:
            raise ValueError("amount_minor must be non-negative")

    @classmethod
    def from_decimal(cls, value: Decimal | str, currency: Currency = Currency.USDC) -> Money:
        scaled = (Decimal(value) * (10**currency.decimals)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
        return cls(int(scaled), currency)

    def to_decimal(self) -> Decimal:
        return Decimal(self.amount_minor) / (10**self.currency.decimals)

    def convert_to(self, currency: Currency) -> Money:
        """Re-scale between currencies of equal value but different precision.

        Only meaningful between pegged units (USD <-> USDC). Rates are a
        provider concern, deliberately not modelled here.
        """
        shift = currency.decimals - self.currency.decimals
        amount = (
            self.amount_minor * (10**shift) if shift >= 0 else self.amount_minor // (10**-shift)
        )
        return Money(amount, currency)

    def __str__(self) -> str:
        return f"{self.to_decimal():.{self.currency.decimals}f} {self.currency.value}"


class ChargeStatus(str, Enum):
    PENDING = "pending"        # created, nothing received
    CONFIRMING = "confirming"  # seen on chain, not yet final
    SETTLED = "settled"
    EXPIRED = "expired"
    FAILED = "failed"
    REFUNDED = "refunded"


@dataclass
class Charge:
    charge_id: str
    reference: str  # our id -- listing id, attempt id, order id
    amount: Money
    status: ChargeStatus = ChargeStatus.PENDING
    checkout_url: str | None = None
    # Crypto-specific. A card provider leaves these null.
    chain: str | None = None
    address: str | None = None
    tx_hash: str | None = None
    confirmations: int = 0
    required_confirmations: int = 1
    created_at: datetime | None = None
    expires_at: datetime | None = None
    settled_at: datetime | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def is_settled(self) -> bool:
        return self.status is ChargeStatus.SETTLED

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at


@dataclass
class Payout:
    payout_id: str
    destination: str  # wallet address, or a provider-side account id
    amount: Money
    status: ChargeStatus = ChargeStatus.PENDING
    tx_hash: str | None = None
    created_at: datetime | None = None


class RefundNotSupported(RuntimeError):
    """On-chain transfers are irreversible; refunds are a fresh outbound payout."""


class PaymentProvider(abc.ABC):
    """Same shape as ArtifactStore and ModelClient: swap the vendor, not the code."""

    @abc.abstractmethod
    def create_charge(
        self,
        amount: Money,
        reference: str,
        *,
        metadata: dict[str, str] | None = None,
        ttl: timedelta = timedelta(hours=1),
    ) -> Charge: ...

    @abc.abstractmethod
    def get_charge(self, charge_id: str) -> Charge:
        """Authoritative status. Never trust a client-reported settlement."""

    @abc.abstractmethod
    def create_payout(self, destination: str, amount: Money, reference: str) -> Payout: ...

    def refund(self, charge_id: str, amount: Money | None = None) -> Payout:
        raise RefundNotSupported(
            "this provider cannot reverse a charge; issue a payout instead"
        )


class MockPaymentProvider(PaymentProvider):
    """In-memory provider for development and tests.

    `settle()` and `expire()` stand in for what a webhook or chain watcher
    would otherwise drive.
    """

    def __init__(self, *, required_confirmations: int = 1) -> None:
        self.charges: dict[str, Charge] = {}
        self.payouts: dict[str, Payout] = {}
        self.required_confirmations = required_confirmations
        self._clock = datetime(2026, 9, 5, 12, 0)

    def create_charge(
        self,
        amount: Money,
        reference: str,
        *,
        metadata: dict[str, str] | None = None,
        ttl: timedelta = timedelta(hours=1),
    ) -> Charge:
        charge_id = f"ch_{uuid.uuid4().hex[:16]}"
        charge = Charge(
            charge_id=charge_id,
            reference=reference,
            amount=amount,
            chain="base" if amount.currency.is_stablecoin else None,
            address=f"0x{uuid.uuid4().hex[:40]}" if amount.currency.is_stablecoin else None,
            required_confirmations=self.required_confirmations,
            checkout_url=f"https://pay.invalid/{charge_id}",
            created_at=self._clock,
            expires_at=self._clock + ttl,
            metadata=metadata or {},
        )
        self.charges[charge_id] = charge
        return charge

    def get_charge(self, charge_id: str) -> Charge:
        return self.charges[charge_id]

    def create_payout(self, destination: str, amount: Money, reference: str) -> Payout:
        payout_id = f"po_{uuid.uuid4().hex[:16]}"
        payout = Payout(
            payout_id=payout_id,
            destination=destination,
            amount=amount,
            status=ChargeStatus.SETTLED,
            tx_hash=f"0x{uuid.uuid4().hex}",
            created_at=self._clock,
        )
        self.payouts[payout_id] = payout
        return payout

    # -- test/dev drivers -------------------------------------------------

    def confirm(self, charge_id: str, confirmations: int = 1) -> Charge:
        charge = self.charges[charge_id]
        charge.confirmations = confirmations
        charge.tx_hash = charge.tx_hash or f"0x{uuid.uuid4().hex}"
        if confirmations >= charge.required_confirmations:
            charge.status = ChargeStatus.SETTLED
            charge.settled_at = self._clock
        else:
            charge.status = ChargeStatus.CONFIRMING
        return charge

    def settle(self, charge_id: str) -> Charge:
        return self.confirm(charge_id, self.charges[charge_id].required_confirmations)

    def expire(self, charge_id: str) -> Charge:
        charge = self.charges[charge_id]
        charge.status = ChargeStatus.EXPIRED
        return charge


class HostedCryptoProvider(PaymentProvider):
    """Adapter for a hosted stablecoin checkout (Coinbase Commerce class).

    Deliberately thin. Custody, key management, and chain watching are a RENT --
    self-custody is the most reliable way to lose the money.

    The three `_request` calls below need the chosen vendor's current API shape
    filled in; everything above this line is vendor-independent.
    """

    def __init__(self, api_key: str, base_url: str, chain: str = "base") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.chain = chain

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        raise NotImplementedError(
            "wire this to the chosen provider's REST API before going live"
        )

    def create_charge(
        self,
        amount: Money,
        reference: str,
        *,
        metadata: dict[str, str] | None = None,
        ttl: timedelta = timedelta(hours=1),
    ) -> Charge:
        raise NotImplementedError

    def get_charge(self, charge_id: str) -> Charge:
        raise NotImplementedError

    def create_payout(self, destination: str, amount: Money, reference: str) -> Payout:
        raise NotImplementedError
