from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, Float, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# JSONB on PostgreSQL, generic JSON elsewhere (MySQL JSON, SQLite TEXT)
JSONType = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Bill(Base):
    __tablename__ = "bills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # File metadata
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(255), unique=True)
    file_type: Mapped[str] = mapped_column(String(10))

    # Extracted fields (all nullable: never invent values)
    consumer_name: Mapped[str | None] = mapped_column(String(255))
    account_number: Mapped[str | None] = mapped_column(String(64), index=True)
    billing_period: Mapped[str | None] = mapped_column(String(100))
    billing_period_start: Mapped[date | None] = mapped_column(Date)
    billing_period_end: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    previous_reading: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    current_reading: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    units_consumed: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    net_amount_due: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    # Audit / debugging
    raw_ocr_text: Mapped[str] = mapped_column(Text, default="")
    extraction_method: Mapped[str | None] = mapped_column(String(20))  # text | ocr | mixed
    page_count: Mapped[int | None] = mapped_column(Integer)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer)
    parser_name: Mapped[str | None] = mapped_column(String(50))

    validation_status: Mapped[str] = mapped_column(String(10), index=True)
    validation_details: Mapped[dict] = mapped_column(JSONType, default=dict)
    field_sources: Mapped[dict] = mapped_column(JSONType, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # avoid leaking PII into logs
        return f"<Bill id={self.id} status={self.validation_status}>"
