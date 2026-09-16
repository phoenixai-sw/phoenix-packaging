from datetime import datetime, timezone

from cryptography.fernet import Fernet
import pytest

from services.api.config import Settings
from services.api.database import Base, build_database
from services.api.models import Tenant
from services.api.billing import models
from services.api.billing.payments import BillingSettings, MockProvider

NOW = datetime(2024, 1, 31, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def billing_db(tmp_path):
    engine, factory = build_database(Settings(environment="test", database_url=f"sqlite:///{tmp_path / 'billing.db'}", storage_dir=tmp_path / "storage"))
    Base.metadata.create_all(engine)
    with factory.begin() as db:
        tenant = Tenant(name="과금 검수", created_at=NOW)
        db.add(tenant)
        db.flush()
        tenant_id = tenant.id
    yield factory, tenant_id, NOW
    engine.dispose()


@pytest.fixture
def payment_settings():
    return BillingSettings(provider="mock", environment="test", encryption_key=Fernet.generate_key().decode())


@pytest.fixture
def provider():
    return MockProvider()
