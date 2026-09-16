"""Generate the one authorized Seedance 2.5 homepage film.

Secrets are read locally and used only in the official BytePlus request header.
This script never writes credentials, input Base64, or signed output URLs.
Creation and polling are separate so a timeout cannot silently bill a duplicate.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL = "dreamina-seedance-2-5-260628"
BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
METADATA = ROOT / "docs" / "media-video.json"
OUTPUT = ROOT / "apps" / "web" / "public" / "media" / "packaging-film.mp4"
PROMPT = """Create an 8-second photorealistic premium food packaging campaign film based on the supplied reference image. Match exactly the three existing stand-up pouches: forest-green MATCHA pouch on the left, warm cream OAT & HONEY granola pouch in the middle, and muted coral ORCHARD pouch on the right. Preserve their package proportions, typography, printed label design, colors, sealed tops, and believable soft paper-film materials throughout. Use the same warm sunlit travertine studio, subtle tea leaves, ceramic bowl, oats and apricot accents. Compose a wide cinematic 16:9 shot by extending the surrounding studio space naturally, keeping all three pouches visible and the product design beautifully legible. One continuous sophisticated slow camera dolly moving a few centimeters forward and slightly right, gentle parallax and almost imperceptible warm natural shadow movement. Elegant restrained luxury editorial product cinematography, realistic soft daylight, creamy warm highlights, deep forest greens, tactile material detail, stable geometry, physically believable contact shadows, smooth controlled motion, high-end Korean lifestyle brand art direction. End on a calm hero composition. No cuts, no floating products, no hands, no people, no morphing, no new packaging, no added letters, no text overlays, no captions, no watermark. Silent video, no music or sound."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def credentials(path: Path | None) -> str:
    import os
    key = os.environ.get("BYTEPLUS_API_KEY", "")
    if not key and path:
        raw = path.read_text(encoding="utf-8-sig")
        matches = re.findall(r"ark-[A-Za-z0-9_-]+", raw)
        if len(matches) != 1:
            raise SystemExit("Expected exactly one BytePlus API key in the supplied credential file.")
        key = matches[0]
    if not key:
        raise SystemExit("Set BYTEPLUS_API_KEY or provide --credentials-file.")
    return key


def clean(value: object, key: str) -> str:
    text = str(value).replace(key, "[REDACTED]")
    text = re.sub(r"(?:ark-|sk-)[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"https?://\S+", "[URL omitted]", text)
    return text[:1500]


def save(data: dict) -> None:
    data["updated_at"] = now()
    METADATA.parent.mkdir(parents=True, exist_ok=True)
    METADATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request(key: str, path: str, body: dict | None = None) -> dict:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=payload, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def safe_http_error(exc: urllib.error.HTTPError, key: str) -> dict:
    try:
        body = json.loads(exc.read().decode("utf-8"))
    except (ValueError, UnicodeError):
        body = {}
    error = body.get("error", {})
    return {"http_status": exc.code, "code": clean(error.get("code", "HTTP_ERROR"), key),
            "message": clean(error.get("message", "The provider rejected the request."), key)}


def submit(key: str, image: Path, retry: bool) -> dict:
    previous = json.loads(METADATA.read_text(encoding="utf-8")) if METADATA.exists() else None
    if previous and (not retry or previous.get("status") not in {"failed", "request_failed"} or previous.get("attempts", 1) >= 2):
        raise SystemExit("A generation record already exists. Poll its task; only one explicit retry after confirmed failure is permitted.")
    raw = image.read_bytes()
    if len(raw) >= 30 * 1024 * 1024:
        raise SystemExit("Reference image must be smaller than 30MB.")
    mime = mimetypes.guess_type(image.name)[0] or "image/webp"
    metadata = {"provider": "BytePlus ModelArk", "requested_model": MODEL, "status": "submitting", "attempts": (previous or {}).get("attempts", 0) + 1,
                "created_at": now(), "prompt": PROMPT, "reference_image": str(image.relative_to(ROOT)).replace("\\", "/"),
                "reference_sha256": hashlib.sha256(raw).hexdigest(), "reference_role": "reference_image",
                "requested_output": {"duration": 8, "resolution": "720p", "ratio": "16:9", "generate_audio": False, "watermark": False},
                "documentation": ["https://docs.byteplus.com/en/docs/ModelArk/1520757", "https://docs.byteplus.com/en/docs/ModelArk/1521309", "https://docs.byteplus.com/en/docs/Byteplus_LAS/video_gen_enhanced"],
                "note": "The 3:2 reference uses reference_image rather than first_frame so the output can be 16:9. No credentials, signed URLs, or Base64 input are retained."}
    if previous:
        metadata["previous_attempt"] = {name: previous.get(name) for name in ("task_id", "status", "error")}
    save(metadata)
    body = {"model": MODEL, "content": [{"type": "text", "text": PROMPT}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")}, "role": "reference_image"}],
            "omni_reference_task_type": "reference", **metadata["requested_output"]}
    try:
        result = request(key, "/contents/generations/tasks", body)
    except urllib.error.HTTPError as exc:
        error = safe_http_error(exc, key)
        metadata["status"] = "blocked" if exc.code in (401, 403, 404) else "request_failed"
        metadata["error"] = error
        save(metadata)
        print(json.dumps({"status": metadata["status"], "error": error}, ensure_ascii=False))
        return metadata
    except Exception as exc:
        metadata["status"] = "submission_unknown"
        metadata["error"] = {"code": type(exc).__name__, "message": clean(exc, key)}
        save(metadata)
        print(json.dumps({"status": "submission_unknown", "message": "Do not retry blindly; creation may have succeeded."}))
        return metadata
    metadata["task_id"] = result["id"]
    metadata["status"] = "queued"
    save(metadata)
    print(json.dumps({"task_id": metadata["task_id"], "status": "queued", "model": MODEL}))
    return metadata


def poll(key: str) -> dict:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    if metadata.get("status") == "succeeded" and OUTPUT.exists() and hashlib.sha256(OUTPUT.read_bytes()).hexdigest() == metadata.get("sha256"):
        print(json.dumps({"status": "succeeded", "asset": metadata.get("asset"), "bytes": metadata.get("bytes"), "already_downloaded": True}))
        return metadata
    task_id = metadata.get("task_id")
    if not task_id:
        raise SystemExit("No created task to poll.")
    try:
        result = request(key, "/contents/generations/tasks/" + urllib.parse.quote(task_id, safe=""))
    except urllib.error.HTTPError as exc:
        error = safe_http_error(exc, key)
        print(json.dumps({"status": "poll_failed", "error": error}, ensure_ascii=False))
        return metadata
    metadata["status"] = result.get("status", "unknown")
    metadata["actual_model"] = result.get("model")
    for field in ("resolution", "ratio", "duration", "framespersecond", "generate_audio", "seed"):
        if field in result:
            metadata.setdefault("actual_output", {})[field] = result[field]
    metadata["usage"] = {name: value for name, value in result.get("usage", {}).items() if isinstance(value, (int, float))}
    if result.get("error"):
        metadata["error"] = {name: clean(result["error"].get(name, ""), key) for name in ("code", "message")}
    if metadata["status"] == "succeeded":
        if result.get("model") != MODEL:
            metadata["status"] = "model_mismatch"
            save(metadata)
            raise SystemExit("Provider returned an unexpected model; refusing to label it Seedance 2.5.")
        url = result.get("content", {}).get("video_url", "")
        if urllib.parse.urlparse(url).scheme != "https":
            raise SystemExit("Provider did not return an HTTPS video artifact.")
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        temporary = OUTPUT.with_suffix(".mp4.part")
        total = 0
        with urllib.request.urlopen(url, timeout=90) as response, temporary.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 150 * 1024 * 1024:
                    raise SystemExit("Output exceeded the permitted 150MB download size.")
                target.write(chunk)
        if b"ftyp" not in temporary.read_bytes()[:32]:
            raise SystemExit("Downloaded artifact is not an MP4 container.")
        temporary.replace(OUTPUT)
        metadata["asset"] = str(OUTPUT.relative_to(ROOT)).replace("\\", "/")
        metadata["bytes"] = OUTPUT.stat().st_size
        metadata["sha256"] = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    save(metadata)
    print(json.dumps({name: metadata.get(name) for name in ("task_id", "status", "actual_model", "actual_output", "asset", "bytes", "error")}, ensure_ascii=False))
    return metadata


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("submit", "poll"))
    parser.add_argument("--credentials-file", type=Path)
    parser.add_argument("--image", type=Path, default=ROOT / "apps/web/public/media/packaging-hero.webp")
    parser.add_argument("--retry", action="store_true")
    args = parser.parse_args()
    token = credentials(args.credentials_file)
    try:
        if args.operation == "submit":
            submit(token, args.image.resolve(), args.retry)
        else:
            poll(token)
    except Exception as exc:
        # A signed artifact URL must not appear in a traceback or CLI output.
        print(json.dumps({"status": "operation_failed", "code": type(exc).__name__, "message": clean(exc, token)}, ensure_ascii=False))
        raise SystemExit(1) from None
