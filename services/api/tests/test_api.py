from copy import deepcopy
from datetime import timedelta
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import select

from services.api.config import Settings
from services.api.database import utcnow
from services.api.main import create_app
from services.api.models import LoginSession, User
from services.api.tests.auth_helpers import register, google_login


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(environment="test", database_url=f"sqlite:///{tmp_path / 'test.db'}", storage_dir=tmp_path / "storage", worker_secret="test-worker-secret"))


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def project(client):
    response = client.post("/v1/projects", json={"name": "산들 포장", "product_name": "유기농 현미", "brand_name": "산들", "width_mm": 160, "height_mm": 230})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def image_file():
    raw = BytesIO()
    Image.new("RGB", (32, 40), "red").save(raw, format="PNG")
    return ("photo.png", raw.getvalue(), "image/png")


def test_real_persistence_across_application_restart(tmp_path):
    settings = Settings(environment="test", database_url=f"sqlite:///{tmp_path / 'durable.db'}", storage_dir=tmp_path / "storage")
    with TestClient(create_app(settings)) as first:
        register(first)
        created = project(first)
        scene = deepcopy(created["scene"])
        scene["faces"][0]["objects"][1]["text"] = "높은 단백질 함량\n유기농 현미"
        result = first.patch(f"/v1/projects/{created['id']}/draft", json={"base_revision": 1, "scene": scene})
        assert result.status_code == 200, result.text
        assert result.json()["data"]["base_revision"] == 2
    with TestClient(create_app(settings)) as second:
        login = google_login(second)
        assert login.status_code == 200
        persisted = second.get(f"/v1/projects/{created['id']}").json()["data"]
        assert persisted["scene"]["faces"][0]["objects"][1]["text"] == "높은 단백질 함량\n유기농 현미"


def test_session_tokens_are_hashed_and_logout_revokes(client, app):
    register(client)
    cookie = client.cookies.get("phoenix_session")
    with app.state.session_factory() as db:
        stored = db.scalar(select(LoginSession))
        assert stored.token_hash != cookie and len(stored.token_hash) == 64
        assert db.scalar(select(User)).password_hash == ""
        assert db.scalar(select(User)).google_sub
    assert client.post("/v1/auth/logout").status_code == 200
    client.cookies.set("phoenix_session", cookie)
    assert client.get("/v1/me").status_code == 401


def test_csrf_and_hostile_origin_are_rejected(client):
    data = register(client)
    del client.headers["X-CSRF-Token"]
    assert client.post("/v1/projects", json={"name": "x", "product_name": "x"}).status_code == 403
    client.headers["X-CSRF-Token"] = data["csrf_token"]
    assert client.post("/v1/projects", headers={"Origin": "https://attacker.example"}, json={"name": "x", "product_name": "x"}).status_code == 403
    assert client.post("/v1/auth/google", headers={"Origin": "https://attacker.example"}, json={"credential": "test-credential-invalid", "csrf_token": "x" * 32}).status_code == 403


def test_stale_draft_cannot_overwrite_and_snapshots_stay_immutable(client):
    register(client)
    item = project(client)
    url = f"/v1/projects/{item['id']}"
    updated_scene = deepcopy(item["scene"])
    updated_scene["faces"][0]["objects"][1]["text"] = "첫 번째 저장"
    assert client.patch(url + "/draft", json={"base_revision": 1, "scene": updated_scene}).status_code == 200
    stale = client.patch(url + "/draft", json={"base_revision": 1, "scene": item["scene"]})
    assert stale.status_code == 409
    assert stale.json()["field_errors"]["base_revision"]["server_revision"] == 2
    saved = client.get(url).json()["data"]
    assert saved["scene"]["faces"][0]["objects"][1]["text"] == "첫 번째 저장"
    revisions = client.get(url + "/revisions").json()["data"]["items"]
    assert revisions[0]["number"] == 2 and revisions[0]["scene"] == saved["scene"]
    assert revisions[1]["number"] == 1 and revisions[1]["scene"] == item["scene"]
    snap = client.post(url + "/revisions", json={"base_revision": 2})
    assert snap.status_code == 201
    assert snap.json()["data"]["scene"] == saved["scene"]


def test_tenant_isolation_for_projects_assets_jobs_and_file_linking(client, app):
    register(client)
    item = project(client)
    upload = client.post("/v1/assets", files={"file": image_file()})
    assert upload.status_code == 201
    asset_id = upload.json()["data"]["id"]
    export = client.post("/v1/exports", json={"project_id": item["id"], "base_revision": 1})
    assert export.status_code == 202
    job_id = export.json()["data"]["id"]
    with TestClient(app) as other:
        register(other, "other@example.com")
        for route in [f"/v1/projects/{item['id']}", f"/v1/assets/{asset_id}/content", f"/v1/jobs/{job_id}", f"/v1/exports/{job_id}/download"]:
            assert other.get(route).status_code == 404, route
        own = project(other)
        own["scene"]["faces"][0]["objects"].append({"id": "stolen-image", "type": "image", "face_id": "front", "x_mm": 20, "y_mm": 20, "width_mm": 30, "height_mm": 30, "asset_id": asset_id})
        denied = other.patch(f"/v1/projects/{own['id']}/draft", json={"base_revision": 1, "scene": own["scene"]})
        assert denied.status_code == 404


def test_malicious_svg_false_mime_and_external_assets_rejected(client):
    register(client)
    for file in [("attack.svg", b'<svg onload="alert(1)"></svg>', "image/svg+xml"), ("attack.png", b"<script>evil</script>", "image/png")]:
        assert client.post("/v1/assets", files={"file": file}).status_code == 422
    item = project(client)
    item["scene"]["faces"][0]["objects"].append({"id": "evil", "type": "image", "face_id": "front", "x_mm": 20, "y_mm": 20, "width_mm": 30, "height_mm": 30, "asset_id": "https://attacker.example/private"})
    assert client.patch(f"/v1/projects/{item['id']}/draft", json={"base_revision": 1, "scene": item["scene"]}).status_code == 422


def test_scene_cannot_change_geometry_or_add_script_properties(client):
    register(client)
    item = project(client)
    bad = deepcopy(item["scene"])
    bad["faces"][0]["width_mm"] = 200
    assert client.patch(f"/v1/projects/{item['id']}/draft", json={"base_revision": 1, "scene": bad}).status_code == 422
    bad = deepcopy(item["scene"])
    bad["faces"][0]["objects"][0]["onload"] = "evil()"
    assert client.patch(f"/v1/projects/{item['id']}/draft", json={"base_revision": 1, "scene": bad}).status_code == 422


def test_export_idempotency_and_production_gate(client):
    register(client)
    item = project(client)
    body = {"project_id": item["id"], "base_revision": 1}
    first = client.post("/v1/exports", json=body, headers={"Idempotency-Key": "same-operation"})
    second = client.post("/v1/exports", json=body, headers={"Idempotency-Key": "same-operation"})
    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    other = project(client)
    assert client.post("/v1/exports", json={"project_id": other["id"], "base_revision": 1}, headers={"Idempotency-Key": "same-operation"}).status_code == 409
    assert client.post("/v1/exports", json={**body, "kind": "production"}).status_code == 422
    assert client.post("/v1/internal/jobs/process").status_code == 404


def test_viewer_cannot_edit_upload_or_export(client, app):
    data = register(client)
    item = project(client)
    with app.state.session_factory() as db:
        user = db.get(User, data["user"]["id"])
        user.role = "viewer"
        from services.api.billing.models import Subscription
        now=utcnow()
        db.add(Subscription(tenant_id=user.tenant_id,plan_id="pro",status="active",anchor_day=now.day,current_period_start=now,current_period_end=now+timedelta(days=30),billing_anchor=now,paid_until=now+timedelta(days=30)))
        db.commit()
    assert client.get(f"/v1/projects/{item['id']}").status_code == 200
    assert client.patch(f"/v1/projects/{item['id']}/draft", json={"base_revision": 1, "scene": item["scene"]}).status_code == 403
    assert client.post("/v1/assets", files={"file": image_file()}).status_code == 403
    assert client.post("/v1/exports", json={"project_id": item["id"], "base_revision": 1}).status_code == 403


def test_hosted_settings_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="PostgreSQL"):
        Settings(environment="staging").validate()
    with pytest.raises(ValueError, match="Fixture/demo"):
        Settings(environment="production", database_url="postgresql://x", cookie_secure=True, app_url="https://example.com", allowed_origins=("https://example.com",), storage_backend="supabase", supabase_url="https://example.supabase.co", supabase_service_role_key="test", demo_mode=True).validate()


def test_review_export_worker_creates_real_pdf(client, app):
    register(client)
    item = project(client)
    response = client.post("/v1/exports", json={"project_id": item["id"], "base_revision": 1})
    job_id = response.json()["data"]["id"]
    processed = client.post("/v1/internal/jobs/process", headers={"Authorization": "Bearer test-worker-secret"})
    assert processed.status_code == 200, processed.text
    assert processed.json()["data"]["processed"] == 1
    finished = client.get(f"/v1/jobs/{job_id}").json()["data"]
    assert finished["status"] == "succeeded", finished
    assert "storage_key" not in finished["result"]
    pdf = client.get(f"/v1/exports/{job_id}/download")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")
    assert "application/pdf" in pdf.headers["content-type"]
