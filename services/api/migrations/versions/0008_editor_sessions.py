"""Add expiring project editor leases without changing existing scenes/history.

Stage API/web builds first, back up the current database, run Alembic upgrade
head with validated hosted settings, then promote API followed by web. Old API
ignores the additive table; old clients retain CAS when no active lease exists.
Never use downgrade as a rollback while the new API is serving lease requests.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_editor_sessions"
down_revision = "0007_google_auth"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("project_edit_leases",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("login_session_id", sa.String(36), nullable=False),
        sa.Column("editor_id", sa.String(36), nullable=False),
        sa.Column("lease_token", sa.String(36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_project_edit_leases_tenant_id", "project_edit_leases", ["tenant_id"])
    op.create_index("ix_project_edit_leases_expires_at", "project_edit_leases", ["expires_at"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("ALTER TABLE project_edit_leases ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text("REVOKE ALL ON TABLE project_edit_leases FROM PUBLIC"))
        for role in ("anon", "authenticated"):
            if op.get_bind().scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}):
                op.execute(sa.text(f'REVOKE ALL ON TABLE project_edit_leases FROM "{role}"'))


def downgrade():
    op.drop_index("ix_project_edit_leases_expires_at", table_name="project_edit_leases")
    op.drop_index("ix_project_edit_leases_tenant_id", table_name="project_edit_leases")
    op.drop_table("project_edit_leases")
