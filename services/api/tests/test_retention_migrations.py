from io import StringIO
from pathlib import Path
from uuid import uuid4
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, MetaData, Table, select, text


def test_additive_operations_migration_preserves_original_metadata_and_rolls_back(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path/'operations-migration.db'}"
    monkeypatch.setenv("APP_ENV", "test"); monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(Path(__file__).resolve().parents[1]/"alembic.ini"))
    command.upgrade(cfg, "0010_structure_snapshots")
    engine = create_engine(url)
    metadata = MetaData(); metadata.reflect(bind=engine)
    tenant, user, asset = str(uuid4()), str(uuid4()), str(uuid4())
    raw_metadata = {"sha256": "f"*64, "image_quality": {"root_source_asset_id": asset, "native_equivalent_pixels": [32, 40]}}
    with engine.begin() as db:
        db.execute(text("INSERT INTO tenants(id,name,created_at) VALUES(:id,'Legacy','2026-09-18')"), {"id": tenant})
        db.execute(text("INSERT INTO users(id,tenant_id,name,email,password_hash,role,is_admin,is_active,google_email_authoritative,created_at) VALUES(:id,:tenant,'Owner','fixture@example.com','','owner',0,1,0,'2026-09-18')"), {"id": user,"tenant":tenant})
        db.execute(metadata.tables["assets"].insert().values(id=asset,tenant_id=tenant,storage_key=f"{tenant}/assets/{asset}",original_name="원본.png",content_type="image/png",byte_size=100,width_px=32,height_px=40,source="upload",metadata_json=raw_metadata,created_at=__import__('datetime').datetime(2026,9,18)))
    command.upgrade(cfg, "0013_service_orders")
    before = MetaData(); before.reflect(bind=engine)
    with engine.begin() as db:
        db.execute(before.tables["deletion_requests"].insert().values(id=str(uuid4()),tenant_id=tenant,requested_by=user,target_kind="asset",target_id=asset,reason="이전 승인 자료 보존",operation_key="legacy",request_hash="a"*64,status="approved",reviewed_by=None,review_reason=None,revision=2,created_at=__import__("datetime").datetime(2026,9,18),updated_at=__import__("datetime").datetime(2026,9,18)))
    command.upgrade(cfg, "0014_customer_deletion")
    tables = inspect(engine).get_table_names()
    assert "storage_write_intents" in tables and "support_access_sessions" in tables
    columns = {c["name"] for c in inspect(engine).get_columns("deletion_requests")}
    assert {"due_at", "execution_snapshot", "executed_at", "checked_at", "attempts"} <= columns
    with engine.connect() as db:
        assert db.scalar(select(metadata.tables["assets"].c.metadata_json)) == raw_metadata
        assert not list(db.execute(text("PRAGMA foreign_key_check")))
        old = db.execute(text("SELECT due_at, execution_snapshot, attempts FROM deletion_requests WHERE operation_key='legacy'")).one()
        assert tuple(old) == (None, None, 0)
    command.downgrade(cfg, "0013_service_orders")
    assert "due_at" not in {c["name"] for c in inspect(engine).get_columns("deletion_requests")}
    command.downgrade(cfg, "0010_structure_snapshots")
    assert "retention_holds" not in inspect(engine).get_table_names()
    with engine.connect() as db:
        assert db.scalar(select(metadata.tables["assets"].c.metadata_json)) == raw_metadata
    engine.dispose()


def test_operations_postgres_migration_enables_rls_without_public_policies(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fixture:fixture@localhost/fixture")
    output = StringIO()
    cfg = Config(str(Path(__file__).resolve().parents[1]/"alembic.ini"), output_buffer=output)
    command.upgrade(cfg, "0010_structure_snapshots:0012_storage_lifecycle", sql=True)
    sql = output.getvalue()
    for table in ("retention_accounts", "retention_holds", "deletion_requests", "retention_notices", "support_access_sessions", "storage_maintenance_gate", "storage_backup_runs", "storage_backup_pins", "storage_write_intents", "storage_gc_candidates"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY" not in sql
