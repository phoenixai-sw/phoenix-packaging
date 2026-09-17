"""Versioned runtime policies and reasoned credit corrections; preserve agreed prices."""
from alembic import op
import sqlalchemy as sa
import json
revision='0015_admin_policies'
down_revision='0014_customer_deletion'
branch_labels=None
depends_on=None

LEGACY_PRICING = {'version': '2026-09-18-image-quality-2', 'currency': 'KRW', 'live_billing_enabled': False, 'plans': [{'id': 'starter', 'name': 'Starter', 'monthly_ex_vat': 49000, 'monthly_inc_vat': 53900, 'credits': 500, 'seats': 1}, {'id': 'pro', 'name': 'Pro', 'monthly_ex_vat': 99000, 'monthly_inc_vat': 108900, 'credits': 1500, 'seats': 3}, {'id': 'partner', 'name': 'Partner', 'monthly_ex_vat': 249000, 'monthly_inc_vat': 273900, 'credits': 4500, 'seats': 5}], 'topups': [{'credits': 500, 'ex_vat': 39000, 'inc_vat': 42900, 'expires_months': 12}, {'credits': 1000, 'ex_vat': 69000, 'inc_vat': 75900, 'expires_months': 12}], 'trial': {'credits': 30, 'expires_days': 14, 'production_export': False, 'auto_conversion': False}, 'actions': {'editor.manual': 0, 'preview.all_faces': 0, 'image.generate.standard': 10, 'image.edit.standard': 10, 'image.edit.high': 20, 'image.generate.high': 20, 'export.production.first': 40, 'export.production.repeat': 0, 'export.review': 0}}


def upgrade():
    op.create_table('operation_policy_versions',
        sa.Column('id',sa.String(36),primary_key=True),sa.Column('kind',sa.String(20),nullable=False),sa.Column('version',sa.String(100),nullable=False,unique=True),
        sa.Column('payload',sa.JSON,nullable=False),sa.Column('payload_hash',sa.String(64),nullable=False),sa.Column('reason',sa.Text,nullable=False),
        sa.Column('created_by',sa.String(36),sa.ForeignKey('users.id'),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('published_at',sa.DateTime(timezone=True),nullable=True))
    op.create_index('ix_operation_policy_versions_kind','operation_policy_versions',['kind'])
    op.create_table('operation_active_policies',sa.Column('kind',sa.String(20),primary_key=True),
        sa.Column('version_id',sa.String(36),sa.ForeignKey('operation_policy_versions.id'),nullable=True),sa.Column('revision',sa.Integer,nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.execute("INSERT INTO operation_active_policies (kind,version_id,revision,updated_at) VALUES ('pricing',NULL,0,CURRENT_TIMESTAMP),('image',NULL,0,CURRENT_TIMESTAMP)")
    op.create_table('credit_corrections',sa.Column('id',sa.String(36),primary_key=True),sa.Column('tenant_id',sa.String(36),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('operation_key',sa.String(160),nullable=False),sa.Column('request_hash',sa.String(64),nullable=False),sa.Column('amount',sa.Integer,nullable=False),
        sa.Column('bucket_id',sa.String(36),sa.ForeignKey('credit_buckets.id'),nullable=False),sa.Column('scope',sa.String(30),nullable=False),sa.Column('reason',sa.String(500),nullable=False),
        sa.Column('actor_id',sa.String(36),sa.ForeignKey('users.id'),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('tenant_id','operation_key',name='uq_credit_correction_operation'),sa.CheckConstraint('amount != 0',name='ck_credit_correction_amount'))
    op.create_index('ix_credit_corrections_tenant_id','credit_corrections',['tenant_id'])
    for table,fields in [('subscriptions',('pricing_snapshot','next_pricing_snapshot')),('payment_orders',('pricing_snapshot','source_pricing_snapshot'))]:
        for field in fields:op.add_column(table,sa.Column(field,sa.JSON,nullable=True))
    for table_name in ('subscriptions','payment_orders'):
        frozen=json.dumps(LEGACY_PRICING,ensure_ascii=False).replace("'","''")
        op.execute(sa.text(f"UPDATE {table_name} SET pricing_snapshot = '{frozen}'"))
    if op.get_bind().dialect.name=='postgresql':
        for table in ('operation_policy_versions','operation_active_policies','credit_corrections'):
            op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            op.execute(sa.text(f"DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON {table} FROM anon; END IF; IF EXISTS (SELECT FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON {table} FROM authenticated; END IF; END $$;"))


def downgrade():
    for table,fields in [('payment_orders',('source_pricing_snapshot','pricing_snapshot')),('subscriptions',('next_pricing_snapshot','pricing_snapshot'))]:
        for field in fields:op.drop_column(table,field)
    op.drop_table('credit_corrections');op.drop_table('operation_active_policies');op.drop_table('operation_policy_versions')
