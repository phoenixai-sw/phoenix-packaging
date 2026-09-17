from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from services.api.database import utcnow
from services.api.errors import APIError
from services.api.models import Tenant
from services.api.billing.models import CreditBucket
from services.api.billing.payments import MockProvider
from services.api.billing.routes import install_billing_routes
from services.api.billing.service import ensure_trial


def test_hosted_mock_confirmation_route_is_not_exposed(billing_db, payment_settings):
    factory, tenant, now = billing_db
    app = FastAPI()
    app.state.settings = SimpleNamespace(environment="staging")
    @app.middleware("http")
    async def context(request, call_next):
        request.state.request_id = str(uuid4())
        return await call_next(request)
    @app.exception_handler(APIError)
    async def errors(request, error):
        return JSONResponse(status_code=error.status, content={"code": error.code})
    def db_session():
        with factory() as db:
            yield db
    payment_settings.environment = "staging"
    install_billing_routes(app, db_session, settings=payment_settings, provider=MockProvider())
    with TestClient(app) as client:
        denied = client.post("/v1/billing/mock-confirm", json={"order_id": "order_123"})
        assert denied.status_code == 404
    with factory() as db:
        assert list(db.scalars(select(CreditBucket))) == []


def test_financial_history_is_append_only_in_real_migrated_database(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[4]
    path = tmp_path / "migration.db"
    url = "sqlite:///" + str(path)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("APP_ENV", "test")
    cfg = Config(str(root / "services/api/alembic.ini"))
    command.upgrade(cfg, "0004_billing")
    engine = create_engine(url)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = utcnow()
    try:
        # The historical migration test must not call today's policy-aware
        # service against a schema which predates policy tables. Insert valid
        # history through the actual 0004 columns, without creating new tables.
        metadata = MetaData()
        tables = {name: Table(name, metadata, autoload_with=engine) for name in
                  ('tenants', 'credit_wallets', 'credit_buckets', 'credit_ledger')}
        tenant_id, bucket_id, entry_id = (str(uuid4()) for _ in range(3))
        with engine.begin() as connection:
            connection.execute(tables['tenants'].insert().values(id=tenant_id, name='Historical ledger fixture', created_at=now))
            connection.execute(tables['credit_wallets'].insert().values(tenant_id=tenant_id, lock_version=0, ever_paid=False, created_at=now))
            connection.execute(tables['credit_buckets'].insert().values(id=bucket_id, tenant_id=tenant_id,
                kind='trial', scope='standard_only', grant_key='signup-trial', granted=30, available=30,
                reserved=0, consumed=0, expired=0, expires_at=now+timedelta(days=14), created_at=now))
            connection.execute(tables['credit_ledger'].insert().values(id=entry_id, tenant_id=tenant_id,
                bucket_id=bucket_id, event_key='historical-trial', event='GRANT', amount=30,
                reason='Historical trial fixture', created_at=now))
            original = dict(connection.execute(select(tables['credit_ledger'])).mappings().one())

        def check_immutable(revision, expected_rows):
            for statement in ['UPDATE credit_ledger SET amount=999', 'DELETE FROM credit_ledger']:
                with pytest.raises(IntegrityError):
                    with engine.begin() as connection:
                        connection.execute(text(statement))
            with engine.connect() as connection:
                assert list(connection.scalars(text('SELECT amount FROM credit_ledger'))) == [30]*expected_rows
                assert connection.scalar(text('SELECT version_num FROM alembic_version')) == revision
                assert dict(connection.execute(select(tables['credit_ledger']).where(tables['credit_ledger'].c.id==entry_id)).mappings().one()) == original
                assert not list(connection.exec_driver_sql('PRAGMA foreign_key_check'))

        check_immutable('0004_billing', 1)
        command.upgrade(cfg, 'head')
        # Also exercise the real current service on fully migrated tables and
        # verify that both historical and newly appended entries stay protected.
        with factory.begin() as db:
            tenant = Tenant(name='Current ledger fixture', created_at=now)
            db.add(tenant); db.flush()
            ensure_trial(db, tenant.id, now=now)
            ensure_trial(db, tenant.id, now=now)
        check_immutable(ScriptDirectory.from_config(cfg).get_current_head(), 2)
    finally:
        engine.dispose()
