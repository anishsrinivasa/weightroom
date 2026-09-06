"""Hosted stablecoin checkout.

The vendor holds custody, runs the chain watcher, and gives the buyer a payment
page. We keep the ledger and the entitlement. That split is deliberate: custody
is not the moat, and self-custody is the most reliable way to lose the money.

Two rules survive from the demo provider and matter more here, not less:

**A webhook is a hint, never evidence.** Anyone can POST to a webhook URL. Even
with a valid signature, this implementation treats the callback purely as a
prompt to go and re-read the charge from the vendor's API. Nothing settles on
the strength of a payload someone sent us.

**Only the vendor's own status settles a charge.** No local inference from
amounts or timestamps.

The vendor's exact field names live in the small `_map_*` methods at the bottom.
Everything above them is vendor-independent, so switching processors is a
question of rewriting three short functions.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
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


class CheckoutError(RuntimeError):
    """The processor rejected a request or returned something unusable."""


@dataclass(frozen=True)
class CheckoutConfig:
    api_key: str
    base_url: str
    webhook_secret: str = ""
    chain: str = "base"
    currency: Currency = Currency.USDC
    timeout_s: float = 15.0

    @classmethod
    def from_env(cls) -> CheckoutConfig | None:
        api_key = os.environ.get("KEYSTONE_CHECKOUT_API_KEY", "").strip()
        base_url = os.environ.get("KEYSTONE_CHECKOUT_URL", "").strip()
        if not api_key or not base_url:
            return None
        return cls(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            webhook_secret=os.environ.get("KEYSTONE_CHECKOUT_WEBHOOK_SECRET", "").strip(),
            chain=os.environ.get("KEYSTONE_CHAIN", "base"),
        )


def verify_webhook(secret: str, raw_body: bytes, signature: str) -> bool:
    """Constant-time HMAC-SHA256 check over the exact bytes received.

    Verify against the raw body, never a re-serialised dict: re-encoding JSON
    can reorder keys or change spacing and silently invalidate a good
    signature -- or, worse, validate a payload that is not the one signed.

    A passing signature still does not settle anything. It only tells us the
    callback is worth acting on.
    """
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    candidate = signature.strip().removeprefix("sha256=")
    return hmac.compare_digest(expected, candidate)


class HostedCheckoutProvider(PaymentProvider):
    def __init__(self, config: CheckoutConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client or httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_s,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Accept": "application/json",
            },
        )

    # -- provider interface ----------------------------------------------

    def create_charge(
        self,
        amount: Money,
        reference: str,
        *,
        metadata: dict[str, str] | None = None,
        ttl: timedelta = timedelta(hours=1),
    ) -> Charge:
        payload = {
            "amount": str(amount.to_decimal()),
            "currency": amount.currency.value,
            "chain": self.config.chain,
            # Our id, echoed back on every read and callback. It is what ties a
            # payment to one order and stops a charge settling a different one.
            "reference": reference,
            "expires_in_seconds": int(ttl.total_seconds()),
            "metadata": metadata or {},
        }
        data = self._request("POST", "/charges", json=payload)
        return self._map_charge(data, fallback_amount=amount, fallback_reference=reference)

    def get_charge(self, charge_id: str) -> Charge:
        return self._map_charge(self._request("GET", f"/charges/{charge_id}"))

    def create_payout(self, destination: str, amount: Money, reference: str) -> Payout:
        data = self._request(
            "POST",
            "/payouts",
            json={
                "destination": destination,
                "amount": str(amount.to_decimal()),
                "currency": amount.currency.value,
                "chain": self.config.chain,
                "reference": reference,
            },
        )
        return self._map_payout(data, fallback_amount=amount, destination=destination)

    def refund(self, charge_id: str, amount: Money | None = None) -> Payout:
        raise RefundNotSupported(
            "on-chain transfers are irreversible; refund by issuing a payout"
        )

    # -- webhook ----------------------------------------------------------

    def charge_id_from_webhook(self, payload: dict) -> str | None:
        """Pull the charge id out of a callback and nothing else.

        The rest of the payload is deliberately ignored. Whatever it claims
        about status or amount, the caller re-reads the charge from the API.
        """
        data = payload.get("data") or payload
        value = data.get("charge_id") or data.get("id")
        return str(value) if value else None

    # -- transport --------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise CheckoutError(f"payment processor unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise CheckoutError(
                f"payment processor returned {response.status_code}: {response.text[:200]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise CheckoutError("payment processor returned a non-JSON body") from exc
        return body.get("data", body) if isinstance(body, dict) else {}

    # -- vendor field mapping ---------------------------------------------
    # Everything below is the only vendor-specific code. Adjust the field
    # names and status vocabulary for the processor in use.

    _STATUS = {
        "new": ChargeStatus.PENDING,
        "pending": ChargeStatus.PENDING,
        "created": ChargeStatus.PENDING,
        "detected": ChargeStatus.CONFIRMING,
        "confirming": ChargeStatus.CONFIRMING,
        "unresolved": ChargeStatus.CONFIRMING,
        "completed": ChargeStatus.SETTLED,
        "confirmed": ChargeStatus.SETTLED,
        "resolved": ChargeStatus.SETTLED,
        "expired": ChargeStatus.EXPIRED,
        "canceled": ChargeStatus.EXPIRED,
        "cancelled": ChargeStatus.EXPIRED,
        "failed": ChargeStatus.FAILED,
    }

    def _map_status(self, raw: str | None) -> ChargeStatus:
        # An unrecognised status is treated as still pending rather than
        # settled. Guessing in the payer's favour is how a marketplace gives
        # away the catalogue.
        return self._STATUS.get(str(raw or "").lower(), ChargeStatus.PENDING)

    def _map_charge(
        self,
        data: dict,
        *,
        fallback_amount: Money | None = None,
        fallback_reference: str = "",
    ) -> Charge:
        charge_id = data.get("id") or data.get("charge_id")
        if not charge_id:
            raise CheckoutError("payment processor response has no charge id")

        amount = fallback_amount
        raw_amount = data.get("amount")
        if raw_amount is not None:
            currency = Currency(str(data.get("currency", self.config.currency.value)).upper())
            amount = Money.from_decimal(str(raw_amount), currency)
        if amount is None:
            raise CheckoutError("payment processor response has no amount")

        return Charge(
            charge_id=str(charge_id),
            reference=str(data.get("reference") or fallback_reference),
            amount=amount,
            status=self._map_status(data.get("status")),
            checkout_url=data.get("hosted_url") or data.get("checkout_url"),
            chain=data.get("chain") or self.config.chain,
            address=data.get("address") or data.get("deposit_address"),
            tx_hash=data.get("tx_hash") or data.get("transaction_hash"),
            confirmations=int(data.get("confirmations") or 0),
            required_confirmations=int(data.get("required_confirmations") or 1),
            created_at=_timestamp(data.get("created_at")),
            expires_at=_timestamp(data.get("expires_at")),
            settled_at=_timestamp(data.get("settled_at") or data.get("confirmed_at")),
            metadata={str(k): str(v) for k, v in (data.get("metadata") or {}).items()},
        )

    def _map_payout(self, data: dict, *, fallback_amount: Money, destination: str) -> Payout:
        return Payout(
            payout_id=str(data.get("id") or data.get("payout_id") or ""),
            destination=str(data.get("destination") or destination),
            amount=fallback_amount,
            status=self._map_status(data.get("status")),
            tx_hash=data.get("tx_hash") or data.get("transaction_hash"),
            created_at=_timestamp(data.get("created_at")),
        )


def _timestamp(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def from_env() -> HostedCheckoutProvider | None:
    config = CheckoutConfig.from_env()
    return HostedCheckoutProvider(config) if config else None
