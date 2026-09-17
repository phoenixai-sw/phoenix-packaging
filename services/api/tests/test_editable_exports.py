from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from threading import Event, Lock
from uuid import uuid4
from zipfile import ZipFile

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.exc import IntegrityError

from services.api import editable_exports as exports
from services.api.billing.models import LedgerEntry, Subscription
from services.api.config import Settings
from services.api.database import utcnow
from services.api.feature_models import AuditEvent, Membership, Workspace, WorkspaceMember
from services.api.main import create_app
from services.api.models import Asset, Job, Project, Revision, User
from services.api.schemas import Scene
from services.api.storage import SupabaseStorage
from services.api.tests.test_api import app, client, image_file, project, register
from services.api.tests.test_business import business, paid, workspace, invitation, accept


def enqueue(client, item, key=None):
    response = client.post("/v1/exports", json={"project_id": item["id"], "base_revision": item["base_revision"], "kind": "editable"},
                           headers={"Idempotency-Key": key} if key else {})
    assert response.status_code == 202, response.text
    return response.json()["data"]


def run(app):
    return exports.process_editable_jobs(app.state.session_factory, app.state.storage)


def scene_with_assets(client, app, *, lineage=False):
    item = project(client)
    body = image_file()
    asset = client.post("/v1/assets", files={"file": body}).json()["data"]
    scene = deepcopy(item["scene"])
    scene["faces"][0]["objects"][0].update({"text": "높은 단백질 함량\n한글·English 123", "font_weight": 700, "locked": True})
    scene["faces"][0]["objects"].append({"id": "hidden-source", "type": "image", "face_id": "front", "asset_id": asset["id"],
        "x_mm": 22, "y_mm": 42, "width_mm": 20, "height_mm": 30, "rotation_deg": 90, "z_index": -1,
        "visible": False, "print_enabled": False, "locked": True, "crop": {"x": .1, "y": .2, "width": .6, "height": .7}})
    scene["faces"][1]["objects"].append({"id": "sample-code", "type": "barcode", "face_id": "back",
        "x_mm": 20, "y_mm": 175, "width_mm": 37.29, "height_mm": 31.85,
        "barcode_value": "9520000000011", "module_mm": .33, "bar_height_mm": 22.85,
        "barcode_owned": False, "barcode_usage": "sample", "z_index": 9})
    if lineage:
        parent = client.post("/v1/assets", files={"file": body}).json()["data"]
        with app.state.session_factory() as db:
            row = db.get(Asset, asset["id"])
            row.original_name = "../../untrusted\\original.png"
            row.metadata_json = {"sha256": sha256(body[1]).hexdigest(), "prompt": "private prompt not exported", "image_quality": {
                "resampled": True, "extended": True, "native_equivalent_pixels": [12, 15], "root_source_asset_id": parent["id"], "parent_asset_id": parent["id"]}}
            db.commit()
    response = client.patch(f"/v1/projects/{item['id']}/draft", json={"base_revision": 1, "scene": scene})
    assert response.status_code == 200, response.text
    return response.json()["data"], asset, body[1]


def test_complete_archive_preserves_scene_original_lineage_fonts_hashes_and_no_charge(client, app):
    auth = register(client)
    item, asset, raw = scene_with_assets(client, app, lineage=True)
    with app.state.session_factory() as db:
        ledger_before = [(r.id, r.amount, r.event) for r in db.scalars(select(LedgerEntry))]
    job = enqueue(client, item)
    assert run(app) == 1
    result = client.get(f"/v1/jobs/{job['id']}").json()["data"]
    assert result["status"] == "succeeded", result
    assert result["kind"] == "editable_export" and result["result"]["asset_count"] == 2
    assert result["result"]["font_count"] == 2 and result["result"]["credits_charged"] == 0
    assert "storage_key" not in result["result"] and "scene" not in result["result"]
    downloaded = client.get(result["download_url"])
    assert downloaded.headers["content-type"] == "application/zip"
    assert sha256(downloaded.content).hexdigest() == result["result"]["sha256"]
    with ZipFile(BytesIO(downloaded.content)) as archive:
        assert all(not p.startswith(("/", "..")) and "\\" not in p for p in archive.namelist())
        restored = json.loads(archive.read("scene.json"))
        assert restored == item["scene"]
        Scene.model_validate(restored)
        sources = json.loads(archive.read("assets.json"))["items"]
        image = next(a for a in sources if a["id"] == asset["id"])
        assert archive.read(image["path"]) == raw
        assert image["metadata"]["image_quality"]["native_equivalent_pixels"] == [12, 15]
        assert "prompt" not in image["metadata"]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["production_approved"] is False
        assert manifest["revision_number"] == 2
        for file in manifest["files"]:
            content = archive.read(file["path"])
            assert len(content) == file["byte_size"] and sha256(content).hexdigest() == file["sha256"]
        assert b"SIL OPEN FONT LICENSE" in archive.read("fonts/OFL.txt")
        assert b"Copyright" in archive.read("fonts/OFL.txt")
        all_json = b"".join(archive.read(n) for n in archive.namelist() if n.endswith(".json"))
        assert b"storage_key" not in all_json and b"private prompt" not in all_json
    with app.state.session_factory() as db:
        assert [(r.id, r.amount, r.event) for r in db.scalars(select(LedgerEntry))] == ledger_before
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "editable_export_succeeded")).details["credits_charged"] == 0
    assert any(j["id"] == job["id"] for j in client.get(f"/v1/projects/{item['id']}/exports").json()["data"]["items"])


def test_source_json_and_binary_restore_to_independent_project_without_original_asset(client, app):
    register(client)
    item, asset, raw = scene_with_assets(client, app)
    job = enqueue(client, item)
    run(app)
    with ZipFile(BytesIO(client.get(f"/v1/exports/{job['id']}/download").content)) as archive:
        restored = json.loads(archive.read("scene.json"))
        index = json.loads(archive.read("assets.json"))["items"]
        # A new tenant cannot rely on any original tenant asset or private URL.
        register(client, "restore@example.com")
        fresh = project(client)
        mapping = {}
        for source in index:
            response = client.post("/v1/assets", files={"file": (Path(source["path"]).name, archive.read(source["path"]), source["content_type"])})
            assert response.status_code == 201, response.text
            mapping[source["id"]] = response.json()["data"]["id"]
        for face in restored["faces"]:
            for obj in face["objects"]:
                if obj.get("asset_id"):
                    obj["asset_id"] = mapping[obj["asset_id"]]
        saved = client.patch(f"/v1/projects/{fresh['id']}/draft", json={"base_revision": 1, "scene": restored})
        assert saved.status_code == 200, saved.text
        reopened = client.get(f"/v1/projects/{fresh['id']}").json()["data"]["scene"]
        assert reopened["faces"] == restored["faces"]
        assert client.get(f"/v1/assets/{mapping[asset['id']]}/content").content == raw


def test_snapshot_and_operation_survive_later_edits_and_no_new_lease_needed_for_read(client, app):
    register(client)
    item = project(client)
    first = enqueue(client, item, "one-archive")
    changed = deepcopy(item["scene"])
    changed["faces"][0]["objects"][0]["text"] = "새 리비전"
    assert client.patch(f"/v1/projects/{item['id']}/draft", json={"base_revision": 1, "scene": changed}).status_code == 200
    assert enqueue(client, item, "one-archive")["id"] == first["id"]
    assert client.post("/v1/exports", json={"project_id": item["id"], "base_revision": 2, "kind": "editable"}, headers={"Idempotency-Key": "one-archive"}).status_code == 409
    run(app)
    with ZipFile(BytesIO(client.get(f"/v1/exports/{first['id']}/download").content)) as archive:
        assert json.loads(archive.read("scene.json")) == item["scene"]
    lease = client.post(f"/v1/projects/{item['id']}/edit-session", json={"editor_id": str(uuid4())}).json()["data"]
    denied = client.post("/v1/exports", json={"project_id": item["id"], "base_revision": 2, "kind": "editable"})
    assert denied.status_code == 423
    assert client.get(f"/v1/exports/{first['id']}/download").status_code == 200
    assert client.post("/v1/exports", json={"project_id": item["id"], "base_revision": 2, "kind": "editable"}, headers={"X-Editor-Lease": lease["lease_token"]}).status_code == 202
    assert client.post("/v1/exports", json={"project_id": item["id"], "base_revision": 2, "kind": "editable"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403


@pytest.mark.parametrize("failure", ["missing", "corrupt", "metadata", "upload"])
def test_incomplete_bundle_never_publishes_and_free_retry_is_explicit(client, app, monkeypatch, failure):
    register(client)
    item, asset, raw = scene_with_assets(client, app)
    with app.state.session_factory() as db:
        row = db.get(Asset, asset["id"])
        row.metadata_json = {"sha256": sha256(raw).hexdigest()}
        source_key = row.storage_key
        db.commit()
    job = enqueue(client, item)
    original_put = app.state.storage.put
    if failure == "missing":
        app.state.storage.path(source_key).unlink()
    elif failure == "corrupt":
        original_put(source_key, b"corrupt", "image/png")
    elif failure == "metadata":
        with app.state.session_factory() as db:
            db.get(Asset, asset["id"]).byte_size += 1
            db.commit()
    else:
        monkeypatch.setattr(app.state.storage, "put", lambda *args: (_ for _ in ()).throw(OSError("secret signed URL")))
    assert run(app) == 1
    failed = client.get(f"/v1/jobs/{job['id']}").json()["data"]
    assert failed["status"] == "failed" and failed["download_url"] is None and "secret" not in failed["error"]
    assert client.get(f"/v1/exports/{job['id']}/download").status_code == 409
    monkeypatch.setattr(app.state.storage, "put", original_put)
    original_put(source_key, raw, "image/png")
    with app.state.session_factory() as db:
        db.get(Asset, asset["id"]).byte_size = len(raw)
        db.commit()
    assert client.post(f"/v1/jobs/{job['id']}/retry").status_code == 202
    assert client.post(f"/v1/jobs/{job['id']}/retry").status_code == 409
    assert run(app) == 1
    assert client.get(f"/v1/jobs/{job['id']}").json()["data"]["status"] == "succeeded"


@pytest.mark.parametrize("when", ["queued", "after_upload"])
def test_actor_revocation_before_generation_or_publication_leaves_no_download(client, app, monkeypatch, when):
    auth = register(client)
    item = project(client)
    job = enqueue(client, item)
    def revoke():
        with app.state.session_factory() as db:
            db.get(User, auth["user"]["id"]).is_active = False
            db.commit()
    if when == "queued":
        revoke()
    else:
        original = app.state.storage.put
        def put(*args):
            original(*args)
            revoke()
        monkeypatch.setattr(app.state.storage, "put", put)
    run(app)
    with app.state.session_factory() as db:
        assert db.get(Job, job["id"]).status == "failed"
        assert db.get(Job, job["id"]).result is None
    # Publication remains forbidden. The unpublished object is retained as a
    # known write intent until hold/backup-aware GC is explicitly enabled.
    assert len(list(app.state.storage.root.rglob("*.zip"))) == (1 if when == "after_upload" else 0)
    if when == "after_upload":
        from services.api.retention.models import StorageIntent
        with app.state.session_factory() as db:
            assert db.scalar(select(StorageIntent).where(StorageIntent.job_id == job["id"])).status == "planned"


def test_workspace_tenant_viewer_and_expired_membership_access(business):
    app, owner, auth, member = business
    paid(app, auth["tenant"]["id"])
    allowed, denied = workspace(owner, "허용"), workspace(owner, "비공개")
    item = owner.post("/v1/projects", json={"name": "팀 자료", "product_name": "샘플", "workspace_id": allowed}).json()["data"]
    job = enqueue(owner, item)
    run(app)
    other, _ = member("foreign@example.com")
    assert other.get(f"/v1/exports/{job['id']}/download").status_code == 404
    editor, editor_auth = member("member@example.com")
    accept(editor, invitation(owner, "member@example.com", [allowed]))
    assert editor.get(f"/v1/exports/{job['id']}/download").status_code == 200
    with app.state.session_factory() as db:
        membership = db.scalar(select(Membership).where(Membership.user_id == editor_auth["user"]["id"]))
        membership.role = "viewer"
        db.commit()
    assert editor.post("/v1/exports", json={"project_id": item["id"], "base_revision": 1, "kind": "editable"}).status_code == 403
    with app.state.session_factory() as db:
        db.get(Project, item["id"]).workspace_id = denied
        db.commit()
    assert editor.get(f"/v1/exports/{job['id']}/download").status_code == 404
    with app.state.session_factory() as db:
        db.get(Project, item["id"]).workspace_id = allowed
        subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == auth["tenant"]["id"]))
        subscription.cancel_at_period_end = True
        subscription.paid_until = utcnow() - timedelta(days=1)
        db.commit()
    assert editor.get(f"/v1/exports/{job['id']}/download").status_code == 403
    assert owner.get(f"/v1/exports/{job['id']}/download").status_code == 200
    # The owner retains free editable access after cancellation with no purchase.
    assert enqueue(owner, item, "after-cancel")["kind"] == "editable_export"


@pytest.mark.parametrize("stage", ["queued", "after_upload", "download"])
def test_each_source_workspace_is_rechecked_not_only_project(business, monkeypatch, stage):
    app, owner, auth, member = business
    paid(app, auth["tenant"]["id"])
    allowed, denied = workspace(owner, "프로젝트"), workspace(owner, "별도 원본")
    item, asset, _ = scene_with_assets(owner, app)
    with app.state.session_factory() as db:
        db.get(Project, item["id"]).workspace_id = allowed
        db.commit()
    editor, _ = member("archive-editor@example.com")
    accept(editor, invitation(owner, "archive-editor@example.com", [allowed]))
    job = enqueue(editor, item)
    def revoke_source():
        with app.state.session_factory() as db:
            db.get(Asset, asset["id"]).workspace_id = denied
            db.commit()
    if stage == "queued":
        revoke_source()
    elif stage == "after_upload":
        put = app.state.storage.put
        def publish(*args):
            put(*args)
            revoke_source()
        monkeypatch.setattr(app.state.storage, "put", publish)
    run(app)
    if stage == "download":
        assert editor.get(f"/v1/exports/{job['id']}/download").status_code == 200
        revoke_source()
        assert editor.get(f"/v1/exports/{job['id']}/download").status_code == 404
        assert owner.get(f"/v1/exports/{job['id']}/download").status_code == 200
    else:
        with app.state.session_factory() as db:
            assert db.get(Job, job["id"]).status == "failed"
        # An authorized owner can retry the same immutable archive. The retry
        # records its new actor rather than impersonating the revoked requester.
        assert owner.post(f"/v1/jobs/{job['id']}/retry").status_code == 202
        assert run(app) == 1
        assert owner.get(f"/v1/exports/{job['id']}/download").status_code == 200


def test_cross_kind_running_limit_and_combined_free_rate_limit(client, app):
    register(client)
    item = project(client)
    editable = enqueue(client, item)
    review = client.post("/v1/exports", json={"project_id": item["id"], "base_revision": 1}).json()["data"]
    with app.state.session_factory() as db:
        db.get(Job, review["id"]).status = "running"
        db.commit()
        with pytest.raises(IntegrityError):
            db.execute(update(Job).where(Job.id == editable["id"]).values(status="running"))
            db.commit()
        db.rollback()
    assert run(app) == 0
    with app.state.session_factory() as db:
        row = db.get(Job, review["id"])
        row.status = "failed"
        for number in range(28):
            db.add(Job(tenant_id=row.tenant_id, project_id=row.project_id, revision_id=row.revision_id,
                       kind="review_export", status="failed", operation_key=f"free-limit-{number}", request_hash=str(number), snapshot={}))
        db.commit()
    # An existing operation is returned even when subsequent new free work is throttled.
    assert enqueue(client, item)["id"] == editable["id"]
    for kind in ("review", "editable"):
        response = client.post("/v1/exports", json={"project_id": item["id"], "base_revision": 1, "kind": kind}, headers={"Idempotency-Key": "over-limit-" + kind})
        assert response.status_code == 429, response.text
    assert run(app) == 1


def test_archive_total_limit_never_publishes_partial_zip(client, app, monkeypatch):
    register(client)
    item = project(client)
    job = enqueue(client, item)
    monkeypatch.setattr(exports, "MAX_BUNDLE_BYTES", 1000)
    assert run(app) == 1
    result = client.get(f"/v1/jobs/{job['id']}").json()["data"]
    assert result["status"] == "failed" and result["download_url"] is None
    assert not list(app.state.storage.root.rglob("*.zip"))


@pytest.mark.parametrize("mutation,code", [("font", "EDITABLE_FONT_UNSUPPORTED"), ("url", "EDITABLE_ASSET_INVALID"), ("bytes", "EDITABLE_SIZE_LIMIT")])
def test_untrusted_or_oversized_source_snapshot_fails_closed(client, app, mutation, code):
    register(client)
    item, asset, _ = scene_with_assets(client, app)
    with app.state.session_factory() as db:
        row = db.get(Project, item["id"])
        scene = deepcopy(row.scene)
        if mutation == "font":
            scene["faces"][0]["objects"][0]["font_id"] = "../../private"
        elif mutation == "url":
            scene["faces"][0]["objects"][-1]["asset_id"] = "https://private.invalid/secret"
        else:
            db.get(Asset, asset["id"]).byte_size = exports.MAX_ASSET_BYTES + 1
        row.scene = scene
        db.commit()
    response = client.post("/v1/exports", json={"project_id": item["id"], "base_revision": 2, "kind": "editable"})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == code


def test_hosted_download_is_redirect_without_large_body_proxy(tmp_path, monkeypatch):
    storage = SupabaseStorage(Settings(supabase_url="https://private.example", supabase_service_role_key="test-not-secret"))
    monkeypatch.setattr("services.api.main.build_storage", lambda _: storage)
    app = create_app(Settings(environment="test", database_url=f"sqlite:///{tmp_path/'hosted.db'}", storage_dir=tmp_path/'unused'))
    calls = []
    monkeypatch.setattr(storage, "signed_url", lambda key, **kwargs: calls.append((key, kwargs)) or "https://private.example/signed-test")
    raw=b'fixture-export'*(30*1024*1024//14)
    monkeypatch.setattr(storage, "get", lambda *_: pytest.fail("Download integrity uses a bounded read"))
    monkeypatch.setattr(storage, "get_limited", lambda *_: raw)
    with TestClient(app) as client:
        register(client)
        item = project(client)
        job = enqueue(client, item)
        with app.state.session_factory() as db:
            row = db.get(Job, job["id"])
            row.status, row.result = "succeeded", {"storage_key": f"{row.tenant_id}/exports/{row.id}/test.zip", "byte_size":len(raw),"sha256":sha256(raw).hexdigest()}
            db.commit()
        response = client.get(f"/v1/exports/{job['id']}/download", follow_redirects=False)
        assert response.status_code == 307 and len(response.content) == 0
        assert calls[0][1]["ttl"] == 60 and calls[0][1]["download_name"].endswith(".zip")


def test_expired_worker_cannot_replace_or_remove_recovered_archive(client, app, monkeypatch):
    register(client)
    item = project(client)
    job = enqueue(client, item)
    original = exports.build_editable_archive
    started, release, mutex = Event(), Event(), Lock()
    count = [0]
    def build(*args):
        with mutex:
            count[0] += 1
            attempt = count[0]
        if attempt == 1:
            started.set()
            assert release.wait(30)
        return original(*args)
    monkeypatch.setattr(exports, "build_editable_archive", build)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, app)
        assert started.wait(10)
        with app.state.session_factory() as db:
            db.get(Job, job["id"]).updated_at = utcnow() - timedelta(minutes=16)
            db.commit()
        assert pool.submit(run, app).result(30) == 1
        with app.state.session_factory() as db:
            valid_key = db.get(Job, job["id"]).result["storage_key"]
        release.set()
        assert first.result(30) == 1
    with app.state.session_factory() as db:
        assert db.get(Job, job["id"]).result["storage_key"] == valid_key
    assert len(list(app.state.storage.root.rglob("*.zip"))) == 1
    assert app.state.storage.get(valid_key).startswith(b"PK")


def test_running_export_index_serializes_different_kinds_and_migration_preserves_data(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path/'upgrade.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(cfg, "0008_editor_sessions")
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(text("INSERT INTO tenants(id,name,created_at) VALUES('tenant','preserved','2026-09-18')"))
    command.upgrade(cfg, "0009_editable_exports")
    with engine.connect() as db:
        index = db.scalar(text("SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_running_export_tenant'"))
        assert "editable_export" in index
        assert db.scalar(text("SELECT name FROM tenants WHERE id='tenant'")) == "preserved"
    command.downgrade(cfg, "0008_editor_sessions")
    with engine.connect() as db:
        assert "editable_export" not in db.scalar(text("SELECT sql FROM sqlite_master WHERE name='uq_running_export_tenant'"))
    engine.dispose()
