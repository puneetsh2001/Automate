"""create bills table

Revision ID: 0001
Revises:
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# JSONB on PostgreSQL, JSON on MySQL/SQLite
JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "bills",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("stored_filename", sa.String(255), nullable=False),
        sa.Column("file_type", sa.String(10), nullable=False),
        sa.Column("consumer_name", sa.String(255), nullable=True),
        sa.Column("account_number", sa.String(64), nullable=True),
        sa.Column("billing_period", sa.String(100), nullable=True),
        sa.Column("billing_period_start", sa.Date(), nullable=True),
        sa.Column("billing_period_end", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("previous_reading", sa.Numeric(14, 3), nullable=True),
        sa.Column("current_reading", sa.Numeric(14, 3), nullable=True),
        sa.Column("units_consumed", sa.Numeric(14, 3), nullable=True),
        sa.Column("net_amount_due", sa.Numeric(12, 2), nullable=True),
        sa.Column("raw_ocr_text", sa.Text(), nullable=False),
        sa.Column("extraction_method", sa.String(20), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),
        sa.Column("parser_name", sa.String(50), nullable=True),
        sa.Column("validation_status", sa.String(10), nullable=False),
        sa.Column("validation_details", JSONType, nullable=False),
        sa.Column("field_sources", JSONType, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("stored_filename", name="uq_bills_stored_filename"),
    )
    op.create_index("ix_bills_account_number", "bills", ["account_number"])
    op.create_index("ix_bills_validation_status", "bills", ["validation_status"])


def downgrade() -> None:
    op.drop_index("ix_bills_validation_status", table_name="bills")
    op.drop_index("ix_bills_account_number", table_name="bills")
    op.drop_table("bills")
