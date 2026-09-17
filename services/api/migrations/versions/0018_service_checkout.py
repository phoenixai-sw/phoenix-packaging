"""Frozen accepted service quotes and payment links; legacy quotes stay ineligible."""
from alembic import op
import sqlalchemy as sa

revision='0018_service_checkout'
down_revision='0017_metrics'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('service_order_quotes',sa.Column('checkout_terms',sa.JSON(),nullable=True))
    op.create_table('service_checkouts',
        sa.Column('payment_order_id',sa.String(36),sa.ForeignKey('payment_orders.id'),primary_key=True),
        sa.Column('tenant_id',sa.String(36),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('service_order_id',sa.String(36),sa.ForeignKey('service_orders.id'),nullable=False,unique=True),
        sa.Column('service_quote_id',sa.String(36),sa.ForeignKey('service_order_quotes.id'),nullable=False),
        sa.Column('actor_id',sa.String(36),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('accepted_revision',sa.Integer(),nullable=False),
        sa.Column('snapshot',sa.JSON(),nullable=False),sa.Column('consent',sa.JSON(),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_service_checkouts_tenant_id','service_checkouts',['tenant_id'])
    if op.get_bind().dialect.name=='postgresql':
        op.execute('ALTER TABLE service_checkouts ENABLE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON TABLE service_checkouts FROM PUBLIC')
        for role in ('anon','authenticated'):
            op.execute(f"DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN REVOKE ALL ON TABLE service_checkouts FROM {role}; END IF; END $$;")
        op.execute("CREATE FUNCTION immutable_service_checkout() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Service checkout evidence is immutable'; END $$")
        op.execute('CREATE TRIGGER immutable_service_checkout BEFORE UPDATE OR DELETE ON service_checkouts FOR EACH ROW EXECUTE FUNCTION immutable_service_checkout()')
        op.execute('CREATE TRIGGER immutable_service_quote BEFORE UPDATE OR DELETE ON service_order_quotes FOR EACH ROW EXECUTE FUNCTION immutable_service_checkout()')
    else:
        for action in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER immutable_service_checkout_{action.lower()} BEFORE {action} ON service_checkouts BEGIN SELECT RAISE(ABORT,'Service checkout evidence is immutable'); END")
            op.execute(f"CREATE TRIGGER immutable_service_quote_{action.lower()} BEFORE {action} ON service_order_quotes BEGIN SELECT RAISE(ABORT,'Service quote evidence is immutable'); END")


def downgrade():
    op.drop_table('service_checkouts')
    if op.get_bind().dialect.name=='postgresql':
        op.execute('DROP TRIGGER immutable_service_quote ON service_order_quotes')
        op.execute('DROP FUNCTION immutable_service_checkout()')
    else:
        for action in ('update','delete'):op.execute(f'DROP TRIGGER immutable_service_quote_{action}')
    op.drop_column('service_order_quotes','checkout_terms')
