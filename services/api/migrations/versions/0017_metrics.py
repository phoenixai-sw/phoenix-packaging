"""Internal content-free metrics and append-only operating costs."""
from alembic import op
import sqlalchemy as sa

revision = "0017_metrics"
down_revision = "0016_font_assets"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('metric_events',
        sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('event_key', sa.String(length=200), nullable=False, primary_key=False),
        sa.Column('name', sa.String(length=80), nullable=False, primary_key=False),
        sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('tenants.id'), nullable=False, primary_key=False),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), nullable=True, primary_key=False),
        sa.Column('job_id', sa.String(length=36), sa.ForeignKey('jobs.id'), nullable=True, primary_key=False),
        sa.Column('revision_id', sa.String(length=36), sa.ForeignKey('project_revisions.id'), nullable=True, primary_key=False),
        sa.Column('policy_version', sa.String(length=100), nullable=False, primary_key=False),
        sa.Column('properties', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.UniqueConstraint('event_key', name=None),
    )
    op.create_index('ix_metric_events_name', 'metric_events', ['name'], unique=False)
    op.create_index('ix_metric_events_occurred_at', 'metric_events', ['occurred_at'], unique=False)
    op.create_index('ix_metric_events_tenant_id', 'metric_events', ['tenant_id'], unique=False)
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE metric_events ENABLE ROW LEVEL SECURITY")
    op.create_table('metric_acquisitions',
        sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('tenants.id'), nullable=False, primary_key=True),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False, primary_key=False),
        sa.Column('channel', sa.String(length=30), nullable=False, primary_key=False),
        sa.Column('utm_source', sa.String(length=80), nullable=True, primary_key=False),
        sa.Column('utm_medium', sa.String(length=80), nullable=True, primary_key=False),
        sa.Column('utm_campaign', sa.String(length=80), nullable=True, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
    )
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE metric_acquisitions ENABLE ROW LEVEL SECURITY")
    op.create_table('metric_activity_slices',
        sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('tenants.id'), nullable=False, primary_key=False),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), nullable=False, primary_key=False),
        sa.Column('actor_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False, primary_key=False),
        sa.Column('session_id', sa.String(length=36), nullable=False, primary_key=False),
        sa.Column('lease_id', sa.String(length=36), nullable=False, primary_key=False),
        sa.Column('time_bucket', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('seconds', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.UniqueConstraint('project_id', 'time_bucket', name='uq_metric_activity_bucket'),
    )
    op.create_index('ix_metric_activity_slices_tenant_id', 'metric_activity_slices', ['tenant_id'], unique=False)
    op.create_index('ix_metric_activity_slices_project_id', 'metric_activity_slices', ['project_id'], unique=False)
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE metric_activity_slices ENABLE ROW LEVEL SECURITY")
    op.create_table('metric_cost_entries',
        sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('operation_key', sa.String(length=160), nullable=False, primary_key=False),
        sa.Column('request_hash', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('tenants.id'), nullable=True, primary_key=False),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), nullable=True, primary_key=False),
        sa.Column('job_id', sa.String(length=36), sa.ForeignKey('jobs.id'), nullable=True, primary_key=False),
        sa.Column('payment_id', sa.String(length=36), sa.ForeignKey('payments.id'), nullable=True, primary_key=False),
        sa.Column('actor_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False, primary_key=False),
        sa.Column('category', sa.String(length=30), nullable=False, primary_key=False),
        sa.Column('basis', sa.String(length=20), nullable=False, primary_key=False),
        sa.Column('amount', sa.Numeric(precision=20, scale=6), nullable=True, primary_key=False),
        sa.Column('currency', sa.String(length=3), nullable=False, primary_key=False),
        sa.Column('support_minutes', sa.Integer(), nullable=True, primary_key=False),
        sa.Column('reason', sa.Text(), nullable=False, primary_key=False),
        sa.Column('supersedes_id', sa.String(length=36), sa.ForeignKey('metric_cost_entries.id'), nullable=True, primary_key=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.UniqueConstraint('supersedes_id', name=None),
        sa.UniqueConstraint('operation_key', name=None),
    )
    op.create_index('ix_metric_cost_entries_tenant_id', 'metric_cost_entries', ['tenant_id'], unique=False)
    op.create_index('ix_metric_cost_entries_occurred_at', 'metric_cost_entries', ['occurred_at'], unique=False)
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE metric_cost_entries ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table('metric_cost_entries')
    op.drop_table('metric_activity_slices')
    op.drop_table('metric_acquisitions')
    op.drop_table('metric_events')
