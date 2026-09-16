from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select, text
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
    with factory.begin() as db:
        tenant = Tenant(name="원장 검수", created_at=now)
        db.add(tenant)
        db.flush()
        ensure_trial(db, tenant.id, now=now)
    for statement in ["UPDATE credit_ledger SET amount=999", "DELETE FROM credit_ledger"]:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(statement))
    with engine.connect() as connection:
        assert connection.execute(text("SELECT amount FROM credit_ledger")).scalar_one() == 30
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0004_billing"
    engine.dispose()
