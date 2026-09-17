"""Separate service requests and immutable quote history; no billing changes."""
from alembic import op
import sqlalchemy as sa
revision='0013_service_orders'
down_revision='0012_storage_lifecycle'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('service_orders',
        sa.Column('id',sa.String(36),primary_key=True),sa.Column('tenant_id',sa.String(36),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('requested_by',sa.String(36),sa.ForeignKey('users.id'),nullable=False),sa.Column('service_code',sa.String(40),nullable=False),
        sa.Column('project_id',sa.String(36),sa.ForeignKey('projects.id'),nullable=True),sa.Column('request_note',sa.Text,nullable=False),
        sa.Column('catalog_snapshot',sa.JSON,nullable=False),sa.Column('catalog_policy_version',sa.String(80),nullable=False),
        sa.Column('operation_key',sa.String(160),nullable=False),sa.Column('request_hash',sa.String(64),nullable=False),
        sa.Column('status',sa.String(30),nullable=False),sa.Column('revision',sa.Integer,nullable=False),
        sa.Column('current_quote_id',sa.String(36),nullable=True),sa.Column('accepted_quote_id',sa.String(36),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('tenant_id','operation_key',name='uq_service_order_operation'),sa.CheckConstraint('revision > 0',name='ck_service_order_revision'),
        sa.CheckConstraint("status IN ('requested','quoted','accepted','in_progress','delivered','completed','canceled','rejected')",name='ck_service_order_status'))
    op.create_index('ix_service_orders_tenant_id','service_orders',['tenant_id']);op.create_index('ix_service_orders_status','service_orders',['status'])
    op.create_table('service_order_quotes',
        sa.Column('id',sa.String(36),primary_key=True),sa.Column('tenant_id',sa.String(36),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('order_id',sa.String(36),sa.ForeignKey('service_orders.id'),nullable=False),sa.Column('number',sa.Integer,nullable=False),
        sa.Column('amount_inc_vat',sa.Integer,nullable=False),sa.Column('currency',sa.String(3),nullable=False),
        sa.Column('scope',sa.Text,nullable=False),sa.Column('exclusions',sa.Text,nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('policy_version',sa.String(80),nullable=False),sa.Column('quoted_by',sa.String(36),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('reason',sa.String(500),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('order_id','number',name='uq_service_quote_number'),sa.CheckConstraint('amount_inc_vat >= 0 AND amount_inc_vat <= 10000000',name='ck_service_quote_amount'))
    op.create_index('ix_service_order_quotes_tenant_id','service_order_quotes',['tenant_id']);op.create_index('ix_service_order_quotes_order_id','service_order_quotes',['order_id'])
    op.create_table('service_order_events',
        sa.Column('id',sa.String(36),primary_key=True),sa.Column('tenant_id',sa.String(36),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('order_id',sa.String(36),sa.ForeignKey('service_orders.id'),nullable=False),sa.Column('order_revision',sa.Integer,nullable=False),
        sa.Column('actor_id',sa.String(36),sa.ForeignKey('users.id'),nullable=False),sa.Column('kind',sa.String(40),nullable=False),
        sa.Column('note',sa.Text,nullable=False),sa.Column('quote_id',sa.String(36),sa.ForeignKey('service_order_quotes.id'),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('order_id','order_revision',name='uq_service_order_event_revision'))
    op.create_index('ix_service_order_events_tenant_id','service_order_events',['tenant_id']);op.create_index('ix_service_order_events_order_id','service_order_events',['order_id'])
    if op.get_bind().dialect.name=='postgresql':
        for name in ('service_orders','service_order_quotes','service_order_events'):
            op.execute(sa.text(f'ALTER TABLE {name} ENABLE ROW LEVEL SECURITY'));op.execute(sa.text(f'REVOKE ALL ON TABLE {name} FROM PUBLIC'))
            for role in ('anon','authenticated'):
                # Keep the role probe in PostgreSQL so offline SQL generation
                # and the online transaction execute exactly the same logic.
                op.execute(sa.text(f"DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN REVOKE ALL ON TABLE {name} FROM {role}; END IF; END $$;"))


def downgrade():
    for name in ('service_order_events','service_order_quotes','service_orders'):op.drop_table(name)
