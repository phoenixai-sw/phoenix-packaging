"""Immutable tenant font originals and explicit brand selection allowlists."""
from alembic import op
import sqlalchemy as sa

revision = '0016_font_assets'
down_revision = '0015_admin_policies'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('font_assets',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('tenant_id',sa.String(36),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('created_by',sa.String(36),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('storage_key',sa.String(300),nullable=False,unique=True),
        sa.Column('original_name',sa.String(160),nullable=False),
        sa.Column('family',sa.String(120),nullable=False),sa.Column('subfamily',sa.String(120),nullable=False),
        sa.Column('weight',sa.Integer(),nullable=False),sa.Column('sha256',sa.String(64),nullable=False),
        sa.Column('byte_size',sa.Integer(),nullable=False),sa.Column('glyph_count',sa.Integer(),nullable=False),
        sa.Column('fs_type',sa.Integer(),nullable=False),sa.Column('ascent_ratio',sa.Float(),nullable=False),sa.Column('descent_ratio',sa.Float(),nullable=False),sa.Column('license_name',sa.String(200),nullable=False),
        sa.Column('license_text',sa.Text(),nullable=False),sa.Column('source_url',sa.String(2000),nullable=False),
        sa.Column('rights_holder',sa.String(200),nullable=False),sa.Column('redistribution_allowed',sa.Boolean(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_font_assets_tenant_id','font_assets',['tenant_id'])
    op.create_table('font_upload_sessions',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('tenant_id',sa.String(36),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('user_id',sa.String(36),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('storage_key',sa.String(300),nullable=False,unique=True),sa.Column('name',sa.String(160),nullable=False),
        sa.Column('byte_size',sa.Integer(),nullable=False),sa.Column('declaration',sa.JSON(),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),
        sa.Column('font_asset_id',sa.String(36),sa.ForeignKey('font_assets.id'),nullable=True),
        sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_font_upload_sessions_tenant_id','font_upload_sessions',['tenant_id'])
    op.add_column('brands',sa.Column('font_asset_ids',sa.JSON(),nullable=False,server_default='[]'))
    if op.get_bind().dialect.name == 'postgresql':
        for table in ('font_assets','font_upload_sessions'):
            op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute("CREATE FUNCTION phoenix_immutable_font() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Font assets are immutable'; END; $$")
        op.execute('CREATE TRIGGER immutable_font BEFORE UPDATE OR DELETE ON font_assets FOR EACH ROW EXECUTE FUNCTION phoenix_immutable_font()')
    else:
        for action in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER immutable_font_{action.lower()} BEFORE {action} ON font_assets BEGIN SELECT RAISE(ABORT, 'Font assets are immutable'); END")


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP TRIGGER IF EXISTS immutable_font ON font_assets')
        op.execute('DROP FUNCTION IF EXISTS phoenix_immutable_font()')
    else:
        for action in ('update','delete'): op.execute(f'DROP TRIGGER IF EXISTS immutable_font_{action}')
    with op.batch_alter_table('brands') as batch: batch.drop_column('font_asset_ids')
    op.drop_table('font_upload_sessions')
    op.drop_table('font_assets')
