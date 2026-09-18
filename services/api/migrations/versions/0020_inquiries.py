"""Public sales inquiries from the landing/pricing form."""
from alembic import op
import sqlalchemy as sa

revision='0020_inquiries'
down_revision='0019_referrals'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('inquiries',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('company',sa.String(120),nullable=False),
        sa.Column('name',sa.String(80),nullable=False),
        sa.Column('email',sa.String(254),nullable=False),
        sa.Column('phone',sa.String(40),nullable=True),
        sa.Column('package_type',sa.String(30),nullable=False),
        sa.Column('monthly_changes',sa.Integer(),nullable=False),
        sa.Column('next_order_date',sa.String(10),nullable=True),
        sa.Column('has_dieline',sa.Boolean(),nullable=False),
        sa.Column('message',sa.Text(),nullable=True),
        sa.Column('source',sa.String(40),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),
        sa.Column('note',sa.String(1000),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_inquiries_created_at','inquiries',['created_at'])
    if op.get_bind().dialect.name=='postgresql':
        op.execute('ALTER TABLE inquiries ENABLE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON TABLE inquiries FROM PUBLIC')
        for role in ('anon','authenticated'):
            op.execute(f"DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN REVOKE ALL ON TABLE inquiries FROM {role}; END IF; END $$;")


def downgrade():
    op.drop_table('inquiries')
