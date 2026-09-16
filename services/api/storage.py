"""Private storage adapters; callers must check tenant ownership first."""
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlencode

import httpx

from .config import Settings


def validate_key(key: str) -> str:
    path = PurePosixPath(key)
    if path.is_absolute() or ".." in path.parts or "\\" in key or not key:
        raise ValueError("Invalid storage key")
    return key


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        path = (self.root / validate_key(key)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Invalid storage path")
        return path

    def put(self, key: str, body: bytes, content_type: str) -> None:
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)

    def get(self, key: str) -> bytes:
        return self.path(key).read_bytes()


class SupabaseStorage:
    def __init__(self, settings: Settings):
        self.storage_url = settings.supabase_url.rstrip("/") + "/storage/v1"
        self.bucket = quote(settings.supabase_storage_bucket, safe="")
        self.base_url = self.storage_url + "/object/" + self.bucket + "/"
        self.headers = {"Authorization": f"Bearer {settings.supabase_service_role_key}", "apikey": settings.supabase_service_role_key}

    def put(self, key: str, body: bytes, content_type: str) -> None:
        with httpx.Client(timeout=45) as client:
            response = client.post(self.base_url + quote(validate_key(key), safe="/"), headers={**self.headers, "Content-Type": content_type, "x-upsert": "true"}, content=body)
            response.raise_for_status()

    def get(self, key: str) -> bytes:
        with httpx.Client(timeout=45) as client:
            response = client.get(self.base_url + quote(validate_key(key), safe="/"), headers=self.headers)
            response.raise_for_status()
            return response.content

    def signed_url(self, key: str, ttl=60, download_name: str | None = None) -> str:
        with httpx.Client(timeout=15) as client:
            response = client.post(self.storage_url + "/object/sign/" + self.bucket + "/" + quote(validate_key(key), safe="/"), headers=self.headers, json={"expiresIn": max(1, min(int(ttl), 300))})
            response.raise_for_status()
            signed = response.json()["signedURL"]
        if not isinstance(signed, str) or not signed.startswith("/object/sign/"):
            raise ValueError("Unexpected private storage signing response")
        result = self.storage_url + signed
        if download_name is not None:
            result += ("&" if "?" in result else "?") + urlencode({"download": download_name})
        return result


def build_storage(settings: Settings):
    return SupabaseStorage(settings) if settings.storage_backend == "supabase" else LocalStorage(settings.storage_dir)
