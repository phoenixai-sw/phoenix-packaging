"""Deletion tests operate exclusively on isolated local fixture objects."""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select

from services.api.database import utcnow
from services.api.models import Asset, Job, Project, Revision
from services.api.feature_models import AuditEvent, Brand
from services.api.billing.models import LedgerEntry
from services.api.retention.models import DeletionRequest, BackupRun
from services.api.retention.deletion import process_deletion_requests
from services.api.retention.storage_lifecycle import begin_backup, finish_backup
from services.api.tests.test_retention_ops import ops, body
from services.api.tests.test_api import app, client, image_file


def prepare(ops, kind="asset"):
    app, owner, admin, auth, item = ops
    if kind == "asset":
        asset = owner.post("/v1/assets", files={"file": image_file()}).json()["data"]
        identity = asset["id"]
        with app.state.session_factory() as db: key = db.get(Asset, identity).storage_key
    else:
        from services.api.jobs import process_pending_jobs
        job = owner.post("/v1/exports", json={"project_id": item["id"], "base_revision": 1}).json()["data"]
        assert process_pending_jobs(app.state.session_factory, app.state.storage) == 1
        identity = job["id"]
        with app.state.session_factory() as db: key = db.get(Job, identity).result["storage_key"]
    response = owner.post("/v1/deletion-requests", headers={"Idempotency-Key": str(uuid4())},
        json={"target_kind": kind, "target_id": identity, "reason": "격리 테스트 자료의 명시적 삭제 요청"})
    assert response.status_code == 201, response.text
    request = response.json()["data"]
    approved = admin.post(f"/v1/admin/deletion-requests/{request['id']}/review", json={"revision": 1, "decision": "approved", "reason": "요청자와 삭제 대상 파일을 확인했습니다"})
    assert approved.status_code == 200, approved.text
    return approved.json()["data"], identity, key


def run(app, request, *, days=1):
    return process_deletion_requests(app.state.session_factory, app.state.storage, app.state.settings,
        now=datetime.fromisoformat(request["due_at"]) + timedelta(days=days))


@pytest.mark.parametrize("kind", ["asset", "export"])
def test_explicit_deletion_grace_default_off_tombstone_and_ledger_preserved(ops, kind):
    app, owner, _, auth, item = ops
    request, identity, key = prepare(ops, kind)
    assert request["execution_supported"] and request["cancelable"]
    assert datetime.fromisoformat(request["due_at"]) >= utcnow()+timedelta(days=6, hours=23)
    app.state.settings.retention_customer_delete_enabled = True
    assert process_deletion_requests(app.state.session_factory, app.state.storage, app.state.settings)["deleted"] == 0
    app.state.settings.retention_customer_delete_enabled = False
    assert run(app, request)["deleted"] == 0 and app.state.storage.path(key).exists()
    with app.state.session_factory() as db:
        ledger = [(r.id, r.event, r.amount) for r in db.scalars(select(LedgerEntry))]
        scene = deepcopy(db.get(Project, item["id"]).scene)
    app.state.settings.retention_customer_delete_enabled = True
    assert run(app, request)["deleted"] == 1
    assert not app.state.storage.path(key).exists()
    path = f"/v1/assets/{identity}/content" if kind == "asset" else f"/v1/exports/{identity}/download"
    assert owner.get(path).status_code == 410
    if kind == "asset":
        assert identity not in {a["id"] for a in owner.get("/v1/assets").json()["data"]["items"]}
    if kind == "export":
        job = owner.get(f"/v1/jobs/{identity}")
        assert job.status_code == 200 and job.json()["data"]["download_url"] is None
        assert "_retention" not in job.text and "storage_key" not in job.text
    assert run(app, request)["deleted"] == 0
    with app.state.session_factory() as db:
        assert [(r.id, r.event, r.amount) for r in db.scalars(select(LedgerEntry))] == ledger
        assert db.get(Project, item["id"]).scene == scene
        assert db.get(DeletionRequest, request["id"]).status == "executed"
        assert len(list(db.scalars(select(AuditEvent).where(AuditEvent.action == "deletion_executed")))) == 1


def test_owner_cancellation_before_due_prevents_execution(ops):
    app, owner, _, _, _ = ops
    request, _, key = prepare(ops)
    cancel = owner.post(f"/v1/deletion-requests/{request['id']}/cancel", json={"revision": 2, "reason": "유예기간에 소유자가 삭제를 취소합니다"})
    assert cancel.status_code == 200
    app.state.settings.retention_customer_delete_enabled = True
    assert run(app, request)["deleted"] == 0 and app.state.storage.path(key).exists()


@pytest.mark.parametrize("reason", ["scene", "revision", "brand", "lineage", "hold", "project_hold", "backup", "incomplete", "changed_bytes"])
def test_refs_holds_backups_running_work_and_changed_bytes_block_actual_delete(ops, reason):
    app, owner, admin, auth, item = ops
    request, identity, key = prepare(ops)
    app.state.settings.retention_customer_delete_enabled = True
    if reason == "hold":
        admin.post("/v1/admin/retention/holds", json=body(auth, item, target_kind="asset", target_id=identity, reason_code="dispute"))
    elif reason == "project_hold":
        admin.post("/v1/admin/retention/holds", json=body(auth, item, reason_code="dispute"))
    elif reason == "backup": begin_backup(app.state.session_factory, "파일 삭제와 병행하는 백업 보호")
    elif reason == "changed_bytes": app.state.storage.put(key, b"changed identity", "image/png")
    else:
        with app.state.session_factory() as db:
            if reason in {"scene", "revision"}:
                row = db.get(Project, item["id"]) if reason == "scene" else db.scalar(select(Revision).where(Revision.project_id == item["id"]))
                row.scene = {**row.scene, "test_preserved_asset": identity}
            elif reason == "brand": db.add(Brand(tenant_id=auth["tenant"]["id"],name="Fixture",logo_asset_id=identity))
            elif reason == "lineage": db.add(Asset(tenant_id=auth["tenant"]["id"], storage_key=auth["tenant"]["id"]+"/assets/derivative", original_name="derived.png", content_type="image/png",byte_size=1,width_px=1,height_px=1,metadata_json={"image_quality":{"root_source_asset_id":identity}}))
            else:
                revision = db.scalar(select(Revision).where(Revision.project_id==item["id"]))
                db.add(Job(tenant_id=auth["tenant"]["id"],project_id=item["id"],revision_id=revision.id,operation_key="pending",request_hash="a"*64,snapshot={},status="reconciliation_required"))
            db.commit()
    assert run(app, request)["deleted"] == 0
    assert app.state.storage.path(key).exists()
    if reason == "changed_bytes":
        # Sealed before I/O and uncertain identity retained, never silently erased.
        assert owner.get(f"/v1/assets/{identity}/content").status_code == 410
        with app.state.session_factory() as db: assert db.get(DeletionRequest,request["id"]).status == "attention_required"


def test_lost_delete_response_preserves_seal_then_reconciles_missing_without_refund(ops, monkeypatch):
    app, owner, admin, _, item = ops
    request, identity, key = prepare(ops)
    app.state.settings.retention_customer_delete_enabled = True
    original = app.state.storage.delete
    def lost_response(path):
        original(path)
        raise TimeoutError("isolated fixture lost response")
    monkeypatch.setattr(app.state.storage, "delete", lost_response)
    assert run(app, request)["deleted"] == 0
    assert owner.get(f"/v1/assets/{identity}/content").status_code == 410
    scene = deepcopy(item["scene"])
    scene["faces"][0]["objects"].append({"id":"late-reference","type":"image","face_id":"front","x_mm":20,"y_mm":20,"width_mm":20,"height_mm":25,"rotation_deg":0,"z_index":1,"asset_id":identity})
    assert owner.patch(f"/v1/projects/{item['id']}/draft",json={"base_revision":1,"scene":scene}).status_code == 410
    latest=owner.get(f"/v1/deletion-requests/{request['id']}").json()["data"]
    assert not latest["cancelable"]
    assert owner.post(f"/v1/deletion-requests/{request['id']}/cancel",json={"revision":latest["revision"],"reason":"실행 후 원본을 되돌리려는 요청"}).status_code == 409
    assert run(app, request, days=2)["deleted"] == 1
    with app.state.session_factory() as db:
        assert not list(db.scalars(select(LedgerEntry).where(LedgerEntry.event == "COMPENSATE")))


def test_backup_crash_pin_requires_age_reason_and_stopped_confirmation(ops):
    app, _, admin, _, _ = ops
    run_id=begin_backup(app.state.session_factory,"프로세스 중단 백업 보존 테스트")
    with app.state.session_factory() as db:
        db.get(BackupRun,run_id).created_at=utcnow()-timedelta(days=2);db.commit()
    path=f"/v1/admin/retention/backups/{run_id}/abandon"
    assert admin.post(path,json={"reason":"오래되었지만 프로세스 종료 미확인"}).status_code == 409
    assert admin.post(path,json={"reason":"독립 프로세스 종료 확인 후 수동 해제","process_stopped":True}).status_code == 200


def test_two_executors_delete_once_and_write_one_completion_event(ops):
    app, _, _, _, _ = ops
    request, _, key = prepare(ops)
    app.state.settings.retention_customer_delete_enabled = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: run(app, request), range(2)))
    assert sum(item["deleted"] for item in outcomes) == 1
    assert not app.state.storage.path(key).exists()
    with app.state.session_factory() as db:
        assert len(list(db.scalars(select(AuditEvent).where(AuditEvent.action == "deletion_executed")))) == 1


def test_backup_after_customer_deletion_preserves_tombstone_not_missing_file(ops, tmp_path, monkeypatch):
    from services.api.tests.test_backup_restore import backup_script
    app, owner, _, auth, _ = ops
    request, identity, key = prepare(ops)
    app.state.settings.retention_customer_delete_enabled = True
    assert run(app, request)["deleted"] == 1
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", app.state.settings.database_url)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_DIR", str(app.state.settings.storage_dir))
    output, encryption_key = tmp_path/"deleted-backup", tmp_path/"separate.key"
    report = backup_script.backup(output, encryption_key)
    assert report["verified"] and report["objects_verified"] == 0
    backup_script.verify(output, encryption_key, restore_dir=tmp_path/"restored")
    from services.api.config import Settings
    from services.api.database import build_database
    engine, sessions = build_database(Settings(environment="test", database_url=f"sqlite:///{tmp_path/'restored'/'restored.db'}"))
    with sessions() as db:
        asset = db.get(Asset, identity)
        assert asset.metadata_json["_retention"]["state"] == "deleted"
        assert db.get(DeletionRequest,request["id"]).status == "executed"
    engine.dispose()
