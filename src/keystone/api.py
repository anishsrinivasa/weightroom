"""HTTP API.

Thin by design. It validates, writes rows, and hands slow work to the worker --
no request thread ever waits on a GPU. Certification takes minutes; a publish
call returns in milliseconds with the listing in `pending_certification`.

The security-critical part is the boundary: every report leaving this process
goes through `redact()` for an audience derived from the authenticated
principal, and `assert_no_leak()` re-checks the result. Belt and braces,
because a leak here is not a bug, it is the end of the moat.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from keystone.auth import Authenticator, Principal, audience_for
from keystone.benchmarks import SelectionError, declined_ids, menu, normalise_selection, quote
from keystone.db import ArtifactRow, ListingRow, Store, UserRow
from keystone.listing import (
    DEFAULT_POLICY,
    AttemptPolicy,
    ListingState,
    TransitionError,
    transition,
)
from keystone.orders import (
    DEFAULT_SPLIT,
    Order,
    OrderStatus,
    RevenueSplit,
    check_entitlement,
    settle,
)
from keystone.payments import DemoChainProvider, PaymentProvider
from keystone.schema import Audience, CertificationReport, FileEntry
from keystone.safety_gates import summarize as summarize_safety_gates
from keystone.storage import ArtifactStore, LocalStore, artifact_key
from keystone.visibility import assert_no_leak, redact


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------

class Deps:
    """Swappable services. Tests build one of these with fakes."""

    def __init__(
        self,
        store: Store,
        artifacts: ArtifactStore,
        payments: PaymentProvider,
        auth: Authenticator,
        policy: AttemptPolicy = DEFAULT_POLICY,
        signer=None,
        split: RevenueSplit = DEFAULT_SPLIT,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.payments = payments
        self.auth = auth
        self.policy = policy
        self.signer = signer
        self.split = split


def get_deps(request: Request) -> Deps:
    return request.app.state.deps


D = Annotated[Deps, Depends(get_deps)]


def get_principal(
    d: D, authorization: Annotated[str | None, Header()] = None
) -> Principal | None:
    """An invalid token is anonymous, not an error -- browsing stays open."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return d.auth.principal_for(authorization.split(" ", 1)[1].strip())


P = Annotated[Principal | None, Depends(get_principal)]


def require(principal: Principal | None) -> Principal:
    if principal is None:
        raise HTTPException(401, "authentication required")
    return principal


def _view(
    d: Deps, report: CertificationReport, principal: Principal | None, creator_id: str | None
) -> dict:
    """The only way a report leaves this process."""
    audience = audience_for(principal, creator_id)
    view = redact(report, audience)
    if audience is Audience.BUYER:
        # A listed model is already proof that every mandatory gate passed.
        # Buyers see capability evidence; the gate implementation and its
        # failure taxonomy belong in the seller workspace only.
        view.suite_results = [result for result in view.suite_results if not result.gate]
    assert_no_leak(view, audience)  # belt and braces
    payload = {
        "audience": audience.value,
        "report": view.model_dump(mode="json"),
    }
    if audience is Audience.CREATOR:
        payload["safety_gates"] = summarize_safety_gates(view)
    return payload


def _row_or_404(d: Deps, s: Session, listing_id: str) -> ListingRow:
    row = d.store.get_listing(s, listing_id)
    if row is None:
        raise HTTPException(404, "no such listing")
    return row


def create_app(deps: Deps) -> FastAPI:
    app = FastAPI(title="Keystone", version="0.2.0")
    app.state.deps = deps

    # ----------------------------------------------------------------------
    # artifacts: declare -> upload direct to store -> finalize
    # ----------------------------------------------------------------------

    @app.post("/v1/artifacts", status_code=200)
    def declare_artifact(body: DeclareArtifact, d: D, principal: P) -> dict:
        """Declare a manifest, get presigned PUT URLs for the parts we lack.

        Weights never cross this API. The client uploads straight to object
        storage. Files we already hold come back with no URL -- that is the
        content-addressed dedup showing through to the creator as "instant".
        """
        require(principal)
        files = [FileEntry(**f.model_dump()) for f in body.files]
        digest = body.digest

        uploads: dict[str, str] = {}
        have = 0
        for entry in files:
            key = artifact_key(digest, entry.path)
            if d.artifacts.exists(key):
                have += 1
            else:
                uploads[entry.path] = d.artifacts.presign_put(key)

        return {
            "digest": digest,
            "already_stored": have,
            "upload_urls": uploads,
            "complete": not uploads,
        }

    @app.post("/v1/artifacts/{digest}/finalize", status_code=201)
    def finalize_artifact(digest: str, body: DeclareArtifact, d: D, principal: P) -> dict:
        require(principal)
        files = [FileEntry(**f.model_dump()) for f in body.files]

        missing = [f.path for f in files if not d.artifacts.exists(artifact_key(digest, f.path))]
        if missing:
            raise HTTPException(409, f"not uploaded: {missing[:10]}")

        # Hashes are enforced at materialize time, before the weights are ever
        # loaded -- a mismatch is a hard error there, not a warning. Re-hashing
        # multi-GB objects on every finalize would double the ingest cost for a
        # check we already make at the only moment it matters.
        with d.store.session() as s:
            d.store.put_artifact(s, digest, files, sum(f.size_bytes for f in files))
            s.commit()
        return {"digest": digest, "files": len(files)}

    # ----------------------------------------------------------------------
    # listings
    # ----------------------------------------------------------------------

    @app.post("/v1/listings", status_code=201)
    def create_listing(body: CreateListing, d: D, principal: P) -> dict:
        me = require(principal)
        listing_id = f"lst_{uuid.uuid4().hex[:16]}"
        with d.store.session() as s:
            # Refuse to create a listing for weights we do not hold. Otherwise
            # the row reaches the certification queue with nothing to certify,
            # and the seller pays a fee for a job that cannot run.
            if d.store.get_artifact(s, body.artifact_digest) is None:
                raise HTTPException(
                    409, "no finalized artifact for that digest; upload it first"
                )
            d.store.upsert_user(s, me.user_id, me.email)
            row = d.store.create_listing(
                s, listing_id, me.user_id, body.artifact_digest, body.title
            )
            row.price_minor = body.price_minor
            row.currency = body.currency
            s.commit()
        return {"listing_id": listing_id, "state": ListingState.DRAFT.value}

    @app.get("/v1/listings")
    def browse(d: D) -> dict:
        """Public catalogue. Unpublished submissions never cross this boundary."""
        with d.store.session() as s:
            rows = d.store.listings_in_state(s, ListingState.LISTED)
            return {
                "listings": [
                    {
                        "listing_id": r.id,
                        "title": r.title,
                        "artifact_digest": r.artifact_digest,
                        "state": r.state,
                        "price": str(r.price()),
                        "price_minor": r.price_minor,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in rows
                ]
            }

    @app.get("/v1/seller/listings")
    def seller_listings(d: D, principal: P) -> dict:
        """The authenticated seller's complete submission workspace."""
        me = require(principal)
        with d.store.session() as s:
            listings = []
            for row in d.store.listings_for_creator(s, me.user_id):
                report = d.store.latest_report(s, row.id)
                gate_summary = None
                grade = None
                if report is not None:
                    view = redact(report, Audience.CREATOR)
                    assert_no_leak(view, Audience.CREATOR)
                    gate_summary = summarize_safety_gates(view)
                    grade = view.rating.grade
                listings.append(
                    {
                        "listing_id": row.id,
                        "title": row.title,
                        "artifact_digest": row.artifact_digest,
                        "state": row.state,
                        "price": str(row.price()),
                        "price_minor": row.price_minor,
                        "selected_benchmarks": list(row.selected_benchmarks or []),
                        "attempts": len(row.attempts),
                        "grade": grade,
                        "safety_status": gate_summary["overall"] if gate_summary else "pending",
                        "verified": row.state in {
                            ListingState.CERTIFIED.value,
                            ListingState.LISTED.value,
                        },
                        "can_publish": row.state == ListingState.CERTIFIED.value,
                        "created_at": row.created_at.isoformat(),
                        "updated_at": row.updated_at.isoformat(),
                    }
                )
            return {"listings": listings}

    @app.patch("/v1/listings/{listing_id}")
    def update_listing(
        listing_id: str, body: UpdateListing, d: D, principal: P
    ) -> dict:
        """Change price or title. Seller only, and allowed after listing.

        Repricing does not disturb existing orders: an order records the amount
        it was created at, so a buyer mid-checkout pays what they were quoted
        and a completed sale is not retroactively rewritten.
        """
        me = require(principal)
        with d.store.session() as s:
            row = _row_or_404(d, s, listing_id)
            if row.creator_id != me.user_id:
                raise HTTPException(403, "not your listing")
            if row.state == ListingState.WITHDRAWN.value:
                raise HTTPException(409, "this listing has been withdrawn")

            if body.price_minor is not None:
                row.price_minor = body.price_minor
            if body.currency is not None:
                row.currency = body.currency
            if body.title is not None:
                row.title = body.title
            s.commit()
            return {
                "listing_id": row.id,
                "title": row.title,
                "price": str(row.price()),
                "price_minor": row.price_minor,
                "state": row.state,
            }

    @app.get("/v1/listings/{listing_id}")
    def get_listing(listing_id: str, d: D, principal: P) -> dict:
        with d.store.session() as s:
            row = _row_or_404(d, s, listing_id)
            audience = audience_for(principal, row.creator_id)
            if audience is Audience.BUYER and row.state != ListingState.LISTED.value:
                # Hiding the card in the frontend is not a security boundary.
                # Failed, queued, and merely certified submissions are private
                # to their seller (and platform operators) until publication.
                raise HTTPException(404, "no such listing")
            report = d.store.latest_report(s, listing_id)
            payload = {
                "listing_id": row.id,
                "title": row.title,
                "state": row.state,
                "artifact_digest": row.artifact_digest,
                "price": str(row.price()),
                "price_minor": row.price_minor,
                "attempts": len(row.attempts),
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
            if audience is not Audience.BUYER:
                payload["flagged_for_review"] = row.flagged_for_review
                progress = d.store.get_evaluation_progress(s, listing_id)
                if progress is not None:
                    payload["evaluation_progress"] = {
                        "percent": progress.percent,
                        "stage": progress.stage,
                        "gates": progress.gates,
                        "updated_at": progress.updated_at.isoformat(),
                    }
            if report is not None:
                payload |= _view(d, report, principal, row.creator_id)
            return payload

    @app.post("/v1/seller/listings/{listing_id}/activate")
    def activate_listing(listing_id: str, d: D, principal: P) -> dict:
        """Publish a verified model from the seller workspace."""
        me = require(principal)
        with d.store.session() as s:
            row = _row_or_404(d, s, listing_id)
            if row.creator_id != me.user_id:
                raise HTTPException(403, "not your listing")
            try:
                row.state = transition(
                    ListingState(row.state), ListingState.LISTED
                ).value
            except TransitionError as exc:
                raise HTTPException(409, "only a verified model can be published") from exc
            s.commit()
            return {"listing_id": row.id, "state": row.state, "published": True}

    # ----------------------------------------------------------------------
    # publish: quote -> pay -> queue
    # ----------------------------------------------------------------------

    @app.get("/v1/benchmarks")
    def benchmarks(d: D) -> dict:
        """The menu a creator picks from.

        Mandatory items are listed too, so the price shown is the price paid.
        """
        from keystone.registry import SUITES_ROOT, discover

        items = menu(discover(SUITES_ROOT))
        return {
            "benchmarks": [i.as_dict() for i in items],
            "mandatory_total": str(
                quote(discover(SUITES_ROOT), [])
            ),
        }

    @app.post("/v1/listings/{listing_id}/publish")
    def publish(listing_id: str, body: PublishRequest, d: D, principal: P) -> dict:
        """Ask to publish. Returns a charge to settle, or the reason we refused.

        The price is the sum of what will actually run: mandatory suites plus
        whatever the creator selected. Rate limits are evaluated before a charge
        is minted, so we never take money from someone we are about to reject on
        cooldown.
        """
        from keystone.registry import SUITES_ROOT, discover

        me = require(principal)
        now = datetime.now(timezone.utc)
        suites = discover(SUITES_ROOT)

        try:
            running = normalise_selection(suites, body.benchmarks)
        except SelectionError as exc:
            raise HTTPException(400, str(exc)) from exc

        price = quote(suites, body.benchmarks)
        declined = declined_ids(suites, body.benchmarks)

        with d.store.session() as s:
            row = _row_or_404(d, s, listing_id)
            if row.creator_id != me.user_id:
                raise HTTPException(403, "not your listing")

            listing = d.store.load_listing(s, listing_id)
            ok, reason = listing.can_attempt(now, d.policy)  # no charge yet
            if not ok and "payment required" not in reason:
                raise HTTPException(409, reason)

            row.selected_benchmarks = running
            charge = d.payments.create_charge(
                price, listing_id, metadata={"creator_id": me.user_id}
            )
            d.store.put_charge(s, charge)
            s.commit()

        return {
            "charge_id": charge.charge_id,
            "amount": str(charge.amount),
            "running": running,
            "declined": declined,
            "chain": charge.chain,
            "address": charge.address,
            "checkout_url": charge.checkout_url,
            "expires_at": charge.expires_at.isoformat() if charge.expires_at else None,
        }

    @app.post("/v1/listings/{listing_id}/confirm")
    def confirm(listing_id: str, body: ConfirmPayment, d: D, principal: P) -> dict:
        """Queue certification once the charge has actually settled.

        Settlement is re-read from the provider. A client saying it paid is not
        evidence that it paid.
        """
        me = require(principal)
        now = datetime.now(timezone.utc)

        with d.store.session() as s:
            row = _row_or_404(d, s, listing_id)
            if row.creator_id != me.user_id:
                raise HTTPException(403, "not your listing")

            charge = d.payments.get_charge(body.charge_id)
            d.store.put_charge(s, charge)

            listing = d.store.load_listing(s, listing_id)
            # The fee is whatever the selection came to, so the policy is asked
            # about the quoted amount rather than a flat rate.
            policy = replace(d.policy, fee=charge.amount)
            ok, reason = listing.can_attempt(now, policy, charge=charge)
            if not ok:
                s.commit()  # keep the mirrored charge state
                raise HTTPException(402 if "payment" in reason else 409, reason)

            row.state = transition(
                ListingState(row.state), ListingState.PENDING_CERTIFICATION
            ).value
            # A rejected listing may be submitted again. Never expose the
            # previous attempt's 100% progress while this one is queued.
            from keystone.public_safety import initial_progress_gates

            d.store.set_evaluation_progress(
                s,
                listing_id,
                percent=0,
                stage="Waiting for worker",
                gates=initial_progress_gates(),
            )
            s.commit()
            state = row.state

        return {"listing_id": listing_id, "state": state, "queued": True}

    # ----------------------------------------------------------------------
    # reports and downloads
    # ----------------------------------------------------------------------

    @app.get("/v1/reports/{report_id}")
    def get_report(report_id: str, d: D, principal: P) -> dict:
        with d.store.session() as s:
            report = d.store.get_report(s, report_id)
            if report is None:
                raise HTTPException(404, "no such report")
            # A report is creator-visible to whoever listed that artifact.
            listing_row = (
                s.query(ListingRow)
                .filter(ListingRow.artifact_digest == report.subject.artifact_digest)
                .first()
            )
            creator_id = listing_row.creator_id if listing_row else None
            return _view(d, report, principal, creator_id)

    @app.post("/v1/listings/{listing_id}/purchase", status_code=201)
    def purchase(listing_id: str, d: D, principal: P) -> dict:
        """Start a purchase. Returns a charge to settle."""
        me = require(principal)
        with d.store.session() as s:
            row = _row_or_404(d, s, listing_id)
            if row.state != ListingState.LISTED.value:
                raise HTTPException(409, "this model is not published")
            if row.creator_id == me.user_id:
                raise HTTPException(409, "you already own this listing")
            if row.price_minor == 0:
                raise HTTPException(409, "this model is free -- download directly")
            existing = d.store.entitling_order(s, me.user_id, listing_id)
            if existing is not None and existing.status is OrderStatus.PAID:
                raise HTTPException(409, "already purchased")

            d.store.upsert_user(s, me.user_id, me.email)
            order = Order(
                order_id=f"ord_{uuid.uuid4().hex[:16]}",
                buyer_id=me.user_id,
                listing_id=listing_id,
                artifact_digest=row.artifact_digest,
                amount=row.price(),
                created_at=datetime.now(timezone.utc),
            )
            # The charge references the ORDER, not the listing, so a charge can
            # only ever settle the purchase it was minted for.
            charge = d.payments.create_charge(
                order.amount, order.order_id, metadata={"listing_id": listing_id}
            )
            order.charge_id = charge.charge_id
            d.store.create_order(s, order)
            d.store.put_charge(s, charge)
            s.commit()

        return {
            "order_id": order.order_id,
            "amount": str(order.amount),
            "charge_id": charge.charge_id,
            "chain": charge.chain,
            "address": charge.address,
            "checkout_url": charge.checkout_url,
        }

    @app.post("/v1/orders/{order_id}/confirm")
    def confirm_order(order_id: str, d: D, principal: P) -> dict:
        """Settle an order against provider state, then credit the creator."""
        me = require(principal)
        now = datetime.now(timezone.utc)

        with d.store.session() as s:
            order = d.store.get_order(s, order_id)
            if order is None:
                raise HTTPException(404, "no such order")
            if order.buyer_id != me.user_id:
                raise HTTPException(403, "not your order")

            already_paid = order.status is OrderStatus.PAID
            charge = d.payments.get_charge(order.charge_id)
            d.store.put_charge(s, charge)
            settle(order, charge, now)
            d.store.save_order(s, order)

            if order.status is OrderStatus.PAID and not already_paid:
                # One payout row per order; the unique constraint on order_id is
                # what stops a replayed confirmation from paying twice.
                listing = d.store.get_listing(s, order.listing_id)
                seller = s.get(UserRow, listing.creator_id)
                d.store.record_payout(
                    s,
                    payout_id=f"pay_{uuid.uuid4().hex[:16]}",
                    creator_id=listing.creator_id,
                    order_id=order.order_id,
                    amount=d.split.creator_cut(order.amount),
                    destination=seller.payout_address if seller else None,
                )
            s.commit()
            status = order.status.value

        if status != OrderStatus.PAID.value:
            raise HTTPException(402, f"payment not settled ({status})")
        return {"order_id": order_id, "status": status, "entitled": True}

    @app.get("/v1/orders")
    def my_orders(d: D, principal: P) -> dict:
        me = require(principal)
        with d.store.session() as s:
            return {
                "orders": [
                    {
                        "order_id": o.order_id,
                        "listing_id": o.listing_id,
                        "amount": str(o.amount),
                        "status": o.status.value,
                        "created_at": o.created_at.isoformat(),
                    }
                    for o in d.store.orders_for_buyer(s, me.user_id)
                ]
            }

    @app.get("/v1/listings/{listing_id}/download")
    def download(listing_id: str, d: D, principal: P) -> dict:
        """Presigned URLs, minted only against entitlement.

        The check happens before the URL exists rather than after: a presigned
        link expires, but a leaked one is still a copy of the weights.
        """
        me = require(principal)
        with d.store.session() as s:
            row = _row_or_404(d, s, listing_id)
            order = d.store.entitling_order(s, me.user_id, listing_id)
            verdict = check_entitlement(
                buyer_id=me.user_id,
                listing_id=listing_id,
                listing_state=row.state,
                listing_creator_id=row.creator_id,
                price=row.price(),
                order=order,
            )
            if not verdict.allowed:
                raise HTTPException(
                    402 if verdict.payment_required else 403, verdict.reason
                )
            art = s.get(ArtifactRow, row.artifact_digest)
            if art is None:
                raise HTTPException(404, "artifact missing")
            return {
                "digest": row.artifact_digest,
                "files": [
                    {
                        "path": f.path,
                        "size_bytes": f.size_bytes,
                        "sha256": f.sha256,  # verify after download; do not trust us
                        "url": d.artifacts.presign_get(artifact_key(row.artifact_digest, f.path)),
                    }
                    for f in art.files()
                ],
            }

    # ----------------------------------------------------------------------
    # admin
    # ----------------------------------------------------------------------

    @app.get("/v1/admin/flagged")
    def flagged(d: D, principal: P) -> dict:
        me = require(principal)
        if not me.is_admin:
            raise HTTPException(403, "admin only")
        with d.store.session() as s:
            return {
                "listings": [
                    {
                        "listing_id": r.id,
                        "creator_id": r.creator_id,
                        "state": r.state,
                        "attempts": len(r.attempts),
                    }
                    for r in d.store.flagged_listings(s)
                ]
            }

    @app.get("/v1/signing-key")
    def signing_key(d: D) -> dict:
        """Public verification key. Meant to be public -- that is the point.

        A buyer verifies a signed report against this without trusting us at
        the moment of verification.
        """
        import base64

        if d.signer is None:
            raise HTTPException(503, "no signing key configured")
        return {
            "algorithm": "ed25519",
            "key_id": d.signer.key_id,
            "public_key": base64.b64encode(d.signer.public_key_bytes()).decode(),
        }

    # Development only. In production the presigned URL points at R2 and the
    # bytes never touch this process; this exists so the local demo can follow
    # the same declare -> PUT -> finalize path instead of a special case.
    if isinstance(deps.artifacts, LocalStore) and deps.artifacts.base_url:

        @app.put("/v1/dev-upload/{key:path}", include_in_schema=False)
        async def dev_upload(key: str, request: Request, principal: P) -> dict:
            require(principal)
            if ".." in key or key.startswith("/"):
                raise HTTPException(400, "bad key")
            target = deps.artifacts.root / key
            target.parent.mkdir(parents=True, exist_ok=True)
            body = await request.body()
            target.write_bytes(body)
            return {"key": key, "bytes": len(body)}

    @app.get("/v1/charges/{charge_id}")
    def get_charge(charge_id: str, d: D, principal: P) -> dict:
        """Current chain state for a charge. Read from the provider, every time."""
        require(principal)
        try:
            charge = d.payments.get_charge(charge_id)
        except KeyError:
            raise HTTPException(404, "no such charge") from None

        with d.store.session() as s:
            d.store.put_charge(s, charge)
            s.commit()

        received = None
        if isinstance(d.payments, DemoChainProvider):
            got = d.payments.received(charge_id)
            received = str(got) if got else None

        return {
            "charge_id": charge.charge_id,
            "reference": charge.reference,
            "status": charge.status.value,
            "amount": str(charge.amount),
            "received": received,
            "chain": charge.chain,
            "address": charge.address,
            "tx_hash": charge.tx_hash,
            "confirmations": charge.confirmations,
            "required_confirmations": charge.required_confirmations,
            "settled": charge.is_settled,
        }

    # Development only: stands in for a wallet broadcasting the payment. In
    # production the funds arrive on chain and a watcher sees them.
    if isinstance(deps.payments, DemoChainProvider):

        @app.post("/v1/charges/{charge_id}/demo-pay", include_in_schema=False)
        def demo_pay(charge_id: str, body: DemoPay, d: D, principal: P) -> dict:
            require(principal)
            try:
                amount = None
                if body.amount_minor is not None:
                    original = d.payments.charges[charge_id].amount
                    amount = Money(body.amount_minor, original.currency)
                charge = d.payments.broadcast(charge_id, amount)
            except KeyError:
                raise HTTPException(404, "no such charge") from None
            return {"charge_id": charge.charge_id, "tx_hash": charge.tx_hash}

    @app.post("/v1/webhooks/payments", include_in_schema=False)
    async def payment_webhook(request: Request, d: D) -> dict:
        """Processor callback. A prompt to re-read, never a source of truth.

        Anyone can POST here, so the signature check comes first -- over the
        raw bytes, because re-serialising JSON can reorder keys and invalidate
        a good signature. Even once it passes, the payload's claims about
        status and amount are discarded: the only thing taken from it is which
        charge to go and look up.
        """
        from keystone.providers.hosted_checkout import (
            HostedCheckoutProvider,
            verify_webhook,
        )

        if not isinstance(d.payments, HostedCheckoutProvider):
            raise HTTPException(404, "no webhook for this payment provider")

        raw = await request.body()
        signature = request.headers.get("x-webhook-signature", "")
        if not verify_webhook(d.payments.config.webhook_secret, raw, signature):
            raise HTTPException(401, "bad signature")

        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(400, "malformed payload") from None

        charge_id = d.payments.charge_id_from_webhook(payload)
        if not charge_id:
            raise HTTPException(400, "payload names no charge")

        # Re-read from the processor. This is the whole point of the endpoint.
        charge = d.payments.get_charge(charge_id)
        with d.store.session() as s:
            d.store.put_charge(s, charge)
            s.commit()
        return {"charge_id": charge.charge_id, "status": charge.status.value}

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True}

    return app


# --------------------------------------------------------------------------
# request bodies
# --------------------------------------------------------------------------

class FileDecl(BaseModel):
    path: str
    size_bytes: int
    sha256: str = Field(pattern="^[0-9a-f]{64}$")


class DeclareArtifact(BaseModel):
    digest: str = Field(pattern="^[0-9a-f]{64}$")
    files: list[FileDecl]


class CreateListing(BaseModel):
    artifact_digest: str = Field(pattern="^[0-9a-f]{64}$")
    title: str | None = None
    # Minor units. Zero is a real price -- a free model still gets certified.
    price_minor: int = Field(default=0, ge=0)
    currency: str = "USDC"


class UpdateListing(BaseModel):
    """Every field optional: a reprice should not require restating the title."""

    price_minor: int | None = Field(default=None, ge=0)
    currency: str | None = None
    title: str | None = None


class ConfirmPayment(BaseModel):
    charge_id: str


class DemoPay(BaseModel):
    # Send the wrong amount to watch the underpayment path.
    amount_minor: int | None = None


class PublishRequest(BaseModel):
    # Optional capability benchmarks. Mandatory suites are added server-side,
    # so omitting them here does not skip them.
    benchmarks: list[str] = []
