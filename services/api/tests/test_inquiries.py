from fastapi.testclient import TestClient
from services.api.main import create_app
from services.api.config import Settings
from services.api.tests.auth_helpers import register

BODY = {"company": "킹콩푸드", "name": "김대표", "email": "owner@example.com", "phone": "010-0000-0000", "package_type": "stand_up_pouch",
        "monthly_changes": 4, "next_order_date": "2026-10-15", "has_dieline": True, "message": "오리스틱 240×330 지퍼 파우치", "consent": True}


def test_public_submit_admin_list_update_and_rate_limit(tmp_path):
    app = create_app(Settings(environment="test", database_url=f"sqlite:///{tmp_path/'db.sqlite'}", storage_dir=tmp_path/"storage", admin_emails=("qa@example.com",)))
    with TestClient(app) as client:
        created = client.post("/v1/inquiries", json=BODY)
        assert created.status_code == 201, created.text
        receipt = created.json()["data"]
        assert set(receipt) == {"id", "received_at"}
        assert client.post("/v1/inquiries", json={**BODY, "consent": False}).status_code == 422
        assert client.post("/v1/inquiries", json={**BODY, "package_type": "bag"}).status_code == 422
        assert client.get("/v1/admin/inquiries").status_code == 401
        register(client, email="qa@example.com")
        listed = client.get("/v1/admin/inquiries")
        assert listed.status_code == 200 and listed.json()["data"]["items"][0]["company"] == "킹콩푸드"
        updated = client.patch(f"/v1/admin/inquiries/{receipt['id']}", json={"status": "contacted", "note": "10/1 통화"})
        assert updated.status_code == 200 and updated.json()["data"]["status"] == "contacted"
    with TestClient(app) as anonymous:
        for _ in range(9):
            assert anonymous.post("/v1/inquiries", json=BODY).status_code == 201
        assert anonymous.post("/v1/inquiries", json=BODY).status_code == 429


def test_non_admin_cannot_read_inquiries(tmp_path):
    app = create_app(Settings(environment="test", database_url=f"sqlite:///{tmp_path/'db.sqlite'}", storage_dir=tmp_path/"storage", admin_emails=("qa@example.com",)))
    with TestClient(app) as client:
        register(client, email="member@example.com")
        assert client.get("/v1/admin/inquiries").status_code == 403
