"""Provision/release preflight must finish before external state can change."""
import json
import os
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config
import httpx
import psycopg
import pytest

from scripts.cloud_settings import validate_cloud_settings

ROOT = Path(__file__).resolve().parents[3]


def hosted():
    return {"APP_ENV": "staging", "DATABASE_URL": "postgresql://test.invalid/postgres?sslmode=require",
            "STORAGE_BACKEND": "supabase", "SUPABASE_URL": "https://storage.example.test",
            "SUPABASE_SERVICE_ROLE_KEY": "test-only", "COOKIE_SECURE": "true", "DEMO_MODE": "true",
            "APP_URL": "https://phoenix-packaging.vercel.app", "ALLOWED_ORIGINS": "https://phoenix-packaging.vercel.app"}


def execute_script(name, temporary_root):
    # Execute the real tracked script with only its filesystem root relocated.
    filename = temporary_root / "scripts" / name
    exec(compile((ROOT / "scripts" / name).read_text(encoding="utf-8"), str(filename), "exec"),
         {"__name__": "__main__", "__file__": str(filename)})


def test_hosted_environment_is_validated_without_inherited_shell_defaults(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://unrelated.example")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-shell-key")
    previous = dict(os.environ)
    settings = validate_cloud_settings(hosted())
    assert settings.app_url == hosted()["APP_URL"] and settings.ai_provider == "fixture"
    assert dict(os.environ) == previous
    missing = hosted(); del missing["APP_URL"]
    with pytest.raises(ValueError, match="explicit APP_URL"):
        validate_cloud_settings(missing)
    mismatch = {**hosted(), "APP_URL": "https://wrong.example"}
    with pytest.raises(ValueError, match="APP_URL must match"):
        validate_cloud_settings(mismatch)
    assert dict(os.environ) == previous


def test_bootstrap_sets_app_url_and_validates_before_any_external_call_or_output(tmp_path, monkeypatch):
    local = tmp_path / ".local"; local.mkdir()
    (tmp_path / "supabase/.temp").mkdir(parents=True)
    (tmp_path / "supabase/.temp/pooler-url").write_text("postgresql://postgres.fixture@pooler.example.test:6543/postgres")
    for name, value in {"provisioning-secrets.json": {"database_password": "test-password", "worker_secret": "test-worker"},
                        "supabase-project-create.json": {"id": "fixture"},
                        "supabase-api-keys.json": [{"name": "service_role", "api_key": "test-only"}]}.items():
        (local / name).write_text(json.dumps(value))
    calls = []
    def forbidden(*args, **kwargs): calls.append("external"); raise AssertionError("Unexpected external access")
    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    def validated_stop(values):
        assert values["APP_URL"] == values["ALLOWED_ORIGINS"] == "https://phoenix-packaging.vercel.app"
        validate_cloud_settings(values)
        raise ValueError("preflight-stop")
    monkeypatch.setattr("scripts.cloud_settings.validate_cloud_settings", validated_stop)
    with pytest.raises(ValueError, match="preflight-stop"):
        execute_script("configure-cloud.py", tmp_path)
    assert calls == [] and not (local / "cloud-env.json").exists()


@pytest.mark.parametrize("problem", ["missing_app_url", "mismatched_app_url", "missing_requested_key"])
def test_update_rejects_bad_config_before_storage_or_vercel_mutation(tmp_path, monkeypatch, problem):
    local = tmp_path / ".local"; local.mkdir()
    values = hosted()
    if problem == "missing_app_url": del values["APP_URL"]
    if problem == "mismatched_app_url": values["APP_URL"] = "https://wrong.example"
    (local / "cloud-env.json").write_text(json.dumps(values))
    (local / "vercel-api-created.json").write_text(json.dumps({"id": "test-project"}))
    argv = ["update-cloud-env.py", "--storage"]
    if problem == "missing_requested_key": argv += ["--keys", "ABSENT_SETTING"]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setenv("APP_URL", hosted()["APP_URL"])
    calls = []
    def forbidden(*args, **kwargs): calls.append("external"); raise AssertionError("Unexpected external access")
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    with pytest.raises(SystemExit) as stopped:
        execute_script("update-cloud-env.py", tmp_path)
    assert stopped.value.code == 1 and calls == []
    assert not (local / "platform-env-input.json").exists()


def test_hosted_migration_validates_before_building_any_database_connection(monkeypatch):
    values = hosted(); del values["APP_URL"]
    for key, value in values.items(): monkeypatch.setenv(key, value)
    monkeypatch.delenv("APP_URL", raising=False)
    calls = []
    def forbidden(*args, **kwargs): calls.append("database"); raise AssertionError("Unexpected database access")
    monkeypatch.setattr("services.api.database.build_database", forbidden)
    config = Config(str(ROOT / "services/api/alembic.ini"))
    with pytest.raises(ValueError, match="APP_URL must match"):
        command.upgrade(config, "head")
    assert calls == []
