"""Private storage adapters; callers must check tenant ownership first."""
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlencode, unquote
import base64
import json

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

    def get_limited(self, key: str, max_bytes: int) -> bytes:
        with self.path(key).open("rb") as source:
            content = source.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValueError("Asset exceeds limit")
        return content


class SupabaseStorage:
    def __init__(self, settings: Settings):
        self.storage_url = settings.supabase_url.rstrip("/") + "/storage/v1"
        self.bucket = quote(settings.supabase_storage_bucket, safe="")
        upload_bucket=getattr(settings,"supabase_upload_bucket","") or settings.supabase_storage_bucket+"-uploads"
        self.upload_bucket=quote(upload_bucket,safe="")
        self.base_url = self.storage_url + "/object/" + self.bucket + "/"
        self.upload_base_url=self.storage_url+"/object/"+self.upload_bucket+"/"
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

    def confirm_missing(self, key: str) -> bool:
        """A download 404 alone may be a permissions error, not lost content.

        Only privileged server credentials, a healthy bucket and an independent
        successful object listing can confirm absence. Any ambiguity returns
        False; callers must also obtain a second spaced observation.
        """
        path = PurePosixPath(validate_key(key))
        token = self.headers["apikey"]
        privileged = token.startswith("sb_secret_")
        if not privileged:
            try:
                claim = token.split(".")[1]
                privileged = json.loads(base64.urlsafe_b64decode(claim + "=" * (-len(claim) % 4))).get("role") == "service_role"
            except (ValueError, IndexError, UnicodeError):
                return False
        if not privileged:
            return False
        with httpx.Client(timeout=15) as client:
            bucket = client.get(self.storage_url + "/bucket/" + self.bucket, headers=self.headers)
            if bucket.status_code != 200 or bucket.json().get("id") != unquote(self.bucket):
                return False
            response = client.post(self.storage_url + "/object/list/" + self.bucket, headers=self.headers,
                json={"prefix": "" if str(path.parent) == "." else str(path.parent), "search": path.name, "limit": 100, "offset": 0})
            if response.status_code != 200:
                return False
            items = response.json()
            if not isinstance(items, list) or len(items) >= 100 or any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in items):
                return False
            return not any(item.get("name") == path.name for item in items)

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

    def signed_upload_url(self, key: str) -> str:
        validate_key(key)
        if len(PurePosixPath(key).parts)!=3 or PurePosixPath(key).parts[1]!="quarantine":
            raise ValueError("Direct uploads require a quarantine key")
        with httpx.Client(timeout=15) as client:
            response=client.post(self.storage_url+"/object/upload/sign/"+self.upload_bucket+"/"+quote(key,safe="/"),headers=self.headers,json={})
            response.raise_for_status()
            path=response.json()["url"]
        if not isinstance(path,str) or not path.startswith("/object/upload/sign/"+self.upload_bucket+"/"):
            raise ValueError("Unexpected private upload signing response")
        return self.storage_url+path

    def get_limited(self,key:str,max_bytes:int)->bytes:
        validate_key(key)
        quarantine=len(PurePosixPath(key).parts)==3 and PurePosixPath(key).parts[1]=="quarantine"
        base_url=self.upload_base_url if quarantine else self.base_url
        with httpx.Client(timeout=45) as client:
            with client.stream("GET",base_url+quote(key,safe="/"),headers=self.headers) as response:
                if response.is_error:
                    # Retain a bounded error body before closing the stream.
                    # Otherwise HTTPStatusError.response.json() cannot distinguish
                    # NoSuchKey from auth/bucket/transient failures afterward.
                    error_body=bytearray()
                    for chunk in response.iter_bytes():
                        error_body.extend(chunk[:max(0,65536-len(error_body))])
                        if len(error_body)>=65536: break
                    httpx.Response(response.status_code,content=bytes(error_body),request=response.request).raise_for_status()
                length=response.headers.get("content-length")
                if length and int(length)>max_bytes: raise ValueError("Asset exceeds limit")
                content=bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content)>max_bytes: raise ValueError("Asset exceeds limit")
                return bytes(content)

    def delete(self,key:str):
        validate_key(key)
        quarantine=len(PurePosixPath(key).parts)==3 and PurePosixPath(key).parts[1]=="quarantine"
        bucket=self.upload_bucket if quarantine else self.bucket
        with httpx.Client(timeout=15) as client:
            response=client.request("DELETE",self.storage_url+"/object/"+bucket,headers=self.headers,json={"prefixes":[key]})
            if response.status_code!=404: response.raise_for_status()


def build_storage(settings: Settings):
    return SupabaseStorage(settings) if settings.storage_backend == "supabase" else LocalStorage(settings.storage_dir)
