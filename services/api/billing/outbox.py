"""Transactional delivery into the durable DB queue and internal audit notices.

The SQL job tables are the queue used by both cron and local/Celery workers.
Acknowledging a reservation never creates a second job or calls a provider.
No external email or webhook is sent by this consumer.
"""
from sqlalchemy import select

from ..database import utcnow
from ..feature_models import AuditEvent
from ..models import Job
from .models import BillingOutbox, Reservation
from .service import lock_wallet


def deliver_outbox(session_factory, *, now=None, limit=100):
    now = now or utcnow()
    with session_factory() as db:
        supported = ("credits.reserved", "payment.paid", "payment.reconciliation_required", "payment.external_cancellation")
        candidates = list(db.execute(select(BillingOutbox.id, BillingOutbox.tenant_id).where(BillingOutbox.delivered_at.is_(None), BillingOutbox.kind.in_(supported)).order_by(BillingOutbox.created_at, BillingOutbox.id).limit(max(1, min(limit, 500)))))
    delivered = 0
    for identity, tenant_id in candidates:
        with session_factory() as db:
            lock_wallet(db, tenant_id, now)
            row = db.scalar(select(BillingOutbox).where(BillingOutbox.id == identity).with_for_update())
            if row.delivered_at is not None:
                continue
            if row.kind == "credits.reserved":
                reservation = db.scalar(select(Reservation).where(Reservation.id == row.payload.get("reservation_id"), Reservation.tenant_id == tenant_id))
                if reservation is None or not reservation.job_id:
                    continue
                job = db.scalar(select(Job).where(Job.id == reservation.job_id, Job.tenant_id == tenant_id))
                if job is None:
                    continue
                details = {"reservation_id": reservation.id, "job_id": job.id, "action": reservation.action}
            elif row.kind in {"payment.paid", "payment.reconciliation_required", "payment.external_cancellation"}:
                details = {key: row.payload[key] for key in ("order_id", "credits") if key in row.payload}
            else:
                continue  # Unknown kinds need a reviewed handler, never silent ACK.
            db.add(AuditEvent(tenant_id=tenant_id, action=row.kind, entity_id=row.id, details=details, created_at=now))
            row.delivered_at = now
            db.commit()
            delivered += 1
    return delivered
