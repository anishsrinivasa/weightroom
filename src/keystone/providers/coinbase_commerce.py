"""Coinbase Commerce.

Chosen for the default because it settles USDC natively on Base, charges 1% with
no monthly fee, and -- for a marketplace asking strangers to send money -- the
name clears a trust bar that a cheaper processor does not. NOWPayments is 0.5%,
which is worth nothing until there is real volume.

Two details differ from a generic hosted checkout and are the reason this needs
its own class:

**Status lives in a timeline, not a field.** A charge carries `timeline`, an
append-only list of `{status, time}` entries. The current status is the last
one. Reading a top-level `status` returns nothing and would leave every charge
looking pending forever.

**Prices are decimal strings in a nested object.** `pricing.local` holds the
amount the buyer was quoted; `pricing[<crypto>]` holds what actually settled.

The response shape below is written from Coinbase's documented charge object.
Verify it against a real sandbox call before this touches money -- run
`keystone checkout-probe`, which creates a live charge and prints the raw JSON
next to how this class reads it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta

import httpx

from keystone.payments import Charge, ChargeStatus, Currency, Money
from keystone.providers.hosted_checkout import (
    CheckoutConfig,
    CheckoutError,
    HostedCheckoutProvider,
)

API_BASE = "https://api.commerce.coinbase.com"
# Coinbase pins behaviour to a dated API version.
API_VERSION = "2018-03-22"


@dataclass(frozen=True)
class CoinbaseConfig(CheckoutConfig):
    @classmethod
    def from_env(cls) -> CoinbaseConfig | None:
        api_key = os.environ.get("COINBASE_COMMERCE_API_KEY", "").strip()
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            base_url=os.environ.get("COINBASE_COMMERCE_URL", API_BASE).rstrip("/"),
            webhook_secret=os.environ.get("COINBASE_COMMERCE_WEBHOOK_SECRET", "").strip(),
            chain=os.environ.get("KEYSTONE_CHAIN", "base"),
        )


class CoinbaseCommerceProvider(HostedCheckoutProvider):
    """Everything vendor-neutral is inherited; only the wire shape differs."""

    def __init__(self, config: CoinbaseConfig, client: httpx.Client | None = None) -> None:
        super().__init__(
            config,
            client=client
            or httpx.Client(
                base_url=config.base_url,
                timeout=config.timeout_s,
                headers={
                    # Coinbase uses its own key header, not Bearer auth.
                    "X-CC-Api-Key": config.api_key,
                    "X-CC-Version": API_VERSION,
                    "Accept": "application/json",
                },
            ),
        )

    # -- requests ---------------------------------------------------------

    def create_charge(
        self,
        amount: Money,
        reference: str,
        *,
        metadata: dict[str, str] | None = None,
        ttl: timedelta = timedelta(hours=1),
    ) -> Charge:
        payload = {
            "name": "Keystone",
            "description": f"Keystone {reference}",
            "pricing_type": "fixed_price",
            "local_price": {
                "amount": str(amount.to_decimal()),
                "currency": amount.currency.value,
            },
            # Our reference travels in metadata and comes back on every read and
            # webhook. It is what ties a payment to one order.
            "metadata": {"reference": reference, **(metadata or {})},
        }
        data = self._request("POST", "/charges/", json=payload)
        return self._map_charge(data, fallback_amount=amount, fallback_reference=reference)

    def get_charge(self, charge_id: str) -> Charge:
        return self._map_charge(self._request("GET", f"/charges/{charge_id}"))

    def create_payout(self, destination: str, amount: Money, reference: str):
        # Commerce takes payments; it does not send them. Creator payouts go
        # through a separate rail and are deliberately not faked here.
        raise CheckoutError(
            "Coinbase Commerce does not support outbound payouts; "
            "settle creator earnings through a separate rail"
        )

    # -- response shape ---------------------------------------------------

    _TIMELINE = {
        "NEW": ChargeStatus.PENDING,
        "PENDING": ChargeStatus.CONFIRMING,
        "COMPLETED": ChargeStatus.SETTLED,
        "RESOLVED": ChargeStatus.SETTLED,
        "EXPIRED": ChargeStatus.EXPIRED,
        "CANCELED": ChargeStatus.EXPIRED,
        "UNRESOLVED": ChargeStatus.CONFIRMING,
    }

    def _map_status(self, raw: str | None) -> ChargeStatus:
        # Unknown maps to pending, never settled: a vocabulary change at the
        # vendor must not hand away the catalogue.
        return self._TIMELINE.get(str(raw or "").upper(), ChargeStatus.PENDING)

    @staticmethod
    def _latest_status(data: dict) -> str | None:
        timeline = data.get("timeline") or []
        return timeline[-1].get("status") if timeline else None

    def _map_charge(
        self,
        data: dict,
        *,
        fallback_amount: Money | None = None,
        fallback_reference: str = "",
    ) -> Charge:
        charge_id = data.get("id")
        if not charge_id:
            raise CheckoutError("Coinbase response has no charge id")

        amount = fallback_amount
        local = (data.get("pricing") or {}).get("local") or {}
        if local.get("amount"):
            amount = Money.from_decimal(
                str(local["amount"]), Currency(str(local.get("currency", "USDC")).upper())
            )
        if amount is None:
            raise CheckoutError("Coinbase response has no local price")

        metadata = {str(k): str(v) for k, v in (data.get("metadata") or {}).items()}
        status = self._map_status(self._latest_status(data))

        # `addresses` is keyed by network. Prefer the configured chain, but
        # take whatever is offered rather than showing the buyer nothing.
        addresses = data.get("addresses") or {}
        address = addresses.get(self.config.chain) or next(iter(addresses.values()), None)

        return Charge(
            charge_id=str(charge_id),
            reference=metadata.get("reference", fallback_reference),
            amount=amount,
            status=status,
            checkout_url=data.get("hosted_url"),
            chain=self.config.chain,
            address=address,
            # Commerce reports settlement, not raw confirmations, so a settled
            # charge is reported as fully confirmed and nothing else is claimed.
            confirmations=1 if status is ChargeStatus.SETTLED else 0,
            required_confirmations=1,
            created_at=_ts(data.get("created_at")),
            expires_at=_ts(data.get("expires_at")),
            settled_at=_ts(data.get("confirmed_at")),
            metadata=metadata,
        )

    def charge_id_from_webhook(self, payload: dict) -> str | None:
        event = payload.get("event") or {}
        data = event.get("data") or payload.get("data") or {}
        value = data.get("id")
        return str(value) if value else None


def _ts(value):
    from keystone.providers.hosted_checkout import _timestamp

    return _timestamp(value)


def from_env() -> CoinbaseCommerceProvider | None:
    config = CoinbaseConfig.from_env()
    return CoinbaseCommerceProvider(config) if config else None
