"""Serialize editable archives with existing PDF/production workers.

No existing rows or JSON snapshots change. Apply before promoting API code;
stage builds and back up first. Drain export workers before downgrade.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_editable_exports"
down_revision = "0008_editor_sessions"
branch_labels = None
depends_on = None


def _index(kinds):
    op.drop_index("uq_running_export_tenant", table_name="jobs")
    condition = sa.text("status = 'running' AND kind IN (" + ",".join("'" + kind + "'" for kind in kinds) + ")")
    op.create_index("uq_running_export_tenant", "jobs", ["tenant_id"], unique=True,
                    postgresql_where=condition, sqlite_where=condition)


def upgrade():
    _index(("review_export", "production_export", "editable_export"))


def downgrade():
    _index(("review_export", "production_export"))
