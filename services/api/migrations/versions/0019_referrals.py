"""Referral codes and referral bonus records."""
from alembic import op
import sqlalchemy as sa

revision='0019_referrals'
down_revision='0018_service_checkout'
branch_labels=None
depends_on=None


def _lock_down(table):
    if op.get_bind().dialect.name=='postgresql':
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON TABLE {table} FROM PUBLIC')
        for role in ('anon','authenticated'):
            op.execute(f"DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN REVOKE ALL ON TABLE {table} FROM {role}; END IF; END $$;")


def upgrade():
    op.create_table('referral_codes',
        sa.Column('tenant_id',sa.String(36),sa.ForeignKey('tenants.id'),primary_key=True),
        sa.Column('code',sa.String(16),nullable=False,unique=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_referral_codes_code','referral_codes',['code'])
    op.create_table('referrals',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('referrer_tenant_id',sa.String(36),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('referred_tenant_id',sa.String(36),sa.ForeignKey('tenants.id'),nullable=False,unique=True),
        sa.Column('code',sa.String(16),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),
        sa.Column('note',sa.String(300),nullable=True),
        sa.Column('granted_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("status IN ('pending','granted','void')",name='ck_referral_status'),
        sa.CheckConstraint('referrer_tenant_id <> referred_tenant_id',name='ck_referral_not_self'))
    op.create_index('ix_referrals_referrer_tenant_id','referrals',['referrer_tenant_id'])
    for table in ('referral_codes','referrals'):_lock_down(table)


def downgrade():
    op.drop_table('referrals')
    op.drop_table('referral_codes')
