"""Unique export leases and one running export per tenant."""
from alembic import op
import sqlalchemy as sa

revision = "0003_job_leases"
down_revision = "0002_email_tokens"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("lease_id", sa.String(36), nullable=True))
    op.create_index("uq_running_export_tenant", "jobs", ["tenant_id"], unique=True, postgresql_where=sa.text("status = 'running'"), sqlite_where=sa.text("status = 'running'"))


def downgrade():
    op.drop_index("uq_running_export_tenant", table_name="jobs")
    op.drop_column("jobs", "lease_id")
