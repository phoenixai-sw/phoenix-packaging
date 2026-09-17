from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Lock

from fastapi.testclient import TestClient
import httpx
from sqlalchemy import select, update

from services.api.config import Settings
from services.api.database import utcnow
from services.api.jobs import process_pending_jobs
from services.api.main import create_app
from services.api.models import Job
from services.api.storage import SupabaseStorage
from services.api.tests.test_api import project, register


def queued_job(client):
    item = project(client)
    return client.post("/v1/exports", json={"project_id": item["id"], "base_revision": 1}).json()["data"]["id"]


def make_app(tmp_path):
    return create_app(Settings(environment="test", database_url=f"sqlite:///{tmp_path / 'jobs.db'}", storage_dir=tmp_path / "storage"))


def test_simultaneous_workers_process_distinct_tenants(tmp_path, monkeypatch):
    app = make_app(tmp_path)

    def export(project, output, asset_resolver=None):
        output.write_bytes(b"%PDF-test")
        return {"review_only": True}

    monkeypatch.setattr("services.api.exporters.export_review_pdf", export)
    with TestClient(app) as client:
        register(client)
        first = queued_job(client)
        register(client, "other@example.com")
        second = queued_job(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(process_pending_jobs, app.state.session_factory, app.state.storage, 1) for _ in range(2)]
            assert sum(future.result(timeout=15) for future in futures) == 2
        with app.state.session_factory() as db:
            assert {job.status for job in db.scalars(select(Job).where(Job.id.in_([first, second])))} == {"succeeded"}


def test_expired_worker_cannot_overwrite_recovered_result(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    first_entered, release_first = Event(), Event()
    counter, counter_lock = [0], Lock()

    def export(project, output, asset_resolver=None):
        with counter_lock:
            counter[0] += 1
            call = counter[0]
        if call == 1:
            first_entered.set()
            assert release_first.wait(timeout=15)
        output.write_bytes(b"%PDF-obsolete" if call == 1 else b"%PDF-recovered")
        return {"worker_call": call}

    monkeypatch.setattr("services.api.exporters.export_review_pdf", export)
    with TestClient(app) as client:
        register(client)
        job_id = queued_job(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            original = pool.submit(process_pending_jobs, app.state.session_factory, app.state.storage, 1)
            assert first_entered.wait(timeout=10)
            with app.state.session_factory() as db:
                db.execute(update(Job).where(Job.id == job_id).values(updated_at=utcnow() - timedelta(minutes=16)))
                db.commit()
            recovered = pool.submit(process_pending_jobs, app.state.session_factory, app.state.storage, 1)
            assert recovered.result(timeout=10) == 1
            release_first.set()
            assert original.result(timeout=10) == 1
        with app.state.session_factory() as db:
            job = db.get(Job, job_id)
            assert job.status == "succeeded"
            assert job.result["manifest"]["worker_call"] == 2
            assert app.state.storage.get(job.result["storage_key"]) == b"%PDF-recovered"


def test_failed_job_explicit_retry_recovers_without_new_snapshot(tmp_path, monkeypatch):
    app = make_app(tmp_path)

    def fail(project, output, asset_resolver=None):
        raise OSError("temporary provider failure")

    monkeypatch.setattr("services.api.exporters.export_review_pdf", fail)
    with TestClient(app) as client:
        register(client)
        job_id = queued_job(client)
        process_pending_jobs(app.state.session_factory, app.state.storage)
        assert client.get(f"/v1/jobs/{job_id}").json()["data"]["status"] == "failed"
        with app.state.session_factory() as db:
            original_revision = db.get(Job, job_id).revision_id
        assert client.post(f"/v1/jobs/{job_id}/retry").status_code == 202
        assert client.post(f"/v1/jobs/{job_id}/retry").status_code == 409

        def succeed(project, output, asset_resolver=None):
            output.write_bytes(b"%PDF-retried")
            return {"review_only": True}

        monkeypatch.setattr("services.api.exporters.export_review_pdf", succeed)
        process_pending_jobs(app.state.session_factory, app.state.storage)
        with app.state.session_factory() as db:
            assert db.get(Job, job_id).revision_id == original_revision
        assert client.get(f"/v1/exports/{job_id}/download").content == b"%PDF-retried"


def test_signed_storage_download_uses_bounded_ttl_and_filename(monkeypatch):
    original_client = httpx.Client
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"signedURL": "/object/sign/phoenix-private/tenant/export.pdf?token=temporary"})

    monkeypatch.setattr("services.api.storage.httpx.Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    storage = SupabaseStorage(Settings(supabase_url="https://project.supabase.co", supabase_service_role_key="backend-secret"))
    url = storage.signed_url("tenant/export.pdf", ttl=60, download_name="review.pdf")
    assert url == "https://project.supabase.co/storage/v1/object/sign/phoenix-private/tenant/export.pdf?token=temporary&download=review.pdf"
    assert calls[0].url.path == "/storage/v1/object/sign/phoenix-private/tenant/export.pdf"
    assert calls[0].content == b'{"expiresIn":60}'
    assert calls[0].headers["Authorization"] == "Bearer backend-secret"


def test_upload_limit_is_advertised_and_enforced(tmp_path):
    app = create_app(Settings(environment="test", database_url=f"sqlite:///{tmp_path / 'limit.db'}", storage_dir=tmp_path / "storage", upload_limit=1024))
    with TestClient(app) as client:
        register(client)
        assert client.get("/v1/config").json()["data"]["upload_max_bytes"] == 1024
        assert client.post("/v1/assets", files={"file": ("too-big.png", b"x" * 1025, "image/png")}).status_code == 413
