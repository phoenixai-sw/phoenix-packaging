"""Add default-safe operations tables; never delete or expire existing customer files."""
from alembic import op
import sqlalchemy as sa

revision = '0012_storage_lifecycle'
down_revision = '0011_retention_support'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('storage_maintenance_gate',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False, primary_key=False),
    )
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE storage_maintenance_gate ENABLE ROW LEVEL SECURITY")
    op.create_table('storage_backup_runs',
        sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('state', sa.String(length=30), nullable=False, primary_key=False),
        sa.Column('reason', sa.Text(), nullable=False, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('manifest_hash', sa.String(length=64), nullable=True, primary_key=False),
        sa.Column('verified', sa.Boolean(), nullable=False, primary_key=False),
        sa.Column('object_count', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('resolved_by', sa.String(length=36), sa.ForeignKey('users.id'), nullable=True, primary_key=False),
    )
    op.create_index('ix_storage_backup_runs_state', 'storage_backup_runs', ['state'], unique=False)
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE storage_backup_runs ENABLE ROW LEVEL SECURITY")
    op.create_table('storage_backup_pins',
        sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('run_id', sa.String(length=36), sa.ForeignKey('storage_backup_runs.id'), nullable=False, primary_key=False),
        sa.Column('storage_key', sa.String(length=300), nullable=False, primary_key=False),
        sa.UniqueConstraint('run_id', 'storage_key', name='uq_backup_pin'),
    )
    op.create_index('ix_storage_backup_pins_run_id', 'storage_backup_pins', ['run_id'], unique=False)
    op.create_index('ix_storage_backup_pins_storage_key', 'storage_backup_pins', ['storage_key'], unique=False)
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE storage_backup_pins ENABLE ROW LEVEL SECURITY")
    op.create_table('storage_write_intents',
        sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('tenants.id'), nullable=False, primary_key=False),
        sa.Column('job_id', sa.String(length=36), sa.ForeignKey('jobs.id'), nullable=False, primary_key=False),
        sa.Column('unit_id', sa.String(length=36), sa.ForeignKey('ai_units.id'), nullable=True, primary_key=False),
        sa.Column('lease_id', sa.String(length=36), nullable=False, primary_key=False),
        sa.Column('storage_key', sa.String(length=300), nullable=False, primary_key=False),
        sa.Column('sha256', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('byte_size', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('status', sa.String(length=20), nullable=False, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.UniqueConstraint('storage_key', name=None),
    )
    op.create_index('ix_storage_write_intents_status', 'storage_write_intents', ['status'], unique=False)
    op.create_index('ix_storage_write_intents_job_id', 'storage_write_intents', ['job_id'], unique=False)
    op.create_index('ix_storage_write_intents_tenant_id', 'storage_write_intents', ['tenant_id'], unique=False)
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE storage_write_intents ENABLE ROW LEVEL SECURITY")
    op.create_table('storage_gc_candidates',
        sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('intent_id', sa.String(length=36), sa.ForeignKey('storage_write_intents.id'), nullable=False, primary_key=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('status', sa.String(length=20), nullable=False, primary_key=False),
        sa.Column('blocker', sa.String(length=80), nullable=True, primary_key=False),
        sa.Column('attempts', sa.Integer(), nullable=False, primary_key=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.UniqueConstraint('intent_id', name=None),
    )
    if op.get_bind().dialect.name == "postgresql": op.execute("ALTER TABLE storage_gc_candidates ENABLE ROW LEVEL SECURITY")


def downgrade():
    # Operators must stop workers and preserve holds/pins before removing this schema.
    op.drop_table('storage_gc_candidates')
    op.drop_table('storage_write_intents')
    op.drop_table('storage_backup_pins')
    op.drop_table('storage_backup_runs')
    op.drop_table('storage_maintenance_gate')
