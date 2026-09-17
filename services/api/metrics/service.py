from copy import deepcopy
from hashlib import sha256
from sqlalchemy import select
from ..billing.policy import aware
from ..database import utcnow
from ..errors import APIError
from ..models import Job, Project, Revision, User
from .models import MetricEvent, Acquisition

EVENT_NAMES = frozenset({"signup_completed", "trial_granted", "project_created", "brief_completed",
    "generation_succeeded", "generation_failed", "text_edited", "all_faces_reviewed", "preflight_failed",
    "production_export_succeeded", "credits_insufficient", "topup_paid", "subscription_paid", "renewal_paid",
    "printer_accepted", "printer_rejected"})
PROPERTY_KEYS = {"credits", "amount", "currency", "provider", "plan_id", "unit_index", "error_code",
    "changed_object_count", "review_kind", "failure_count", "record_source", "rejection_kind", "payment_id",
    "order_kind", "measurement_basis", "required", "available", "accepted", "pricing_version"}


def policy_version(db):
    from ..billing.policy import pricing
    return pricing(db)["version"]


def record_event(db, name, *, key, tenant_id, project_id=None, job_id=None, revision_id=None,
                 properties=None, occurred_at=None, policy=None):
    """Trusted server hook; never commits and never accepts a free-form event body."""
    if name not in EVENT_NAMES or set(properties or {}) - PROPERTY_KEYS:
        raise ValueError("Unreviewed metric name/property")
    event_key = f"{name}:{key}"
    if len(event_key) > 200: raise ValueError("Metric key too long")
    existing = db.scalar(select(MetricEvent).where(MetricEvent.event_key == event_key))
    if existing:
        if existing.tenant_id != tenant_id: raise APIError(409, "METRIC_IDENTITY_CONFLICT", "기록 연결을 확인할 수 없습니다.")
        return existing
    for model, identity in ((Project,project_id),(Job,job_id),(Revision,revision_id)):
        if identity:
            row=db.get(model,identity)
            if row is None or row.tenant_id != tenant_id: raise ValueError("Metric tenant mismatch")
            if project_id and isinstance(row,(Job,Revision)) and row.project_id != project_id: raise ValueError("Metric project mismatch")
    # A dialect upsert stays inside the caller transaction, including SQLite
    # legacy transaction mode where a first SAVEPOINT could otherwise commit.
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert=sqlite_insert if db.get_bind().dialect.name=="sqlite" else pg_insert
    db.execute(insert(MetricEvent).values(event_key=event_key,name=name,tenant_id=tenant_id,project_id=project_id,job_id=job_id,
        revision_id=revision_id,properties=deepcopy(properties or {}),policy_version=policy or policy_version(db),
        occurred_at=aware(occurred_at or utcnow())).on_conflict_do_nothing(index_elements=[MetricEvent.event_key]))
    row=db.scalar(select(MetricEvent).where(MetricEvent.event_key==event_key))
    if row is None or row.tenant_id != tenant_id: raise APIError(409,"METRIC_IDENTITY_CONFLICT","기록 연결을 확인할 수 없습니다.")
    return row


def record_signup(db, user, acquisition=None):
    values=acquisition.model_dump() if acquisition is not None else {"channel":"direct"}
    if values.get("utm_campaign"):
        values["utm_campaign"] = sha256(values["utm_campaign"].encode()).hexdigest()
    if db.get(Acquisition,user.tenant_id) is None:
        db.add(Acquisition(tenant_id=user.tenant_id,user_id=user.id,**values,created_at=user.created_at))
    record_event(db,"signup_completed",key=user.id,tenant_id=user.tenant_id,occurred_at=user.created_at)


def record_trial_granted(db, tenant_id):
    from ..billing.models import CreditBucket
    bucket=db.scalar(select(CreditBucket).where(CreditBucket.tenant_id==tenant_id,CreditBucket.grant_key=="signup-trial"))
    if bucket:
        record_event(db,"trial_granted",key=bucket.id,tenant_id=tenant_id,
            properties={"credits":bucket.granted},occurred_at=bucket.created_at)


def record_payment_paid(db, order, payment):
    if order.status!="paid" or payment.order_id!=order.id or payment.tenant_id!=order.tenant_id:
        raise ValueError("Payment metric requires a confirmed matching paid order")
    return record_recovered_payment(db,order,payment)


def record_recovered_payment(db, order, payment):
    """A verified cancellation can be the first sight of a prior approval.

    Records historical approval only. Does not grant credits or activate a plan.
    The payment adapter already checks amount/order/key and approvedAt.
    """
    name={"subscription":"subscription_paid","renewal":"renewal_paid","topup":"topup_paid"}.get(order.kind)
    if name is None:return
    if payment.status not in {"DONE","CANCELED","PARTIAL_CANCELED"} or payment.order_id!=order.id or payment.tenant_id!=order.tenant_id or not payment.approved_at:
        raise ValueError("A verified matching historical payment approval is required")
    record_event(db,name,key=payment.id,tenant_id=order.tenant_id,policy=order.pricing_version,
        occurred_at=payment.approved_at,properties={"amount":payment.amount,"currency":payment.currency,
        "provider":payment.provider,"plan_id":order.plan_id,"payment_id":payment.id,"order_kind":order.kind})


def record_project_created(db, project):
    record_event(db,"project_created",key=project.id,tenant_id=project.tenant_id,project_id=project.id,occurred_at=project.created_at)
    if project.product_name.strip() and project.brand_name.strip():
        record_event(db,"brief_completed",key=project.id,tenant_id=project.tenant_id,project_id=project.id,
            properties={"measurement_basis":"required_project_fields"})


def record_revision(db, project, revision, before_scene):
    old={(face['id'],obj["id"]):obj.get("text") for face in (before_scene or {}).get("faces",[]) for obj in face.get("objects",[]) if obj.get("type")=="text"}
    new={(face['id'],obj["id"]):obj.get("text") for face in project.scene.get("faces",[]) for obj in face.get("objects",[]) if obj.get("type")=="text"}
    count=sum(old.get(key)!=new.get(key) for key in old.keys()|new.keys())
    if count: record_event(db,"text_edited",key=revision.id,tenant_id=project.tenant_id,project_id=project.id,
        revision_id=revision.id,properties={"changed_object_count":count},occurred_at=revision.created_at)
    if project.product_name.strip() and project.brand_name.strip():
        record_event(db,"brief_completed",key=project.id,tenant_id=project.tenant_id,project_id=project.id,
            revision_id=revision.id,properties={"measurement_basis":"required_project_fields"})


def record_preflight(db, project, revision, report, reviewed_ids, *, kind):
    expected={f["id"] for f in project.scene.get("faces",[])}
    if expected and expected==set(reviewed_ids):
        record_event(db,"all_faces_reviewed",key=revision.id,tenant_id=project.tenant_id,project_id=project.id,revision_id=revision.id)
    issues=[row for row in report.get("issues",[]) if kind=="production" or row.get("scope")=="review"]
    errors=[row for row in issues if row.get("severity")=="error"]
    if errors: record_event(db,"preflight_failed",key=f"{revision.id}:{kind}:{len(errors)}",tenant_id=project.tenant_id,
        project_id=project.id,revision_id=revision.id,properties={"review_kind":kind,"failure_count":len(errors)})


def record_generation(db, job, unit, *, succeeded, error_code=None, attempt_id=None):
    record_event(db,"generation_succeeded" if succeeded else "generation_failed",
        key=unit.id if succeeded else f"{unit.id}:{attempt_id or unit.attempt_count}",
        tenant_id=job.tenant_id,project_id=job.project_id,job_id=job.id,revision_id=job.revision_id,
        policy=job.snapshot.get("pricing_version") or "legacy-unknown",
        properties={"unit_index":unit.unit_index,"provider":job.snapshot.get("provider_mode","unknown"),
            **({"error_code":error_code} if error_code else {})})


def record_production_success(db, job):
    if job.kind!="production_export" or job.status!="succeeded" or (job.result or {}).get("review_only") is not False:
        raise ValueError("Normal production output required")
    record_event(db,"production_export_succeeded",key=job.id,tenant_id=job.tenant_id,project_id=job.project_id,
        job_id=job.id,revision_id=job.revision_id,policy=job.snapshot.get("pricing_version") or "legacy-unknown",
        occurred_at=job.updated_at)


def record_intake(db, row, metadata, job):
    if row.status not in {"accepted","rejected"}: return
    record_event(db,"printer_accepted" if row.status=="accepted" else "printer_rejected",key=row.id,
        tenant_id=row.tenant_id,project_id=row.project_id,job_id=job.id if job else None,
        revision_id=job.revision_id if job else None,policy=metadata.get("pricing_policy_version") or "legacy-unknown",
        occurred_at=row.created_at,properties={"record_source":metadata.get("record_source","unknown"),
        "rejection_kind":metadata.get("rejection_kind"),"measurement_basis":"self_reported_manufacturer_reply"})


def record_credit_failure(session_factory, tenant_id, request_id, fields):
    """Business transaction has rolled back; preserve only the trusted 402 outcome."""
    import logging
    try:
        with session_factory() as db:
            values={key:value for key,value in fields.items() if key in {"required","available"} and type(value) is int and value>=0}
            record_event(db,"credits_insufficient",key=request_id,tenant_id=tenant_id,properties=values)
            db.commit()
    except Exception as exc:
        # An optional measurement must never replace a real payment error or log content.
        logging.getLogger(__name__).warning("Credit metric unavailable: %s", type(exc).__name__)
