"""Add default-safe operations tables; never delete or expire existing customer files."""
from alembic import op
import sqlalchemy as sa

revision = '0011_retention_support'
down_revision = '0010_structure_snapshots'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('retention_accounts',
        sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('tenants.id'), nullable=False, primary_key=True),
        sa.Column('protected_until', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('policy_version', sa.String(length=40), nullable=False, primary_key=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
    )
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE retention_accounts ENABLE ROW LEVEL SECURITY")
    op.create_table('retention_holds',
        sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('tenants.id'), nullable=False, primary_key=False),
        sa.Column('target_kind', sa.String(length=20), nullable=False, primary_key=False),
        sa.Column('target_id', sa.String(length=36), nullable=False, primary_key=False),
        sa.Column('reason_code', sa.String(length=40), nullable=False, primary_key=False),
        sa.Column('reason', sa.Text(), nullable=False, primary_key=False),
        sa.Column('source_id', sa.String(length=36), nullable=True, primary_key=False),
        sa.Column('created_by', sa.String(length=36), sa.ForeignKey('users.id'), nullable=True, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('released_at', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('released_by', sa.String(length=36), sa.ForeignKey('users.id'), nullable=True, primary_key=False),
        sa.Column('release_reason', sa.Text(), nullable=True, primary_key=False),
        sa.Column('revision', sa.Integer(), nullable=False, primary_key=False),
    )
    op.create_index('ix_retention_holds_tenant_id', 'retention_holds', ['tenant_id'], unique=False)
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE retention_holds ENABLE ROW LEVEL SECURITY")
    op.create_table('deletion_requests',
        sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('tenants.id'), nullable=False, primary_key=False),
        sa.Column('requested_by', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False, primary_key=False),
        sa.Column('target_kind', sa.String(length=20), nullable=False, primary_key=False),
        sa.Column('target_id', sa.String(length=36), nullable=False, primary_key=False),
        sa.Column('reason', sa.Text(), nullable=False, primary_key=False),
        sa.Column('operation_key', sa.String(length=160), nullable=False, primary_key=False),
        sa.Column('request_hash', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('status', sa.String(length=30), nullable=False, primary_key=False),
        sa.Column('reviewed_by', sa.String(length=36), sa.ForeignKey('users.id'), nullable=True, primary_key=False),
        sa.Column('review_reason', sa.Text(), nullable=True, primary_key=False),
        sa.Column('revision', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.UniqueConstraint('tenant_id', 'operation_key', name='uq_deletion_operation'),
    )
    op.create_index('ix_deletion_requests_status', 'deletion_requests', ['status'], unique=False)
    op.create_index('ix_deletion_requests_tenant_id', 'deletion_requests', ['tenant_id'], unique=False)
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE deletion_requests ENABLE ROW LEVEL SECURITY")
    op.create_table('retention_notices',
        sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('tenants.id'), nullable=False, primary_key=False),
        sa.Column('event_key', sa.String(length=160), nullable=False, primary_key=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('stage_days', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('status', sa.String(length=20), nullable=False, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.UniqueConstraint('tenant_id', 'event_key', name='uq_retention_notice'),
    )
    op.create_index('ix_retention_notices_tenant_id', 'retention_notices', ['tenant_id'], unique=False)
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE retention_notices ENABLE ROW LEVEL SECURITY")
    op.create_table('support_access_sessions',
        sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('tenants.id'), nullable=False, primary_key=False),
        sa.Column('actor_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False, primary_key=False),
        sa.Column('login_session_id', sa.String(length=36), nullable=False, primary_key=False),
        sa.Column('target_kind', sa.String(length=20), nullable=False, primary_key=False),
        sa.Column('target_id', sa.String(length=36), nullable=False, primary_key=False),
        sa.Column('reason', sa.Text(), nullable=False, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True, primary_key=False),
    )
    op.create_index('ix_support_access_sessions_tenant_id', 'support_access_sessions', ['tenant_id'], unique=False)
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE support_access_sessions ENABLE ROW LEVEL SECURITY")


def downgrade():
    # Operators must stop workers and preserve holds/pins before removing this schema.
    op.drop_table('support_access_sessions')
    op.drop_table('retention_notices')
    op.drop_table('deletion_requests')
    op.drop_table('retention_holds')
    op.drop_table('retention_accounts')
