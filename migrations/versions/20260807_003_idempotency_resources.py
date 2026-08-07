"""scope and expire idempotency records

Revision ID: 20260807_003
Revises: 20260723_002
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op


revision = "20260807_003"
down_revision = "20260723_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("idempotency_records")}
    if "resource_key" not in columns:
        op.add_column(
            "idempotency_records",
            sa.Column("resource_key", sa.String(length=64), nullable=True),
        )

    indexes = {index["name"] for index in inspector.get_indexes("idempotency_records")}
    if "ix_idempotency_records_resource_key" not in indexes:
        op.create_index(
            "ix_idempotency_records_resource_key",
            "idempotency_records",
            ["resource_key"],
            unique=False,
        )
    # Old cache rows cannot be associated with a user and may contain complete
    # profile responses. They are transient retry data, so purge them once.
    op.execute(sa.text("DELETE FROM idempotency_records"))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("idempotency_records")}
    if "ix_idempotency_records_resource_key" in indexes:
        op.drop_index("ix_idempotency_records_resource_key", table_name="idempotency_records")

    columns = {column["name"] for column in inspector.get_columns("idempotency_records")}
    if "resource_key" in columns:
        op.drop_column("idempotency_records", "resource_key")
