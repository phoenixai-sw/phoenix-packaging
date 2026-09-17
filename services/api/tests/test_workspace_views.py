from fastapi.testclient import TestClient
from sqlalchemy import select
from services.api.models import Asset
from services.api.tests.test_api import app, client, register, project, image_file
from services.api.tests.test_business import paid, workspace, invitation, accept


def test_dashboard_and_asset_library_do_not_leak_other_tenants(client, app):
    register(client)
    created = project(client)
    asset = client.post("/v1/assets", files={"file": image_file()}).json()["data"]
    summary = client.get("/v1/workspace/overview").json()["data"]
    assert summary["project_count"] == 1
    assert all(event["project_id"] == created["id"] for event in summary["timeline"])
    result = client.get("/v1/assets", params={"q": "photo", "limit": 1}).json()["data"]
    assert [a["id"] for a in result["items"]] == [asset["id"]]
    assert "storage_key" not in result["items"][0]
    assert client.get("/v1/assets", params={"q": "%"}).json()["data"]["items"] == []
    with TestClient(app) as other:
        register(other, "other-assets@example.com")
        assert other.get("/v1/assets").json()["data"]["items"] == []
        assert other.get("/v1/workspace/overview").json()["data"]["project_count"] == 0
        assert other.get("/v1/assets", params={"project_id": created["id"]}).status_code == 404


def test_workspace_filters_apply_before_asset_pagination_and_summary(client, app):
    auth = register(client)
    paid(app, auth["tenant"]["id"])
    allowed, hidden = workspace(client, "Allowed"), workspace(client, "Hidden")
    visible_project = client.post("/v1/projects", json={"name": "Visible", "product_name": "Visible", "workspace_id": allowed}).json()["data"]
    client.post("/v1/projects", json={"name": "Hidden", "product_name": "Hidden", "workspace_id": hidden})
    asset_ids = [client.post("/v1/assets", files={"file": image_file()}).json()["data"]["id"] for _ in range(3)]
    with app.state.session_factory() as db:
        for index, space in enumerate([allowed, hidden, hidden]):
            db.get(Asset, asset_ids[index]).workspace_id = space
        db.commit()
    invite = invitation(client, "asset-viewer@example.com", [allowed], "viewer")
    with TestClient(app) as viewer:
        register(viewer, "asset-viewer@example.com")
        accept(viewer, invite)
        response = viewer.get("/v1/assets", params={"limit": 1}).json()["data"]
        assert [row["id"] for row in response["items"]] == [asset_ids[0]]
        assert response["next_offset"] is None
        overview = viewer.get("/v1/workspace/overview").json()["data"]
        assert overview["project_count"] == 1
        assert all(row["project_id"] == visible_project["id"] for row in overview["timeline"])
