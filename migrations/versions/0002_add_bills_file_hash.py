"""add bills.file_hash for duplicate upload detection

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29

Existing rows keep NULL (their files may no longer be on disk); only new
uploads are fingerprinted. NULLs don't conflict in a unique index.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bills", sa.Column("file_hash", sa.String(64), nullable=True))
    op.create_index("ix_bills_file_hash", "bills", ["file_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_bills_file_hash", table_name="bills")
    op.drop_column("bills", "file_hash")
