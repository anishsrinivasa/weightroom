"""Persistence.

Domain logic stays in `listing.py` and `payments.py` as plain dataclasses with
no database in them; this module only maps. That separation is deliberate --
the attempt policy and the state machine are the parts worth testing hard, and
they should not need a database to run.

SQLite for development and tests, Postgres in production. Same code, different
URL.

Reports are stored as JSON because `schema.py` is the contract; a handful of
columns are lifted out for indexing and nothing else. Never query the JSON to
make a decision the columns should be answering.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.pool import StaticPool

from keystone.listing import Attempt, Listing, ListingState
from keystone.orders import Order, OrderStatus
from keystone.payments import Charge, ChargeStatus, Currency, Money
from keystone.schema import CertificationReport, FileEntry


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------

class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    # Where creator payouts go. Null until they set one.
    payout_address: Mapped[str | None] = mapped_column(String(128), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ArtifactRow(Base):
    """Content-addressed, so the digest is the primary key.

    Two creators uploading identical weights share one row -- which is also why
    ownership lives on the listing, not here.
    """

    __tablename__ = "artifacts"

    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    total_bytes: Mapped[int] = mapped_column(Integer)
    file_manifest: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def files(self) -> list[FileEntry]:
        return [FileEntry.model_validate(f) for f in self.file_manifest]


class ListingRow(Base):
    __tablename__ = "listings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    creator_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    artifact_digest: Mapped[str] = mapped_column(
        String(64), ForeignKey("artifacts.digest"), index=True
    )
    state: Mapped[str] = mapped_column(String(32), index=True)
    flagged_for_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    title: Mapped[str | None] = mapped_column(String(200), default=None)
    # Seller-authored blurb shown on the model page. Free text, never parsed.
    description: Mapped[str | None] = mapped_column(Text, default=None)
    # sha256 of a cover image in the artifact store. Nullable: the UI falls
    # back to a house default rather than making an image mandatory.
    image_digest: Mapped[str | None] = mapped_column(String(64), default=None)
    # Zero is a real price, not a missing one: plenty of good open models
    # should cost nothing and still carry a certificate.
    price_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USDC")
    # Benchmarks the creator chose to run. Mandatory suites are folded in at
    # certification time regardless of what is stored here.
    selected_benchmarks: Mapped[list] = mapped_column(JSON, default=list)
    # Curated facets used by the public catalogue. Nullable columns let the
    # additive SQLite development migration upgrade existing databases safely.
    domain_tags: Mapped[list | None] = mapped_column(JSON, default=list, nullable=True)
    size_tag: Mapped[str | None] = mapped_column(String(32), default=None, nullable=True)
    # Redistribution terms the seller attached. Distinct from the upstream
    # licence on the model card, which `provenance` reads and checks; this is a
    # term of sale between two parties here. Nullable so existing rows upgrade,
    # and `licensing.parse` resolves a missing value to the stricter default --
    # a blank must never widen a buyer's rights.
    license_kind: Mapped[str | None] = mapped_column(String(32), default=None, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    def price(self) -> Money:
        return Money(self.price_minor, Currency(self.currency))

    attempts: Mapped[list["AttemptRow"]] = relationship(
        back_populates="listing", order_by="AttemptRow.created_at", cascade="all, delete-orphan"
    )


class AttemptRow(Base):
    __tablename__ = "attempts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    listing_id: Mapped[str] = mapped_column(String(64), ForeignKey("listings.id"), index=True)
    artifact_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    grade: Mapped[str | None] = mapped_column(String(16), default=None)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    charge_id: Mapped[str | None] = mapped_column(String(64), default=None)
    # What ran on this attempt, so a later dispute can be settled from the row.
    selected_benchmarks: Mapped[list] = mapped_column(JSON, default=list)
    # INTERNAL only. This is precisely the signal a prober wants, so it must
    # never reach a serialiser that faces a creator.
    internal_score: Mapped[float | None] = mapped_column(Float, default=None)

    listing: Mapped[ListingRow] = relationship(back_populates="attempts")


class EvaluationProgressRow(Base):
    """Live seller-visible progress, separate from immutable signed reports."""

    __tablename__ = "evaluation_progress"

    listing_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("listings.id"), primary_key=True
    )
    percent: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(160), default="Queued")
    gates: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class ReportRow(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    listing_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("listings.id"), index=True, default=None
    )
    attempt_id: Mapped[str | None] = mapped_column(String(64), default=None)
    artifact_digest: Mapped[str] = mapped_column(String(64), index=True)
    grade: Mapped[str] = mapped_column(String(16), index=True)
    sandboxed: Mapped[bool] = mapped_column(Boolean, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    payload: Mapped[str] = mapped_column(Text)  # the full report; schema.py is the contract

    def report(self) -> CertificationReport:
        return CertificationReport.model_validate_json(self.payload)


class OrderRow(Base):
    """One buyer's entitlement to one listing."""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    buyer_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    listing_id: Mapped[str] = mapped_column(String(64), ForeignKey("listings.id"), index=True)
    artifact_digest: Mapped[str] = mapped_column(String(64), index=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), index=True)
    charge_id: Mapped[str | None] = mapped_column(String(64), default=None)
    # Snapshotted from the listing at purchase, not read back through it. A
    # seller can change the terms on a future sale; what this buyer agreed to
    # is fixed at the moment they agreed, and re-reading the listing would
    # rewrite history every time the seller edited it.
    license_kind: Mapped[str | None] = mapped_column(String(32), default=None, nullable=True)
    license_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=None, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class VoteRow(Base):
    """One account's up or down vote on one listing.

    A row per (voter, listing) with a unique constraint rather than a pair of
    counters on `listings`: counters cannot tell whether a person has already
    voted, so they cannot be changed or withdrawn, and anyone able to call the
    endpoint twice can run the number up on their own.

    Votes require an account for the same reason. An anonymous counter measures
    how many times a button was pressed, which is not the thing a buyer reads
    it as.
    """

    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("voter_id", "listing_id", name="uq_vote_once"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    voter_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    listing_id: Mapped[str] = mapped_column(String(64), ForeignKey("listings.id"), index=True)
    #: +1 or -1. Stored as the value rather than a boolean so the sum is the
    #: score and a third state can be added without rewriting every row.
    value: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class PayoutRow(Base):
    """What a creator is owed, and whether it has been sent."""

    __tablename__ = "payouts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    creator_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    order_id: Mapped[str] = mapped_column(String(64), ForeignKey("orders.id"), unique=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), index=True)
    destination: Mapped[str | None] = mapped_column(String(128), default=None)
    tx_hash: Mapped[str | None] = mapped_column(String(80), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ChargeRow(Base):
    """Mirror of provider state. The provider stays authoritative.

    Never settle a charge from a client callback -- re-read it from the provider
    and write the answer here.
    """

    __tablename__ = "charges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reference: Mapped[str] = mapped_column(String(64), index=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), index=True)
    tx_hash: Mapped[str | None] = mapped_column(String(80), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


# --------------------------------------------------------------------------
# mapping
# --------------------------------------------------------------------------

def to_domain_listing(row: ListingRow) -> Listing:
    return Listing(
        listing_id=row.id,
        creator_id=row.creator_id,
        artifact_digest=row.artifact_digest,
        state=ListingState(row.state),
        attempts=[
            Attempt(
                attempt_id=a.id,
                artifact_digest=a.artifact_digest,
                created_at=a.created_at,
                grade=a.grade,
                passed=a.passed,
                charge_id=a.charge_id,
                internal_score=a.internal_score,
            )
            for a in row.attempts
        ],
        flagged_for_review=row.flagged_for_review,
    )


def to_domain_order(row: OrderRow) -> Order:
    return Order(
        order_id=row.id,
        buyer_id=row.buyer_id,
        listing_id=row.listing_id,
        artifact_digest=row.artifact_digest,
        amount=Money(row.amount_minor, Currency(row.currency)),
        created_at=row.created_at,
        charge_id=row.charge_id,
        status=OrderStatus(row.status),
        paid_at=row.paid_at,
    )


def to_domain_charge(row: ChargeRow) -> Charge:
    return Charge(
        charge_id=row.id,
        reference=row.reference,
        amount=Money(row.amount_minor, Currency(row.currency)),
        status=ChargeStatus(row.status),
        tx_hash=row.tx_hash,
        created_at=row.created_at,
        settled_at=row.settled_at,
    )


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------

class Store:
    """Thin repository. Callers own transactions via `session()`."""

    def __init__(self, url: str = "sqlite:///keystone.db", *, echo: bool = False) -> None:
        kwargs: dict = {}
        if url.startswith("sqlite"):
            # SQLite opens a *separate* database per connection when in-memory,
            # so a threaded server would see an empty schema. Pin one shared
            # connection and let other threads use it.
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url or url == "sqlite://":
                kwargs["poolclass"] = StaticPool
        else:
            # Managed Postgres proxies close idle client connections. Validate
            # a pooled connection before handing it to a request so the first
            # Seller Studio visit after an idle period reconnects instead of
            # surfacing psycopg's client_idle_timeout as a 500.
            kwargs["pool_pre_ping"] = True
        self.engine = create_engine(url, echo=echo, future=True, **kwargs)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """Add columns that `create_all` cannot, because the table exists.

        A stopgap, not a migration tool: it only ever *adds* nullable columns,
        which is the one schema change that is safe to apply blind. Anything
        else -- a rename, a type change, a backfill -- needs Alembic, and this
        deliberately will not pretend otherwise.
        """
        from sqlalchemy import inspect, text

        inspector = inspect(self.engine)
        existing = set(inspector.get_table_names())
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if table.name not in existing:
                    continue
                have = {c["name"] for c in inspector.get_columns(table.name)}
                for column in table.columns:
                    if column.name in have or not column.nullable:
                        continue
                    kind = column.type.compile(self.engine.dialect)
                    conn.execute(
                        text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {kind}")
                    )

    def session(self) -> Session:
        return Session(self.engine, future=True)

    # -- users -----------------------------------------------------------

    def upsert_user(self, s: Session, user_id: str, email: str) -> UserRow:
        row = s.get(UserRow, user_id)
        if row is None:
            row = UserRow(id=user_id, email=email)
            s.add(row)
            # Callers commonly create a listing or order for a first-time user
            # in this same transaction.  Without an ORM relationship between
            # those rows SQLAlchemy may emit the dependent INSERT first;
            # Postgres then correctly rejects it on the user foreign key.
            # Persist just the new principal before any dependent row is staged.
            s.flush([row])
        return row

    # -- artifacts -------------------------------------------------------

    def put_artifact(
        self, s: Session, digest: str, files: Iterable[FileEntry], total_bytes: int
    ) -> ArtifactRow:
        """Idempotent by construction: the digest *is* the content."""
        row = s.get(ArtifactRow, digest)
        if row is None:
            row = ArtifactRow(
                digest=digest,
                total_bytes=total_bytes,
                file_manifest=[f.model_dump(mode="json") for f in files],
            )
            s.add(row)
        return row

    def get_artifact(self, s: Session, digest: str) -> ArtifactRow | None:
        return s.get(ArtifactRow, digest)

    # -- listings --------------------------------------------------------

    def create_listing(
        self, s: Session, listing_id: str, creator_id: str, artifact_digest: str,
        title: str | None = None,
    ) -> ListingRow:
        row = ListingRow(
            id=listing_id,
            creator_id=creator_id,
            artifact_digest=artifact_digest,
            state=ListingState.DRAFT.value,
            title=title,
        )
        s.add(row)
        return row

    def get_listing(self, s: Session, listing_id: str) -> ListingRow | None:
        return s.get(ListingRow, listing_id)

    def load_listing(self, s: Session, listing_id: str) -> Listing | None:
        row = self.get_listing(s, listing_id)
        return to_domain_listing(row) if row else None

    def save_listing(self, s: Session, listing: Listing) -> ListingRow:
        """Write back state, flags, and any attempt not yet persisted."""
        row = s.get(ListingRow, listing.listing_id)
        if row is None:
            raise KeyError(listing.listing_id)
        row.state = listing.state.value
        row.flagged_for_review = listing.flagged_for_review
        row.artifact_digest = listing.artifact_digest

        known = {a.id for a in row.attempts}
        for attempt in listing.attempts:
            if attempt.attempt_id in known:
                continue
            row.attempts.append(
                AttemptRow(
                    id=attempt.attempt_id,
                    listing_id=row.id,
                    artifact_digest=attempt.artifact_digest,
                    created_at=attempt.created_at,
                    grade=attempt.grade,
                    passed=attempt.passed,
                    charge_id=attempt.charge_id,
                    internal_score=attempt.internal_score,
                )
            )
        return row

    def claim_for_certification(self, s: Session, listing_id: str) -> bool:
        """Move one listing to CERTIFYING, but only if nobody else already has.

        A read-then-write leaves a window where two workers both see the row
        pending and both proceed, certifying the same listing twice and paying
        for the GPU twice. A conditional UPDATE closes it: the database decides
        the winner, and rowcount reports whether that was us.
        """
        result = s.execute(
            update(ListingRow)
            .where(
                ListingRow.id == listing_id,
                ListingRow.state == ListingState.PENDING_CERTIFICATION.value,
            )
            .values(state=ListingState.CERTIFYING.value)
        )
        s.commit()
        return result.rowcount == 1

    def listings_in_state(self, s: Session, state: ListingState) -> list[ListingRow]:
        return list(s.scalars(select(ListingRow).where(ListingRow.state == state.value)))

    def listings_for_creator(self, s: Session, creator_id: str) -> list[ListingRow]:
        """Every submission owned by one seller, newest first."""
        return list(
            s.scalars(
                select(ListingRow)
                .where(ListingRow.creator_id == creator_id)
                .order_by(ListingRow.updated_at.desc(), ListingRow.created_at.desc())
            )
        )

    def set_evaluation_progress(
        self,
        s: Session,
        listing_id: str,
        *,
        percent: int,
        stage: str,
        gates: list[dict],
    ) -> EvaluationProgressRow:
        row = s.get(EvaluationProgressRow, listing_id)
        if row is None:
            row = EvaluationProgressRow(listing_id=listing_id)
            s.add(row)
        row.percent = min(100, max(0, int(percent)))
        row.stage = stage[:160]
        row.gates = gates
        row.updated_at = _utcnow()
        return row

    def get_evaluation_progress(
        self, s: Session, listing_id: str
    ) -> EvaluationProgressRow | None:
        return s.get(EvaluationProgressRow, listing_id)

    def flagged_listings(self, s: Session) -> list[ListingRow]:
        return list(s.scalars(select(ListingRow).where(ListingRow.flagged_for_review.is_(True))))

    # -- reports ---------------------------------------------------------

    def put_report(
        self,
        s: Session,
        report: CertificationReport,
        *,
        listing_id: str | None = None,
        attempt_id: str | None = None,
    ) -> ReportRow:
        # Idempotent: a worker that retries after a partial failure must not
        # crash on a duplicate id.
        row = s.get(ReportRow, report.report_id)
        if row is None:
            row = ReportRow(id=report.report_id)
            s.add(row)
        row.listing_id = listing_id
        row.attempt_id = attempt_id
        row.artifact_digest = report.subject.artifact_digest
        row.grade = report.rating.grade
        row.sandboxed = report.environment.sandboxed
        row.created_at = report.created_at
        row.payload = report.model_dump_json()
        return row

    def get_report(self, s: Session, report_id: str) -> CertificationReport | None:
        row = s.get(ReportRow, report_id)
        return row.report() if row else None

    def latest_report(self, s: Session, listing_id: str) -> CertificationReport | None:
        row = s.scalars(
            select(ReportRow)
            .where(ReportRow.listing_id == listing_id)
            .order_by(ReportRow.created_at.desc())
            .limit(1)
        ).first()
        return row.report() if row else None

    # -- orders ----------------------------------------------------------

    def create_order(
        self, s: Session, order: Order, *, license_kind: str | None = None
    ) -> OrderRow:
        row = OrderRow(
            id=order.order_id,
            buyer_id=order.buyer_id,
            listing_id=order.listing_id,
            artifact_digest=order.artifact_digest,
            amount_minor=order.amount.amount_minor,
            currency=order.amount.currency.value,
            status=order.status.value,
            charge_id=order.charge_id,
            created_at=order.created_at,
            license_kind=license_kind,
            license_accepted_at=_utcnow() if license_kind else None,
        )
        s.add(row)
        return row


    # -- votes ------------------------------------------------------------

    def cast_vote(self, s: Session, *, voter_id: str, listing_id: str, value: int) -> None:
        """Record one account's vote, replacing any earlier one.

        Idempotent by (voter, listing): pressing the same button twice leaves
        the same single row, and switching sides moves it rather than adding a
        second. `value` of 0 withdraws.
        """
        row = s.scalars(
            select(VoteRow).where(
                VoteRow.voter_id == voter_id, VoteRow.listing_id == listing_id
            )
        ).first()
        if value == 0:
            if row is not None:
                s.delete(row)
            return
        if row is None:
            s.add(VoteRow(
                id=f"vot_{uuid.uuid4().hex[:16]}",
                voter_id=voter_id,
                listing_id=listing_id,
                value=value,
            ))
        else:
            row.value = value

    def vote_tally(
        self, s: Session, listing_ids: list[str], *, voter_id: str | None = None
    ) -> dict[str, dict]:
        """Up and down counts per listing, plus this viewer's own vote.

        Batched deliberately: the catalogue renders a tally on every card, and
        a query per card is how a listing page starts costing more than the
        evaluation did.
        """
        if not listing_ids:
            return {}
        tally = {
            listing_id: {"up": 0, "down": 0, "score": 0, "mine": 0}
            for listing_id in listing_ids
        }
        for row in s.scalars(
            select(VoteRow).where(VoteRow.listing_id.in_(listing_ids))
        ):
            entry = tally[row.listing_id]
            entry["up" if row.value > 0 else "down"] += 1
            entry["score"] += row.value
            if voter_id and row.voter_id == voter_id:
                entry["mine"] = row.value
        return tally

    def get_order(self, s: Session, order_id: str) -> Order | None:
        row = s.get(OrderRow, order_id)
        return to_domain_order(row) if row else None

    def save_order(self, s: Session, order: Order) -> OrderRow:
        row = s.get(OrderRow, order.order_id)
        if row is None:
            raise KeyError(order.order_id)
        row.status = order.status.value
        row.charge_id = order.charge_id
        row.paid_at = order.paid_at
        return row

    def entitling_order(self, s: Session, buyer_id: str, listing_id: str) -> Order | None:
        """The buyer's paid order for this listing, if any.

        Paid orders win over pending ones, so an abandoned checkout never masks
        a completed purchase.
        """
        rows = list(
            s.scalars(
                select(OrderRow)
                .where(OrderRow.buyer_id == buyer_id, OrderRow.listing_id == listing_id)
                .order_by(OrderRow.created_at.desc())
            )
        )
        paid = next((r for r in rows if r.status == OrderStatus.PAID.value), None)
        chosen = paid or (rows[0] if rows else None)
        return to_domain_order(chosen) if chosen else None

    def orders_for_buyer(self, s: Session, buyer_id: str) -> list[Order]:
        rows = s.scalars(
            select(OrderRow)
            .where(OrderRow.buyer_id == buyer_id)
            .order_by(OrderRow.created_at.desc())
        )
        return [to_domain_order(r) for r in rows]

    # -- payouts ---------------------------------------------------------

    def record_payout(
        self,
        s: Session,
        payout_id: str,
        creator_id: str,
        order_id: str,
        amount: Money,
        *,
        status: str = "pending",
        destination: str | None = None,
        tx_hash: str | None = None,
    ) -> PayoutRow:
        """One payout per order. The unique constraint is the double-pay guard."""
        row = s.get(PayoutRow, payout_id)
        if row is None:
            row = PayoutRow(id=payout_id, order_id=order_id)
            s.add(row)
        row.creator_id = creator_id
        row.amount_minor = amount.amount_minor
        row.currency = amount.currency.value
        row.status = status
        row.destination = destination
        row.tx_hash = tx_hash
        return row

    def payout_for_order(self, s: Session, order_id: str) -> PayoutRow | None:
        return s.scalars(select(PayoutRow).where(PayoutRow.order_id == order_id)).first()

    def unpaid_payouts(self, s: Session) -> list[PayoutRow]:
        return list(s.scalars(select(PayoutRow).where(PayoutRow.status == "pending")))

    # -- charges ---------------------------------------------------------

    def put_charge(self, s: Session, charge: Charge) -> ChargeRow:
        row = s.get(ChargeRow, charge.charge_id)
        if row is None:
            row = ChargeRow(id=charge.charge_id, reference=charge.reference,
                            amount_minor=charge.amount.amount_minor,
                            currency=charge.amount.currency.value,
                            status=charge.status.value)
            s.add(row)
        row.status = charge.status.value
        row.tx_hash = charge.tx_hash
        row.settled_at = charge.settled_at
        return row

    def get_charge(self, s: Session, charge_id: str) -> Charge | None:
        row = s.get(ChargeRow, charge_id)
        return to_domain_charge(row) if row else None


__all__ = [
    "Base", "Store", "UserRow", "ArtifactRow", "ListingRow", "AttemptRow",
    "EvaluationProgressRow",
    "ReportRow", "ChargeRow", "OrderRow", "PayoutRow",
    "to_domain_listing", "to_domain_charge", "to_domain_order",
]
