from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import multiprocessing
import os
import time

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from services.api.config import Settings
from services.api.database import build_database, utcnow
from services.api.feature_models import AuditEvent
from services.api.models import Asset, Job, User
from services.api.retention.models import (RetentionHold, RetentionNotice, SupportSession, StorageIntent,
    GcCandidate, BackupRun)
from services.api.retention.service import refresh_retention
from services.api.retention.storage_lifecycle import (record_write_intent, mark_published, process_known_orphans,
    begin_backup, add_backup_pins, finish_backup)
from services.api.storage import LocalStorage
from services.api.tests.test_api import app, client, register, project, image_file


@pytest.fixture
def ops(app, client):
    owner = register(client)
    item = project(client)
    app.state.settings.admin_emails = ("support@example.com",)
    with TestClient(app) as admin:
        register(admin, "support@example.com")
        yield app, client, admin, owner, item


def body(owner, item, **extra):
    return {"tenant_id": owner["tenant"]["id"], "target_kind": "project", "target_id": item["id"], "reason": "고객 요청 자료 확인", **extra}


def deletion(client, item, key="delete-one"):
    response = client.post("/v1/deletion-requests", headers={"Idempotency-Key": key},
        json={"target_kind": "project", "target_id": item["id"], "reason": "기존 디자인 삭제 검토 요청"})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_request_is_idempotent_review_only_and_never_erases_source(ops):
    app, owner, admin, auth, item = ops
    request = deletion(owner, item)
    assert request["execution_supported"] is False
    assert "SCOPE_REQUIRES_MANUAL_ERASURE_PLAN" in request["blockers"]
    assert deletion(owner, item)["id"] == request["id"]
    mismatch = owner.post("/v1/deletion-requests", headers={"Idempotency-Key": "delete-one"},
        json={"target_kind": "project", "target_id": item["id"], "reason": "본문이 변경된 별개의 요청"})
    assert mismatch.status_code == 409
    reviewed = admin.post(f"/v1/admin/deletion-requests/{request['id']}/review",
        json={"revision": 1, "decision": "approved", "reason": "소유자 요청 대상과 사유 확인"})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["data"]["status"] == "approved"
    assert reviewed.json()["data"]["execution_supported"] is False
    assert owner.get(f"/v1/projects/{item['id']}").json()["data"]["scene"] == item["scene"]
    canceled = owner.post(f"/v1/deletion-requests/{request['id']}/cancel", json={"revision": 2, "reason": "삭제 검토 요청을 철회합니다"})
    assert canceled.status_code == 200
    assert owner.post(f"/v1/deletion-requests/{request['id']}/cancel", json={"revision": 2, "reason": "동일 요청 취소를 재전송합니다"}).status_code == 200
    with app.state.session_factory() as db:
        holds = list(db.scalars(select(RetentionHold).where(RetentionHold.source_id == request["id"])))
        assert len(holds) == 1 and holds[0].released_at is not None
        assert len(list(db.scalars(select(AuditEvent).where(AuditEvent.entity_id == request["id"])))) == 3


def test_hold_is_independent_of_request_and_stale_reviews_are_rejected(ops):
    app, owner, admin, auth, item = ops
    hold = admin.post("/v1/admin/retention/holds", json=body(auth, item, reason_code="dispute")).json()["data"]
    request = deletion(owner, item)
    assert "PRESERVATION_HOLD" in request["blockers"]
    assert admin.post(f"/v1/admin/retention/holds/{hold['id']}/release", json={"revision": 2, "reason": "오래된 상태로 잘못된 해제 요청"}).status_code == 409
    owner.post(f"/v1/deletion-requests/{request['id']}/cancel", json={"revision": 1, "reason": "요청 취소 후 분쟁 자료는 보존"})
    assert owner.get("/v1/retention").json()["data"]["holds"][0]["id"] == hold["id"]
    assert admin.post(f"/v1/admin/deletion-requests/{request['id']}/review", json={"revision": 1, "decision": "approved", "reason": "취소 이전 상태의 요청 검토"}).status_code == 409


def test_owner_tenant_role_and_csrf_checks_precede_operations(ops):
    app, owner, admin, auth, item = ops
    assert owner.post("/v1/deletion-requests", headers={"X-CSRF-Token": "wrong"}, json={"target_kind": "project", "target_id": item["id"], "reason": "요청 사유를 충분히 입력"}).status_code == 403
    assert owner.post("/v1/admin/support-sessions", json=body(auth, item)).status_code == 403
    with TestClient(app) as other:
        register(other, "unrelated@example.com")
        denied = other.post("/v1/deletion-requests", headers={"Idempotency-Key": "cross-tenant"}, json={"target_kind": "project", "target_id": item["id"], "reason": "다른 조직 자료 삭제 요청"})
        assert denied.status_code == 404
    with app.state.session_factory() as db:
        db.get(User, auth["user"]["id"]).role = "viewer"; db.commit()
    assert owner.get("/v1/retention").status_code == 403
    assert owner.get("/v1/deletion-requests").status_code == 403


def test_notices_are_deduplicated_extend_only_and_no_trial_expiry_is_invented(ops):
    from services.api.billing.models import CreditBucket
    app, owner, _, auth, _ = ops
    assert owner.get("/v1/retention").json()["data"]["state"] == "unspecified"
    now = utcnow()
    with app.state.session_factory() as db:
        bucket = db.scalar(select(CreditBucket).where(CreditBucket.tenant_id == auth["tenant"]["id"]))
        bucket.kind, bucket.expires_at = "purchase", now + timedelta(days=6)
        account, state = refresh_retention(db, auth["tenant"]["id"], now=now); db.commit()
        assert state == "protected"
        refresh_retention(db, auth["tenant"]["id"], now=now); db.commit()
        assert len(list(db.scalars(select(RetentionNotice)))) == 1
        bucket.expires_at = now + timedelta(days=80)
        refresh_retention(db, auth["tenant"]["id"], now=now); db.commit()
        assert db.scalar(select(RetentionNotice)).status == "superseded"
        bucket.expires_at = now - timedelta(days=2)
        account, state = refresh_retention(db, auth["tenant"]["id"], now=now)
        assert account.protected_until.replace(tzinfo=now.tzinfo) == now + timedelta(days=80)
        db.commit()
    notices = owner.get("/v1/notices").json()["data"]["items"]
    assert notices[0]["channel"] == "in_app"


def test_support_requires_reason_actor_session_scope_and_immutable_audit(ops):
    app, owner, admin, auth, item = ops
    assert admin.post("/v1/admin/support-sessions", json=body(auth, item, reason="")).status_code == 422
    grant = admin.post("/v1/admin/support-sessions", json=body(auth, item)).json()["data"]
    path = f"/v1/admin/support-sessions/{grant['id']}/projects/{item['id']}"
    response = admin.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["scene"] == item["scene"]
    assert admin.get(f"/v1/projects/{item['id']}").status_code == 404
    other_item = project(owner)
    assert admin.get(f"/v1/admin/support-sessions/{grant['id']}/projects/{other_item['id']}").status_code == 403
    with TestClient(app) as new_session:
        register(new_session, "support@example.com")
        assert new_session.get(path).status_code == 403
    with app.state.session_factory() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "support_project_read"))
        assert event.details["reason"] == "고객 요청 자료 확인"
        assert event.details["target_id"] == item["id"]
    revoked = admin.post(f"/v1/admin/support-sessions/{grant['id']}/revoke", json={"reason": "자료 확인이 끝나 접근 종료"})
    assert revoked.status_code == 200
    assert admin.get(path).status_code == 403


def test_support_asset_requires_exact_scope_and_short_ttl(ops, monkeypatch):
    from services.api.retention import routes
    app, owner, admin, auth, item = ops
    asset = owner.post("/v1/assets", files={"file": image_file()}).json()["data"]
    grant = admin.post("/v1/admin/support-sessions", json=body(auth, item, target_kind="asset", target_id=asset["id"])).json()["data"]
    calls = []
    monkeypatch.setattr(routes, "SupabaseStorage", LocalStorage)
    monkeypatch.setattr(app.state.storage, "signed_url", lambda key, **kw: calls.append(kw) or "https://private.example/test-object", raising=False)
    with app.state.session_factory() as db:
        db.get(SupportSession, grant["id"]).expires_at = utcnow()+timedelta(seconds=3); db.commit()
    response = admin.get(f"/v1/admin/support-sessions/{grant['id']}/assets/{asset['id']}/content", follow_redirects=False)
    assert response.status_code == 307
    assert 1 <= calls[0]["ttl"] <= 3
    assert "storage_key" not in admin.get("/v1/admin/retention/overview").text
    with app.state.session_factory() as db:
        db.get(SupportSession, grant["id"]).expires_at = utcnow()-timedelta(seconds=1); db.commit()
    assert admin.get(f"/v1/admin/support-sessions/{grant['id']}/assets/{asset['id']}/content").status_code == 403


def orphan(app, client, item):
    created = client.post("/v1/exports", json={"project_id": item["id"], "base_revision": item["base_revision"]}).json()["data"]
    lease = str(uuid4())
    with app.state.session_factory() as db:
        job = db.get(Job, created["id"]); job.status, job.lease_id = "running", lease
        tenant = job.tenant_id; db.commit()
    key = f"{tenant}/exports/{created['id']}/{lease}.pdf"; content = b"%PDF-test-private-orphan"
    identity = record_write_intent(app.state.session_factory, tenant, created["id"], lease, key, content)
    app.state.storage.put(key, content, "application/pdf")
    with app.state.session_factory() as db:
        db.get(Job, created["id"]).status = "failed"
        db.get(StorageIntent, identity).created_at = utcnow()-timedelta(days=2)
        db.commit()
    return identity, key, created["id"], content


def test_known_gc_default_off_two_observations_then_delete_only_registered_key(ops):
    app, owner, _, _, item = ops
    identity, key, _, content = orphan(app, owner, item)
    unknown = key.replace(".pdf", "-unknown.pdf")
    app.state.storage.put(unknown, b"unknown original", "application/pdf")
    now = utcnow()
    assert process_known_orphans(app.state.session_factory, app.state.storage, app.state.settings, now=now)["deleted"] == 0
    assert process_known_orphans(app.state.session_factory, app.state.storage, app.state.settings, now=now+timedelta(minutes=6))["deleted"] == 0
    assert app.state.storage.get(key) == content
    app.state.settings.storage_gc_delete_enabled = True
    assert process_known_orphans(app.state.session_factory, app.state.storage, app.state.settings, now=now+timedelta(minutes=7))["deleted"] == 1
    assert not app.state.storage.path(key).exists()
    assert app.state.storage.get(unknown) == b"unknown original"
    assert process_known_orphans(app.state.session_factory, app.state.storage, app.state.settings, now=now+timedelta(minutes=8))["deleted"] == 0
    with app.state.session_factory() as db:
        assert db.get(StorageIntent, identity).status == "deleted"
        assert len(list(db.scalars(select(AuditEvent).where(AuditEvent.action == "known_orphan_deleted")))) == 1


@pytest.mark.parametrize("protection", ["hold", "backup", "queued", "reconciliation", "asset", "snapshot", "published", "changed_bytes", "font"])
def test_gc_preserves_protected_or_changed_objects(ops, protection):
    app, owner, admin, auth, item = ops
    identity, key, job_id, content = orphan(app, owner, item)
    now = utcnow(); app.state.settings.storage_gc_delete_enabled = True
    process_known_orphans(app.state.session_factory, app.state.storage, app.state.settings, now=now)
    if protection == "hold":
        assert admin.post("/v1/admin/retention/holds", json=body(auth, item, reason_code="dispute")).status_code == 201
    elif protection == "backup":
        run = begin_backup(app.state.session_factory, "동시 백업 원본 보존 확인")
        add_backup_pins(app.state.session_factory, run, [key])
    elif protection == "changed_bytes":
        app.state.storage.put(key, content+b"changed", "application/pdf")
    elif protection == "font":
        from services.api.font_assets.models import FontUploadSession
        from services.api.database import Base
        Base.metadata.create_all(app.state.engine)
        with app.state.session_factory() as db:
            db.add(FontUploadSession(tenant_id=auth["tenant"]["id"],user_id=auth["user"]["id"],
                storage_key=key,name="held-font.ttf",byte_size=len(content),declaration={},expires_at=utcnow()+timedelta(days=1)))
            db.commit()
    else:
        with app.state.session_factory() as db:
            job = db.get(Job, job_id)
            if protection in {"queued", "reconciliation"}: job.status = "queued" if protection == "queued" else "reconciliation_required"
            if protection == "snapshot": job.snapshot = {**job.snapshot, "preserved_source": key}
            if protection == "published": mark_published(db, key)
            if protection == "asset": db.add(Asset(tenant_id=auth["tenant"]["id"],storage_key=key,original_name="original.png",content_type="image/png",byte_size=len(content),width_px=1,height_px=1))
            db.commit()
    assert process_known_orphans(app.state.session_factory, app.state.storage, app.state.settings, now=now+timedelta(minutes=6))["deleted"] == 0
    assert app.state.storage.path(key).exists()


def test_failed_backup_does_not_expire_and_admin_reason_is_required_to_release(ops):
    app, _, admin, _, _ = ops
    run = begin_backup(app.state.session_factory, "실패한 백업의 보호 상태 테스트")
    assert admin.post(f"/v1/admin/retention/backups/{run}/abandon", json={"reason": "아직 실행 중인 백업을 종료"}).status_code == 409
    finish_backup(app.state.session_factory, run, verified=False)
    assert admin.post(f"/v1/admin/retention/backups/{run}/abandon", json={"reason": ""}).status_code == 422
    result = admin.post(f"/v1/admin/retention/backups/{run}/abandon", json={"reason": "백업 프로세스 중단과 별도 재실행 확인"})
    assert result.status_code == 200 and result.json()["data"]["state"] == "abandoned"


def _gc_process(database_url, directory, at, entered, release, crash=False):
    # Fresh process/SQL connection: this test does not reuse a Python DB lock.
    settings = Settings(environment="test", database_url=database_url, storage_dir=Path(directory), storage_gc_delete_enabled=True)
    engine, sessions = build_database(settings)
    class SlowStorage(LocalStorage):
        def delete(self, key):
            entered.set()
            if not release.wait(10): raise RuntimeError("test release timeout")
            super().delete(key)
            if crash: os._exit(17)
    process_known_orphans(sessions, SlowStorage(Path(directory)), settings, now=at)
    engine.dispose()


def test_fresh_process_gc_serializes_backup_start_and_commit_loss_reconciles(ops):
    app, owner, _, _, item = ops
    _, key, _, _ = orphan(app, owner, item)
    now = utcnow(); settings = app.state.settings
    process_known_orphans(app.state.session_factory, app.state.storage, settings, now=now)
    context = multiprocessing.get_context("spawn"); entered, release = context.Event(), context.Event()
    child = context.Process(target=_gc_process, args=(settings.database_url, str(settings.storage_dir), now+timedelta(minutes=6), entered, release, True))
    child.start()
    try:
        assert entered.wait(60)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(begin_backup, app.state.session_factory, "GC와 동시에 시작한 독립 백업")
            time.sleep(.15)
            assert not future.done()
            release.set(); child.join(15)
            assert child.exitcode == 17
            backup_id = future.result(timeout=10)
        assert not app.state.storage.path(key).exists()
        # The crash rolled back the deletion marker, but the new backup's pin
        # blocks retry until explicitly resolved; no guessed successful commit.
        settings.storage_gc_delete_enabled = True
        assert process_known_orphans(app.state.session_factory, app.state.storage, settings, now=now+timedelta(minutes=7))["deleted"] == 0
        finish_backup(app.state.session_factory, backup_id, verified=True, manifest_hash=sha256(b"verified fixture").hexdigest())
        process_known_orphans(app.state.session_factory, app.state.storage, settings, now=now+timedelta(minutes=13))
        with app.state.session_factory() as db:
            assert db.scalar(select(StorageIntent)).status == "deleted"
    finally:
        release.set()
        if child.is_alive(): child.terminate()
        child.join(5)


def test_customer_deletion_is_default_off_and_hosted_requires_policy():
    assert Settings().retention_customer_delete_enabled is False
    Settings(environment="test", retention_customer_delete_enabled=True).validate()
