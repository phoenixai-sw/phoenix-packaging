from services.api.tests.test_api import app,client
from services.api import worker_service


def test_worker_endpoint_reports_partial_dispatch_failure_without_error_details(client,app,monkeypatch):
    calls=[]
    def fail(*args,**kwargs):raise RuntimeError("secret_provider_response")
    def complete(*args,**kwargs):calls.append("production");return 0
    monkeypatch.setattr(worker_service,"process_pending_jobs",fail)
    monkeypatch.setattr(worker_service,"process_production_jobs",complete)
    result=client.post("/v1/internal/jobs/process",headers={"Authorization":"Bearer test-worker-secret"})
    assert result.status_code==503
    assert result.json()["code"]=="WORKER_RETRY_PENDING" and result.json()["retryable"] is True
    assert result.json()["field_errors"]["breakdown"]["review"]=="retry_pending"
    assert "secret_provider_response" not in result.text and calls==["production"]


def test_worker_endpoint_success_and_authentication(client):
    assert client.get("/v1/internal/jobs/process").status_code==404
    result=client.get("/v1/internal/jobs/process",headers={"Authorization":"Bearer test-worker-secret"})
    assert result.status_code==200 and result.json()["data"]["processed"]==0


def test_reservation_outbox_acknowledges_real_job_once(client,app):
    from sqlalchemy import select
    from services.api.tests.test_api import register,project
    from services.api.billing.outbox import deliver_outbox
    from services.api.billing.models import BillingOutbox
    from services.api.feature_models import AuditEvent
    register(client);item=project(client)
    quote=client.post("/v1/quotes",json={"project_id":item["id"],"base_revision":1,"action":"image.generate.standard","requested_units":1,"prompt":"soft green rice illustration"}).json()["data"]
    response=client.post("/v1/jobs",headers={"Idempotency-Key":"outbox-job"},json={"quote_id":quote["id"]})
    assert response.status_code==202,response.text
    assert deliver_outbox(app.state.session_factory)==1
    assert deliver_outbox(app.state.session_factory)==0
    with app.state.session_factory() as db:
        assert db.scalar(select(BillingOutbox)).delivered_at is not None
        notice=db.scalar(select(AuditEvent).where(AuditEvent.action=="credits.reserved"))
        assert notice.details["job_id"]==response.json()["data"]["id"]
