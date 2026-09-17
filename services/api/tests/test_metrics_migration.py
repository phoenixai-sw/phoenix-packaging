from io import StringIO
from pathlib import Path
from uuid import uuid4
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,inspect,text


def test_metrics_migration_is_additive_and_downgrade_preserves_customer(tmp_path,monkeypatch):
    url=f"sqlite:///{tmp_path/'metrics.db'}";monkeypatch.setenv('APP_ENV','test');monkeypatch.setenv('DATABASE_URL',url)
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'));command.upgrade(config,'0016_font_assets')
    engine=create_engine(url);tenant=str(uuid4())
    with engine.begin() as db:db.execute(text("INSERT INTO tenants(id,name,created_at) VALUES(:id,'원문 보존','2026-09-18')"),{'id':tenant})
    command.upgrade(config,'0017_metrics')
    assert {'metric_events','metric_acquisitions','metric_activity_slices','metric_cost_entries'}<=set(inspect(engine).get_table_names())
    with engine.connect() as db:
        assert db.scalar(text('SELECT name FROM tenants WHERE id=:id'),{'id':tenant})=='원문 보존'
        assert list(db.execute(text('PRAGMA foreign_key_check')))==[]
    command.downgrade(config,'0016_font_assets')
    assert 'metric_events' not in inspect(engine).get_table_names()
    with engine.connect() as db:assert db.scalar(text('SELECT name FROM tenants WHERE id=:id'),{'id':tenant})=='원문 보존'
    engine.dispose()


def test_metrics_postgres_rls_and_unique_event_activity_cost_keys(monkeypatch):
    monkeypatch.setenv('APP_ENV','test');monkeypatch.setenv('DATABASE_URL','postgresql://fixture:fixture@localhost/fixture')
    output=StringIO();config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'),output_buffer=output)
    command.upgrade(config,'0016_font_assets:0017_metrics',sql=True);sql=output.getvalue()
    for table in ['metric_events','metric_acquisitions','metric_activity_slices','metric_cost_entries']:assert f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY' in sql
    assert 'CREATE POLICY' not in sql and 'UNIQUE (event_key)' in sql and 'UNIQUE (project_id, time_bucket)' in sql and 'UNIQUE (supersedes_id)' in sql
