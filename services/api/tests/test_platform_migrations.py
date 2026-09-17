"""A populated legacy account must not become an admin during schema upgrade."""
from pathlib import Path
from alembic.config import Config
from alembic import command
from sqlalchemy import create_engine,text,select
from sqlalchemy.orm import Session
from services.api.models import User


def test_populated_legacy_upgrade_preserves_safe_permissions(tmp_path,monkeypatch):
    url=f"sqlite:///{tmp_path/'legacy.db'}"
    monkeypatch.setenv("DATABASE_URL",url)
    cfg=Config(str(Path(__file__).resolve().parents[1]/"alembic.ini"))
    command.upgrade(cfg,"0003_job_leases")
    engine=create_engine(url)
    with engine.begin() as db:
        db.execute(text("INSERT INTO tenants(id,name,created_at) VALUES('tenant','Legacy','2026-09-16')"))
        db.execute(text("INSERT INTO users(id,tenant_id,name,email,password_hash,role,created_at) VALUES('user','tenant','Owner','legacy@example.com','hash','owner','2026-09-16')"))
    command.upgrade(cfg,"head")
    with Session(engine) as db:
        user=db.get(User,"user")
        assert user.is_admin is False
        assert user.is_active is True
        assert user.email=="legacy@example.com"
        assert user.password_hash==""
        assert user.google_sub is None
        assert user.google_email_authoritative is False
    with engine.connect() as db:
        assert not list(db.exec_driver_sql("PRAGMA foreign_key_check"))
    engine.dispose()
