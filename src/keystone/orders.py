"""Purchases and entitlement.

Certification decides what may be sold. This decides who may download it.

The rule that matters: a download URL is minted only against a paid order, and
entitlement is checked server-side every time -- never inferred from the client
holding a link. Presigned URLs expire, but a leaked one is still a copy of the
weights, so the check happens before the URL exists rather than after.

Free listings are a first-class case, not a special case. Plenty of good open
models should cost nothing, and a price of zero entitles directly without
manufacturing a fake order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from keystone.payments import Charge, Money


class OrderStatus(str, Enum):
    PENDING = "pending"      # created, charge not settled
    PAID = "paid"            # entitles download
    REFUNDED = "refunded"    # entitlement withdrawn
    CANCELLED = "cancelled"  # never paid; expired or abandoned


@dataclass
class Order:
    order_id: str
    buyer_id: str
    listing_id: str
    artifact_digest: str
    amount: Money
    created_at: datetime
    charge_id: str | None = None
    status: OrderStatus = OrderStatus.PENDING
    paid_at: datetime | None = None

    @property
    def entitles_download(self) -> bool:
        return self.status is OrderStatus.PAID


@dataclass(frozen=True)
class RevenueSplit:
    """How a sale divides. Integer arithmetic only -- cents cannot evaporate.

    The platform cut is computed and the creator receives the remainder, so
    rounding can never leave a stray unit unassigned.
    """

    take_rate_bps: int = 1500  # 15.00%

    def __post_init__(self) -> None:
        if not 0 <= self.take_rate_bps <= 10_000:
            raise ValueError("take_rate_bps must be between 0 and 10000")

    def platform_cut(self, amount: Money) -> Money:
        return Money(amount.amount_minor * self.take_rate_bps // 10_000, amount.currency)

    def creator_cut(self, amount: Money) -> Money:
        return Money(
            amount.amount_minor - self.platform_cut(amount).amount_minor, amount.currency
        )


DEFAULT_SPLIT = RevenueSplit()


class EntitlementError(RuntimeError):
    pass


@dataclass(frozen=True)
class Entitlement:
    """The verdict, plus enough for a caller to pick a status code.

    `payment_required` is set rather than left for the caller to infer from the
    wording -- deciding an HTTP code by string-matching a human message is how
    a rewording silently becomes a security change.
    """

    allowed: bool
    reason: str = ""
    payment_required: bool = False

    def __bool__(self) -> bool:
        return self.allowed


def check_entitlement(
    *,
    buyer_id: str | None,
    listing_id: str,
    listing_state: str,
    listing_creator_id: str,
    price: Money,
    order: Order | None,
) -> Entitlement:
    """May this caller download this listing's weights?

    Reasons are safe to show a user: they say what to do next, never anything
    about other buyers or internal state.
    """
    if buyer_id is None:
        return Entitlement(False, "sign in to download")

    # The creator always reaches their own artifact, at any state. They
    # uploaded it; withholding it would be theatre.
    if buyer_id == listing_creator_id:
        return Entitlement(True)

    if listing_state != "listed":
        return Entitlement(False, "this model is not published")

    if price.amount_minor == 0:
        return Entitlement(True)  # free listings entitle directly

    if order is None:
        return Entitlement(False, f"purchase required: {price}", payment_required=True)
    if order.status is OrderStatus.REFUNDED:
        return Entitlement(False, "this order was refunded")
    if order.listing_id != listing_id or order.buyer_id != buyer_id:
        # An order is entitlement for one buyer and one listing. Never let a
        # paid order for anything else unlock this one.
        return Entitlement(False, "order does not match this listing")
    if order.status is not OrderStatus.PAID:
        return Entitlement(
            False, f"payment not settled ({order.status.value})", payment_required=True
        )

    return Entitlement(True)


def settle(order: Order, charge: Charge, now: datetime) -> Order:
    """Advance an order against authoritative charge state.

    The charge is re-read from the payment provider by the caller; a client
    claiming to have paid is not evidence of payment.
    """
    if order.status is OrderStatus.PAID:
        return order  # idempotent: a replayed webhook must not double-apply

    if charge.reference != order.order_id:
        raise EntitlementError("charge does not reference this order")
    if charge.amount.currency is not order.amount.currency:
        raise EntitlementError(
            f"wrong currency: expected {order.amount.currency.value}"
        )
    if charge.amount.amount_minor < order.amount.amount_minor:
        raise EntitlementError(f"underpaid: {charge.amount} < {order.amount}")

    if charge.is_settled:
        order.status = OrderStatus.PAID
        order.paid_at = now
        order.charge_id = charge.charge_id
    return order
