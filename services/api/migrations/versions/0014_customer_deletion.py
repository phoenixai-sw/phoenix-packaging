"""Durable explicitly approved single-file deletion. Execution remains off."""
from alembic import op
import sqlalchemy as sa

revision = "0014_customer_deletion"
down_revision = "0013_service_orders"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("deletion_requests", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("deletion_requests", sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("deletion_requests", sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("deletion_requests", sa.Column("execution_snapshot", sa.JSON(), nullable=True))
    op.add_column("deletion_requests", sa.Column("blocker", sa.String(80), nullable=True))
    op.add_column("deletion_requests", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    # Existing approvals have no due_at/snapshot and cannot enter the executor.


def downgrade():
    # Stop workers and preserve control metadata before an operator downgrade.
    with op.batch_alter_table("deletion_requests") as batch:
        for name in ("attempts", "blocker", "execution_snapshot", "checked_at", "executed_at", "due_at"):
            batch.drop_column(name)
