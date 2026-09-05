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

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from keystone.auth import Authenticator, Principal, StaticTokenAuth, audience_for
from keystone.db import ArtifactRow, ListingRow, Store
from keystone.listing import DEFAULT_POLICY, AttemptPolicy, ListingState, transition
from keystone.payments import MockPaymentProvider, PaymentProvider
from keystone.schema import Audience, CertificationReport, FileEntry
from keystone.signing import Ed25519Signer
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
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.payments = payments
        self.auth = auth
        self.policy = policy
        self.signer = signer


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
    assert_no_leak(view, audience)  # belt and braces
    return {"audience": audience.value, "report": view.model_dump(mode="json")}


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
            d.store.upsert_user(s, me.user_id, me.email)
            d.store.create_listing(s, listing_id, me.user_id, body.artifact_digest, body.title)
            s.commit()
        return {"listing_id": listing_id, "state": ListingState.DRAFT.value}

    @app.get("/v1/listings")
    def browse(d: D, state: str = Query(ListingState.LISTED.value)) -> dict:
        """Public catalogue. Only what is actually listed, by default."""
        with d.store.session() as s:
            rows = d.store.listings_in_state(s, ListingState(state))
            return {
                "listings": [
                    {
                        "listing_id": r.id,
                        "title": r.title,
                        "artifact_digest": r.artifact_digest,
                        "state": r.state,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in rows
                ]
            }

    @app.get("/v1/listings/{listing_id}")
    def get_listing(listing_id: str, d: D, principal: P) -> dict:
        with d.store.session() as s:
            row = _row_or_404(d, s, listing_id)
            report = d.store.latest_report(s, listing_id)
            payload = {
                "listing_id": row.id,
                "title": row.title,
                "state": row.state,
                "artifact_digest": row.artifact_digest,
                "attempts": len(row.attempts),
            }
            if audience_for(principal, row.creator_id) is not Audience.BUYER:
                payload["flagged_for_review"] = row.flagged_for_review
            if report is not None:
                payload |= _view(d, report, principal, row.creator_id)
            return payload

    # ----------------------------------------------------------------------
    # publish: quote -> pay -> queue
    # ----------------------------------------------------------------------

    @app.post("/v1/listings/{listing_id}/publish")
    def publish(listing_id: str, d: D, principal: P) -> dict:
        """Ask to publish. Returns a charge to settle, or the reason we refused.

        Rate limits are evaluated before a charge is minted, so we never take
        money from someone we are about to reject on cooldown.
        """
        me = require(principal)
        now = datetime.now(timezone.utc)

        with d.store.session() as s:
            row = _row_or_404(d, s, listing_id)
            if row.creator_id != me.user_id:
                raise HTTPException(403, "not your listing")

            listing = d.store.load_listing(s, listing_id)
            ok, reason = listing.can_attempt(now, d.policy)  # no charge yet
            if not ok and "payment required" not in reason:
                raise HTTPException(409, reason)

            charge = d.payments.create_charge(
                d.policy.fee, listing_id, metadata={"creator_id": me.user_id}
            )
            d.store.put_charge(s, charge)
            s.commit()

        return {
            "charge_id": charge.charge_id,
            "amount": str(charge.amount),
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
            ok, reason = listing.can_attempt(now, d.policy, charge=charge)
            if not ok:
                s.commit()  # keep the mirrored charge state
                raise HTTPException(402 if "payment" in reason else 409, reason)

            row.state = transition(
                ListingState(row.state), ListingState.PENDING_CERTIFICATION
            ).value
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

    @app.get("/v1/listings/{listing_id}/download")
    def download(listing_id: str, d: D, principal: P) -> dict:
        """Presigned URLs for a listed model. Entitlement is checked here."""
        require(principal)
        with d.store.session() as s:
            row = _row_or_404(d, s, listing_id)
            if row.state != ListingState.LISTED.value and row.creator_id != (
                principal.user_id if principal else None
            ):
                raise HTTPException(403, "listing is not published")
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

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True}

    # Placeholder UI. Served from the API so there is no second origin and no
    # CORS to configure while this is a demo.
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(static_dir / "index.html")

        app.mount("/static", StaticFiles(directory=static_dir), name="static")

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


class ConfirmPayment(BaseModel):
    charge_id: str


def dev_app() -> FastAPI:
    """Local development wiring: SQLite, filesystem store, mock payments."""
    store = Store("sqlite:///keystone.db")
    store.create_all()
    auth = StaticTokenAuth(
        {
            "dev-creator": Principal("u_creator", "creator@example.com"),
            "dev-admin": Principal("u_admin", "admin@example.com", is_admin=True),
        }
    )
    # A generated key means dev reports verify end to end. Production sets
    # KEYSTONE_SIGNING_KEY so the key survives a restart.
    signer = Ed25519Signer.from_env() or Ed25519Signer.generate()
    return create_app(
        Deps(store, LocalStore(Path(".keystone-store")), MockPaymentProvider(), auth,
             signer=signer)
    )
