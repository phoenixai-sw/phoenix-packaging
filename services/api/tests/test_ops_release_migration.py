from io import StringIO
from pathlib import Path
import re
from alembic import command
from alembic.config import Config


def test_full_operations_upgrade_generates_postgres_sql_without_live_role_queries(monkeypatch):
    monkeypatch.setenv('APP_ENV','test')
    monkeypatch.setenv('DATABASE_URL','postgresql://fixture:fixture@must-not-connect.invalid/fixture')
    output=StringIO();config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'),output_buffer=output)
    command.upgrade(config,'0010_structure_snapshots:0017_metrics',sql=True)
    sql=output.getvalue();tables=re.findall(r'CREATE TABLE ([a-z_]+)',sql)
    assert len(tables)==22
    assert all(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY' in sql for table in tables)
    assert 'CREATE POLICY' not in sql and 'DROP TABLE' not in sql and 'DELETE FROM' not in sql
    for table in ['service_orders','service_order_quotes','service_order_events']:
        assert f'REVOKE ALL ON TABLE {table} FROM PUBLIC' in sql
        for role in ['anon','authenticated']:assert f"IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN REVOKE ALL ON TABLE {table} FROM {role}" in sql
    assert 'UPDATE subscriptions SET pricing_snapshot' in sql
    assert 'UPDATE payment_orders SET pricing_snapshot' in sql
    assert 'CREATE TRIGGER immutable_font' in sql
    assert "version_num='0017_metrics'" in sql
