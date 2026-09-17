from io import StringIO
from pathlib import Path
import json
from uuid import uuid4
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,text,inspect


def test_policy_migration_freezes_existing_contract_and_postgres_rls(tmp_path,monkeypatch):
    url=f"sqlite:///{tmp_path/'policy-migration.db'}"
    monkeypatch.setenv('APP_ENV','test');monkeypatch.setenv('DATABASE_URL',url)
    cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.upgrade(cfg,'0014_customer_deletion');engine=create_engine(url)
    tenant,subscription=str(uuid4()),str(uuid4())
    with engine.begin() as db:
        db.execute(text("INSERT INTO tenants (id,name,created_at) VALUES (:id,'Legacy','2026-09-18')"),{'id':tenant})
        db.execute(text("INSERT INTO subscriptions (id,tenant_id,plan_id,status,anchor_day,timezone,billing_anchor,current_period_start,current_period_end,cancel_at_period_end,created_at) VALUES (:id,:tenant,'starter','active',18,'Asia/Seoul','2026-09-18','2026-09-18','2026-10-18',0,'2026-09-18')"),{'id':subscription,'tenant':tenant})
    command.upgrade(cfg,'0015_admin_policies')
    with engine.connect() as db:
        frozen=json.loads(db.scalar(text('SELECT pricing_snapshot FROM subscriptions')))
        assert frozen['plans'][0]['monthly_inc_vat']==53900 and frozen['actions']['editor.manual']==0
        assert not list(db.execute(text('PRAGMA foreign_key_check')))
    command.downgrade(cfg,'0014_customer_deletion')
    assert 'pricing_snapshot' not in {c['name'] for c in inspect(engine).get_columns('subscriptions')}
    engine.dispose()
    monkeypatch.setenv('DATABASE_URL','postgresql://fixture:fixture@localhost/fixture')
    stream=StringIO();cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'),output_buffer=stream)
    command.upgrade(cfg,'0014_customer_deletion:0015_admin_policies',sql=True)
    sql=stream.getvalue()
    for name in ('operation_policy_versions','operation_active_policies','credit_corrections'):
        assert f'ALTER TABLE {name} ENABLE ROW LEVEL SECURITY' in sql
    assert 'UPDATE subscriptions SET pricing_snapshot' in sql and 'CREATE POLICY' not in sql
