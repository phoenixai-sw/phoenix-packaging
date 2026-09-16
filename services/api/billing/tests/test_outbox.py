from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import func, select

from services.api.billing.models import BillingOutbox
from services.api.billing.outbox import deliver_outbox
from services.api.feature_models import AuditEvent


def test_outbox_concurrent_delivery_creates_one_notice_and_unknown_is_not_acked(billing_db):
    factory,tenant,now=billing_db
    with factory.begin() as db:
        db.add(BillingOutbox(tenant_id=tenant,event_key="paid:1",kind="payment.paid",payload={"order_id":"order_1","credits":500,"secret":"never copied"},created_at=now))
        db.add(BillingOutbox(tenant_id=tenant,event_key="future:1",kind="unknown",payload={},created_at=now))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:deliver_outbox(factory,now=now),range(2)))
    assert sum(results)==1
    with factory.begin() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent))==1
        assert db.scalar(select(AuditEvent)).details=={"order_id":"order_1","credits":500}
        assert db.scalar(select(BillingOutbox).where(BillingOutbox.kind=="unknown")).delivered_at is None


def test_reservation_outbox_waits_for_actual_durable_job(billing_db):
    from services.api.billing.service import reserve
    factory,tenant,now=billing_db
    with factory.begin() as db:reserve(db,tenant,"unit-test-reservation","image.generate.standard",1,now=now)
    assert deliver_outbox(factory,now=now)==0
    with factory.begin() as db:assert db.scalar(select(BillingOutbox)).delivered_at is None
