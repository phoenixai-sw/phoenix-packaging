"""Initial tenant-isolated application schema.

Revision ID: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def pk():
    return sa.Column("id", sa.String(36), primary_key=True)


def tenant():
    return sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, index=True)


def timestamp(name="created_at"):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def upgrade():
    op.create_table("tenants", pk(), sa.Column("name", sa.String(120), nullable=False), timestamp())
    op.create_table("users", pk(), tenant(), sa.Column("name", sa.String(80), nullable=False), sa.Column("email", sa.String(254), nullable=False), sa.Column("password_hash", sa.Text(), nullable=False), sa.Column("role", sa.String(20), nullable=False), timestamp())
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table("login_sessions", pk(), sa.Column("token_hash", sa.String(64), nullable=False), sa.Column("csrf_token", sa.String(128), nullable=False), sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True), timestamp())
    op.create_index("ix_login_sessions_token_hash", "login_sessions", ["token_hash"], unique=True)
    op.create_table("auth_attempts", pk(), sa.Column("key_hash", sa.String(64), nullable=False, index=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True))
    op.create_table("projects", pk(), tenant(), sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False), sa.Column("name", sa.String(160), nullable=False), sa.Column("product_name", sa.String(160), nullable=False), sa.Column("brand_name", sa.String(120), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("template_id", sa.String(80), nullable=False), sa.Column("width_mm", sa.Float(), nullable=False), sa.Column("height_mm", sa.Float(), nullable=False), sa.Column("base_revision", sa.Integer(), nullable=False), sa.Column("scene", sa.JSON(), nullable=False), timestamp(), timestamp("updated_at"))
    op.create_table("project_revisions", pk(), sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True), tenant(), sa.Column("number", sa.Integer(), nullable=False), sa.Column("scene", sa.JSON(), nullable=False), sa.Column("reason", sa.String(80), nullable=False), timestamp(), sa.UniqueConstraint("project_id", "number", name="uq_project_revision"))
    op.create_table("assets", pk(), tenant(), sa.Column("storage_key", sa.String(300), nullable=False, unique=True), sa.Column("original_name", sa.String(160), nullable=False), sa.Column("content_type", sa.String(80), nullable=False), sa.Column("byte_size", sa.Integer(), nullable=False), sa.Column("width_px", sa.Integer(), nullable=False), sa.Column("height_px", sa.Integer(), nullable=False), sa.Column("source", sa.String(40), nullable=False), timestamp())
    op.create_table("jobs", pk(), tenant(), sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True), sa.Column("revision_id", sa.String(36), sa.ForeignKey("project_revisions.id"), nullable=False), sa.Column("kind", sa.String(40), nullable=False), sa.Column("status", sa.String(40), nullable=False, index=True), sa.Column("operation_key", sa.String(200), nullable=False), sa.Column("request_hash", sa.String(64), nullable=False), sa.Column("snapshot", sa.JSON(), nullable=False), sa.Column("result", sa.JSON(), nullable=True), sa.Column("error", sa.Text(), nullable=True), timestamp(), timestamp("updated_at"), sa.UniqueConstraint("tenant_id", "operation_key", name="uq_job_operation_key"))
    if op.get_bind().dialect.name == "postgresql":
        # Only the trusted backend DB role accesses this schema. Supabase browser
        # anon/authenticated roles receive no table access and no RLS policies.
        for table in ("tenants", "users", "login_sessions", "auth_attempts", "projects", "project_revisions", "assets", "jobs"):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'REVOKE ALL ON TABLE "{table}" FROM PUBLIC')
            op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON TABLE \"{table}\" FROM anon; END IF; IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON TABLE \"{table}\" FROM authenticated; END IF; END $$;")


def downgrade():
    for table in ("jobs", "assets", "project_revisions", "projects", "auth_attempts", "login_sessions", "users", "tenants"):
        op.drop_table(table)
