"""add multi-person conversations, manual overrides and rule collaboration

Revision ID: 20260723_002
Revises: 20260722_001
Create Date: 2026-07-23
"""

from alembic import op

from profile_engine import models
from profile_engine.db import Base


revision = "20260723_002"
down_revision = "20260722_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # checkfirst=True also makes this safe for local POC databases that were
    # initialized with SQLAlchemy create_all before Alembic was enabled.
    workspace_tables = [
        models.Conversation.__table__,
        models.ChatMessage.__table__,
        models.ManualOverride.__table__,
        models.TeamMember.__table__,
        models.RuleRevision.__table__,
    ]
    Base.metadata.create_all(bind=op.get_bind(), tables=workspace_tables, checkfirst=True)


def downgrade() -> None:
    workspace_tables = [
        models.RuleRevision.__table__,
        models.TeamMember.__table__,
        models.ManualOverride.__table__,
        models.ChatMessage.__table__,
        models.Conversation.__table__,
    ]
    Base.metadata.drop_all(bind=op.get_bind(), tables=workspace_tables, checkfirst=True)
