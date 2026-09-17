from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from services.api.database import utcnow
from services.api.models import Job, Project, ProjectEditLease, Revision, Tenant, User
from services.api.feature_models import AuditEvent
from services.api.tests.test_api import app, client, image_file, project, register
from services.api.tests.test_business import business, paid, invitation, accept, workspace


def claim(client, item, editor_id=None):
    editor_id = editor_id or str(uuid4())
    response = client.post(f"/v1/projects/{item['id']}/edit-session", json={"editor_id": editor_id})
    assert response.status_code == 200, response.text
    return editor_id, response.json()["data"]


def save(client, item, scene=None, **kwargs):
    return client.patch(f"/v1/projects/{item['id']}/draft", json={"base_revision": item["base_revision"], "scene": scene or item["scene"]}, **kwargs)


def test_two_tabs_same_login_are_exclusive_and_legacy_cannot_bypass(client, app):
    register(client)
    item = project(client)
    editor, lease = claim(client, item)
    url = f"/v1/projects/{item['id']}/edit-session"
    assert lease["editable"] and lease["lease_seconds"] == 120 and lease["heartbeat_seconds"] == 30
    assert claim(client, item, editor)[1]["lease_token"] == lease["lease_token"]
    other = str(uuid4())
    state = client.get(url, params={"editor_id": other}).json()["data"]
    assert state["status"] == "active" and not state["editable"] and not state["holder"]["is_self"]
    assert "lease_token" not in state
    assert client.post(url, json={"editor_id": other}).status_code == 423
    assert save(client, item).json()["code"] == "EDIT_LEASE_HELD"
    assert save(client, item, headers={"X-Editor-Lease": str(uuid4())}).status_code == 423
    valid = save(client, item, headers={"X-Editor-Lease": lease["lease_token"]})
    assert valid.status_code == 200
    assert save(client, item, headers={"X-Editor-Lease": lease["lease_token"]}).status_code == 409
    assert client.get(f"/v1/projects/{item['id']}/revisions").status_code == 200
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ProjectEditLease)) == 1


def test_heartbeat_expiry_release_and_stale_token_never_fall_back(client, monkeypatch):
    register(client)
    item = project(client)
    editor, lease = claim(client, item)
    url = f"/v1/projects/{item['id']}/edit-session"
    body = {"editor_id": editor, "lease_token": lease["lease_token"]}
    now = utcnow()
    monkeypatch.setattr("services.api.editor_sessions.utcnow", lambda: now + timedelta(seconds=60))
    renewed = client.patch(url, json=body)
    assert renewed.status_code == 200
    assert renewed.json()["data"]["expires_at"] > lease["expires_at"]
    monkeypatch.setattr("services.api.editor_sessions.utcnow", lambda: now + timedelta(seconds=181))
    assert client.patch(url, json=body).json()["code"] == "EDIT_LEASE_EXPIRED"
    assert save(client, item, headers={"X-Editor-Lease": lease["lease_token"]}).json()["code"] == "EDIT_LEASE_EXPIRED"
    editor2, lease2 = claim(client, item)
    assert lease2["lease_token"] != lease["lease_token"]
    assert client.request("DELETE", url, json=body).status_code == 423
    release_body = {"editor_id": editor2, "lease_token": lease2["lease_token"]}
    assert client.request("DELETE", url, json=release_body).json()["data"]["status"] == "available"
    assert client.request("DELETE", url, json=release_body).status_code == 200
    assert save(client, item).status_code == 200


def test_lease_is_bound_to_login_not_only_user_and_logout_releases(client, app):
    register(client)
    item = project(client)
    editor, lease = claim(client, item)
    with TestClient(app) as second:
        register(second)
        stolen = save(second, item, headers={"X-Editor-Lease": lease["lease_token"]})
        assert stolen.status_code == 423
        assert second.post(f"/v1/projects/{item['id']}/edit-session", json={"editor_id": editor}).status_code == 423
        assert client.post("/v1/auth/logout").status_code == 200
        _, replacement = claim(second, item)
        assert replacement["lease_token"] != lease["lease_token"]


def test_concurrent_claims_choose_one_editor(app, client):
    register(client)
    item = project(client)
    cookie = client.cookies.get("phoenix_session")
    csrf = client.headers["X-CSRF-Token"]
    def competing_claim(_):
        with TestClient(app) as tab:
            tab.cookies.set("phoenix_session", cookie)
            return tab.post(f"/v1/projects/{item['id']}/edit-session", headers={"X-CSRF-Token": csrf}, json={"editor_id": str(uuid4())}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(competing_claim, range(2))) == [200, 423]


def test_lease_csrf_origin_tenant_viewer_and_workspace_are_enforced(business):
    app, owner, auth, member = business
    item = project(owner)
    other, _ = member("other@example.com")
    path = f"/v1/projects/{item['id']}/edit-session"
    source = owner.get(f"/v1/projects/{item['id']}/revisions").json()["data"]["items"][0]
    history = f"/v1/projects/{item['id']}/revisions"
    body = {"editor_id": str(uuid4())}
    assert other.get(path).status_code == 404
    assert other.post(path, json=body).status_code == 404
    assert other.get(history).status_code == 404
    assert other.get(history + "/" + source["id"]).status_code == 404
    assert other.post(history + "/" + source["id"] + "/restore", json={"base_revision": 1}).status_code == 404
    assert owner.post(path, headers={"X-CSRF-Token": "wrong"}, json=body).status_code == 403
    assert owner.post(path, headers={"Origin": "https://attacker.test"}, json=body).status_code == 403
    paid(app, auth["tenant"]["id"])
    viewer, _ = member("viewer@example.com")
    accept(viewer, invitation(owner, "viewer@example.com", role="viewer"))
    state = viewer.get(path).json()["data"]
    assert state["status"] == "available" and state["editable"] is False
    assert viewer.post(path, json=body).status_code == 403
    assert viewer.get(history + "/" + source["id"]).status_code == 200
    assert viewer.post(history + "/" + source["id"] + "/restore", json={"base_revision": 1}).status_code == 403
    ws = workspace(owner, "Restricted")
    hidden = owner.post("/v1/projects", json={"name": "Hidden", "product_name": "Hidden", "workspace_id": ws}).json()["data"]
    assert viewer.get(f"/v1/projects/{hidden['id']}/revisions").status_code == 404
    assert viewer.get(f"/v1/projects/{hidden['id']}/edit-session").status_code == 404


def test_more_than_hundred_revisions_keyset_paging_and_scoped_detail(client, app):
    register(client)
    item = project(client)
    with app.state.session_factory() as db:
        row = db.get(Project, item["id"])
        for number in range(2, 126):
            scene = deepcopy(row.scene)
            scene["faces"][0]["objects"][0]["text"] = f"Revision {number}"
            db.add(Revision(project_id=row.id, tenant_id=row.tenant_id, number=number, scene=scene, reason="autosave"))
        row.base_revision = 125
        db.commit()
    path = f"/v1/projects/{item['id']}/revisions"
    first = client.get(path).json()["data"]
    assert len(first["items"]) == 100 and first["total"] == 125 and first["current_revision"] == 125
    assert first["has_more"] and first["next_before_number"] == 26
    metadata = client.get(path, params={"limit": 2, "include_scene": "false"}).json()["data"]
    assert [row["number"] for row in metadata["items"]] == [125, 124]
    assert all("scene" not in row for row in metadata["items"])
    assert metadata["next_before_number"] == 124
    # A new latest revision cannot shift or duplicate older cursor pages.
    with app.state.session_factory() as db:
        row = db.get(Project, item["id"])
        db.add(Revision(project_id=row.id, tenant_id=row.tenant_id, number=126, scene=row.scene))
        row.base_revision = 126
        db.commit()
    second = client.get(path, params={"before_number": 26}).json()["data"]
    assert [r["number"] for r in second["items"]] == list(range(25, 0, -1))
    assert not second["has_more"] and second["next_before_number"] is None
    oldest = second["items"][-1]
    assert client.get(path + "/" + oldest["id"]).json()["data"]["scene"] == item["scene"]
    other = project(client)
    assert client.get(f"/v1/projects/{other['id']}/revisions/{oldest['id']}").status_code == 404
    for params in ({"limit": 0}, {"limit": 101}, {"before_number": -1}, {"before_number": 10**100}):
        assert client.get(path, params=params).status_code == 422


def test_whole_scene_restore_creates_new_revision_preserves_source_and_requires_cas(client, app):
    register(client)
    item = project(client)
    editor, lease = claim(client, item)
    client.headers["X-Editor-Lease"] = lease["lease_token"]
    asset = client.post("/v1/assets", files={"file": image_file()}).json()["data"]
    scene = deepcopy(item["scene"])
    scene["faces"][0]["background"] = "#FFDDCC"
    scene["faces"][0]["objects"][0].update(text="전체 장면 복원\n문구", font_weight=700, color="#123456", rotation_deg=5)
    scene["faces"][1]["objects"].append({"id": "photo", "type": "image", "face_id": "back", "asset_id": asset["id"], "x_mm": 30, "y_mm": 30, "width_mm": 30, "height_mm": 40, "rotation_deg": 0, "z_index": 0})
    scene["confirmed_fields"], scene["reviewed_face_ids"] = ["ingredients"], ["front", "back"]
    target = save(client, item, scene).json()["data"]
    source = client.get(f"/v1/projects/{item['id']}/revisions").json()["data"]["items"][0]
    changed = deepcopy(target["scene"])
    changed["faces"][0]["objects"] = []
    changed["faces"][1]["objects"] = []
    current = save(client, target, changed).json()["data"]
    restore = f"/v1/projects/{item['id']}/revisions/{source['id']}/restore"
    assert client.post(restore, json={"base_revision": 2}).status_code == 409
    restored = client.post(restore, json={"base_revision": current["base_revision"]})
    assert restored.status_code == 200, restored.text
    data = restored.json()["data"]
    expected = deepcopy(target["scene"])
    expected["confirmed_fields"], expected["reviewed_face_ids"] = [], []
    assert data["scene"] == expected and data["base_revision"] == 4
    assert data["restored_from_revision"] == {"id": source["id"], "number": 2}
    original = client.get(f"/v1/projects/{item['id']}/revisions/{source['id']}").json()["data"]
    assert original["scene"] == target["scene"]
    assert client.post(restore, json={"base_revision": 3}).status_code == 409
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Revision).where(Revision.project_id == item["id"])) == 4
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "revision_restored"))
        assert event.details["source_revision_id"] == source["id"]


def test_restoration_rechecks_history_asset_workspace_access(business):
    app, owner, auth, member = business
    paid(app, auth["tenant"]["id"])
    ws = workspace(owner, "Private assets")
    hidden = owner.post("/v1/projects", json={"name": "Hidden", "product_name": "Hidden", "workspace_id": ws}).json()["data"]
    asset = owner.post("/v1/assets", data={"project_id": hidden["id"]}, files={"file": image_file()}).json()["data"]
    shared = project(owner)
    scene = deepcopy(shared["scene"])
    scene["faces"][0]["objects"].append({"id": "private", "type": "image", "face_id": "front", "asset_id": asset["id"], "x_mm": 30, "y_mm": 30, "width_mm": 30, "height_mm": 40, "rotation_deg": 0, "z_index": 0})
    saved = save(owner, shared, scene).json()["data"]
    source = owner.get(f"/v1/projects/{shared['id']}/revisions").json()["data"]["items"][0]
    current = save(owner, saved, shared["scene"]).json()["data"]
    editor, _ = member("limited@example.com")
    accept(editor, invitation(owner, "limited@example.com"))
    _, lease = claim(editor, current)
    response = editor.post(f"/v1/projects/{shared['id']}/revisions/{source['id']}/restore", headers={"X-Editor-Lease": lease["lease_token"]}, json={"base_revision": 3})
    assert response.status_code == 404
    assert owner.get(f"/v1/projects/{shared['id']}").json()["data"]["base_revision"] == 3


def test_every_scene_mutation_and_new_quote_export_obeys_active_lease(client, app):
    register(client)
    item = project(client)
    _, lease = claim(client, item)
    base = f"/v1/projects/{item['id']}"
    revision = client.get(base + "/revisions").json()["data"]["items"][0]
    brand = client.post("/v1/brands", json={"name": "Brand"}).json()["data"]
    catalog = client.post("/v1/products", json={"name": "Product", "brand_id": brand["id"], "variants": [{"name": "Variant"}]}).json()["data"]
    requests = [
        ("POST", base + "/revisions", {"base_revision": 1}),
        ("POST", base + f"/revisions/{revision['id']}/restore", {"base_revision": 1}),
        ("PATCH", base + "/settings", {"base_revision": 1, "material": "Film"}),
        ("POST", base + "/bindings/apply", {"base_revision": 1, "product_variant_id": catalog["variants"][0]["id"]}),
        ("POST", "/v1/quotes", {"project_id": item["id"], "base_revision": 1, "action": "image.generate.standard", "requested_units": 1, "prompt": "Warm minimal design"}),
        ("POST", "/v1/exports", {"project_id": item["id"], "base_revision": 1}),
        ("POST", "/v1/production/quotes", {"project_id": item["id"], "base_revision": 1}),
    ]
    for method, path, body in requests:
        response = client.request(method, path, json=body)
        assert response.status_code == 423, (path, response.text)
    with app.state.session_factory() as db:
        assert db.get(Project, item["id"]).base_revision == 1
        assert db.scalar(select(func.count()).select_from(Job)) == 0
    assert client.get(base).status_code == 200


def test_new_ai_job_cannot_use_quote_issued_before_other_tab_claim(client, app):
    register(client)
    item = project(client)
    quote = client.post("/v1/quotes", json={"project_id": item["id"], "base_revision": 1, "action": "image.generate.standard", "requested_units": 1, "prompt": "Warm minimal design"})
    assert quote.status_code == 201, quote.text
    _, lease = claim(client, item)
    queued = client.post("/v1/jobs", headers={"Idempotency-Key": str(uuid4())}, json={"quote_id": quote.json()["data"]["id"]})
    assert queued.status_code == 423
    assert client.get("/v1/credits").json()["data"]["available"] == 30
    success = client.post("/v1/jobs", headers={"Idempotency-Key": str(uuid4()), "X-Editor-Lease": lease["lease_token"]}, json={"quote_id": quote.json()["data"]["id"]})
    assert success.status_code == 202, success.text


def test_restore_recovers_catalog_links_without_rewriting_either_history(client):
    register(client)
    brands = [client.post("/v1/brands", json={"name": name}).json()["data"] for name in ("Original", "New")]
    products = [client.post("/v1/products", json={"name": name, "brand_id": brand["id"], "variants": [{"name": "Default"}]}).json()["data"] for name, brand in zip(("Old product", "New product"), brands)]
    item = client.post("/v1/projects", json={"name": "Catalog restore", "product_name": "Old product", "brand_id": brands[0]["id"], "product_variant_id": products[0]["variants"][0]["id"]}).json()["data"]
    base = f"/v1/projects/{item['id']}"
    original = client.get(base + "/revisions").json()["data"]["items"][0]
    _, lease = claim(client, item)
    client.headers["X-Editor-Lease"] = lease["lease_token"]
    changed = client.post(base + "/bindings/apply", json={"base_revision": 1, "product_variant_id": products[1]["variants"][0]["id"]})
    assert changed.status_code == 200, changed.text
    restored = client.post(base + f"/revisions/{original['id']}/restore", json={"base_revision": 2})
    assert restored.status_code == 200, restored.text
    data = restored.json()["data"]
    assert data["brand_id"] == brands[0]["id"] and data["product_variant_id"] == products[0]["variants"][0]["id"]
    assert data["scene"]["product_variant_id"] == data["product_variant_id"]
    versions = client.get(base + "/revisions").json()["data"]["items"]
    assert versions[1]["scene"]["product_variant_id"] == products[1]["variants"][0]["id"]
    assert versions[2]["scene"] == original["scene"]


def test_concurrent_restore_and_save_allow_one_new_revision(app, client):
    register(client)
    item = project(client)
    source = client.get(f"/v1/projects/{item['id']}/revisions").json()["data"]["items"][0]
    _, lease = claim(client, item)
    cookie, csrf = client.cookies.get("phoenix_session"), client.headers["X-CSRF-Token"]
    def mutation(restore):
        with TestClient(app) as tab:
            tab.cookies.set("phoenix_session", cookie)
            tab.headers.update({"X-CSRF-Token": csrf, "X-Editor-Lease": lease["lease_token"]})
            if restore:
                return tab.post(f"/v1/projects/{item['id']}/revisions/{source['id']}/restore", json={"base_revision": 1}).status_code
            return save(tab, item).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(mutation, [True, False])) == [200, 409]
    with app.state.session_factory() as db:
        assert db.get(Project, item["id"]).base_revision == 2
        assert db.scalar(select(func.count()).select_from(Revision).where(Revision.project_id == item["id"])) == 2


def test_additive_migration_preserves_scenes_and_rolls_back_only_leases(tmp_path, monkeypatch):
    from pathlib import Path
    from sqlalchemy import create_engine, MetaData, Table
    monkeypatch.setenv("APP_ENV", "test")
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(cfg, "0007_google_auth")
    engine = create_engine(url)
    # Reflect the historical tables: later ORM columns must not be inserted
    # into or selected from the deliberately older 0007/0008 schema.
    historical = MetaData()
    projects = Table("projects", historical, autoload_with=engine)
    revisions = Table("project_revisions", historical, autoload_with=engine)
    saved_scene = {"schema_version": "1.0", "faces": [{"id": "front", "objects": [{"id": "text", "text": "원본 한글\n보존"}]}]}
    with Session(engine) as db:
        tenant = Tenant(name="Legacy"); db.add(tenant); db.flush()
        user = User(tenant_id=tenant.id, name="Owner", email="old@example.com"); db.add(user); db.flush()
        project_id, revision_id = str(uuid4()), str(uuid4())
        db.execute(projects.insert().values(id=project_id, tenant_id=tenant.id, created_by=user.id, name="Old project", product_name="Product", brand_name="Brand", description="", template_id="three-side-seal", width_mm=160, height_mm=230, base_revision=1, scene=saved_scene, created_at=utcnow(), updated_at=utcnow(), material=""))
        db.execute(revisions.insert().values(id=revision_id, project_id=project_id, tenant_id=tenant.id, number=1, scene=saved_scene, reason="manual", created_at=utcnow()))
        db.commit()
    command.upgrade(cfg, "0008_editor_sessions")
    assert "project_edit_leases" in inspect(engine).get_table_names()
    with Session(engine) as db:
        assert db.scalar(select(projects.c.scene).where(projects.c.id == project_id)) == saved_scene
        assert db.scalar(select(revisions.c.scene).where(revisions.c.id == revision_id)) == saved_scene
        assert not list(db.execute(text("PRAGMA foreign_key_check")))
    command.downgrade(cfg, "0007_google_auth")
    assert "project_edit_leases" not in inspect(engine).get_table_names()
    with Session(engine) as db:
        assert db.scalar(select(projects.c.scene).where(projects.c.id == project_id)) == saved_scene
        assert db.scalar(select(revisions.c.scene).where(revisions.c.id == revision_id)) == saved_scene
    engine.dispose()
