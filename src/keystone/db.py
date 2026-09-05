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
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from keystone.listing import Attempt, Listing, ListingState
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

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
    # INTERNAL only. This is precisely the signal a prober wants, so it must
    # never reach a serialiser that faces a creator.
    internal_score: Mapped[float | None] = mapped_column(Float, default=None)

    listing: Mapped[ListingRow] = relationship(back_populates="attempts")


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
        self.engine = create_engine(url, echo=echo, future=True)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return Session(self.engine, future=True)

    # -- users -----------------------------------------------------------

    def upsert_user(self, s: Session, user_id: str, email: str) -> UserRow:
        row = s.get(UserRow, user_id)
        if row is None:
            row = UserRow(id=user_id, email=email)
            s.add(row)
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

    def listings_in_state(self, s: Session, state: ListingState) -> list[ListingRow]:
        return list(s.scalars(select(ListingRow).where(ListingRow.state == state.value)))

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
        row = ReportRow(
            id=report.report_id,
            listing_id=listing_id,
            attempt_id=attempt_id,
            artifact_digest=report.subject.artifact_digest,
            grade=report.rating.grade,
            sandboxed=report.environment.sandboxed,
            created_at=report.created_at,
            payload=report.model_dump_json(),
        )
        s.add(row)
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
    "ReportRow", "ChargeRow", "to_domain_listing", "to_domain_charge",
]
