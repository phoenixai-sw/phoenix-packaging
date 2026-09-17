"""Replace password and email token authentication with Google identities.

Credential removal is intentional. Downgrade cannot restore old secrets.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_google_auth"
down_revision = "0006_direct_uploads"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("google_sub", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("google_email_authoritative", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_users_google_sub", "users", ["google_sub"], unique=True)
    op.execute(sa.text("UPDATE users SET password_hash='', is_admin=false"))
    op.execute(sa.text("DELETE FROM login_sessions"))
    op.drop_table("auth_tokens")
    op.create_table("google_login_challenges",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("nonce_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("ALTER TABLE google_login_challenges ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text("REVOKE ALL ON TABLE google_login_challenges FROM PUBLIC"))
        for role in ("anon", "authenticated"):
            if op.get_bind().scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}):
                op.execute(sa.text(f'REVOKE ALL ON TABLE google_login_challenges FROM "{role}"'))


def downgrade():
    raise RuntimeError("Google authentication migration removes legacy credentials; restore an audited pre-migration backup to roll back.")
