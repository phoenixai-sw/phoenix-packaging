"""Add nullable server-owned registered geometry snapshots. No legacy backfill.

Apply after backup and before promoting the new API. Existing rows remain NULL.
Do not downgrade while registered V2 projects or queued jobs are in use.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_structure_snapshots"
down_revision = "0009_editable_exports"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects",sa.Column("structure_snapshot",sa.JSON(),nullable=True))
    op.add_column("project_revisions",sa.Column("structure_snapshot",sa.JSON(),nullable=True))


def downgrade():
    op.drop_column("project_revisions","structure_snapshot")
    op.drop_column("projects","structure_snapshot")
