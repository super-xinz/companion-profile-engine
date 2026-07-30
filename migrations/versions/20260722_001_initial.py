"""Initial auditable profile store."""

from alembic import op

from profile_engine.db import Base
from profile_engine import models  # noqa: F401

revision = "20260722_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    legacy_tables = [
        models.User.__table__,
        models.ProfileVersion.__table__,
        models.ProfileEvidence.__table__,
        models.Memory.__table__,
        models.CurrentState.__table__,
        models.RuntimePreference.__table__,
        models.IdempotencyRecord.__table__,
        models.RulePack.__table__,
        models.AuditLog.__table__,
    ]
    Base.metadata.create_all(bind=op.get_bind(), tables=legacy_tables)


def downgrade():
    legacy_tables = [
        models.AuditLog.__table__,
        models.RulePack.__table__,
        models.IdempotencyRecord.__table__,
        models.RuntimePreference.__table__,
        models.CurrentState.__table__,
        models.Memory.__table__,
        models.ProfileEvidence.__table__,
        models.ProfileVersion.__table__,
        models.User.__table__,
    ]
    Base.metadata.drop_all(bind=op.get_bind(), tables=legacy_tables)
