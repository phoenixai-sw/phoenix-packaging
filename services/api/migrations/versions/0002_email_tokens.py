"""Hashed single-use email verification and password reset links."""
from alembic import op
import sqlalchemy as sa

revision = "0002_email_tokens"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("auth_tokens", sa.Column("id", sa.String(36), primary_key=True), sa.Column("token_hash", sa.String(64), nullable=False), sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True), sa.Column("kind", sa.String(30), nullable=False, index=True), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_auth_tokens_token_hash", "auth_tokens", ["token_hash"], unique=True)
    if op.get_bind().dialect.name == "postgresql":
        for table in ("auth_tokens", "alembic_version"):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'REVOKE ALL ON TABLE "{table}" FROM PUBLIC')
            op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON TABLE \"{table}\" FROM anon; END IF; IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON TABLE \"{table}\" FROM authenticated; END IF; END $$;")


def downgrade():
    op.drop_table("auth_tokens")
    op.drop_column("users", "email_verified_at")
