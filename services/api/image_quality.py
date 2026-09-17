"""Free deterministic image derivatives. Preview never changes a project scene."""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import json
from math import ceil
from typing import Literal
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Request
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update

from .auth import require_auth
from .business import owned_record
from .database import utcnow
from .errors import APIError
from .editor_sessions import enforce_edit_lease
from .geometry import validate_scene
from .image_quality_metadata import image_quality_metrics
from .image_crop import crop_pixels
from .models import Asset, Project, Tenant
from .feature_models import UploadSession
from .schemas import Scene, FaceId, Identifier
from .uploads import MAX_BYTES, QUOTA_BYTES

MAX_PIXELS = 40_000_000


class InspectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    base_revision: int = Field(ge=1)
    face_id: FaceId
    object_id: Identifier
    target_ppi: int = Field(default=300, ge=72, le=600, strict=True)


class PreviewBody(InspectBody):
    operation_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    resample: bool = Field(default=True, strict=True)
    bleed_mode: Literal["none", "edge", "mirror"] = "none"


def bleed_missing(face, obj):
    if abs(obj.get("rotation_deg", 0)) % 360 > 1e-9:
        return None
    x, y, w, h = (obj[key] for key in ("x_mm", "y_mm", "width_mm", "height_mm"))
    return {"left": round(max(0, x + 3), 4) if x <= 0 else 0,
            "top": round(max(0, y + 3), 4) if y <= 0 else 0,
            "right": round(max(0, face["width_mm"] + 3 - x - w), 4) if x + w >= face["width_mm"] - 1e-7 else 0,
            "bottom": round(max(0, face["height_mm"] + 3 - y - h), 4) if y + h >= face["height_mm"] - 1e-7 else 0}


def inspect_image(face, obj, asset, raw, target_ppi):
    digest = sha256(raw).hexdigest()
    known_hash = (asset.metadata_json or {}).get("sha256")
    if known_hash and known_hash != digest:
        raise APIError(422, "SOURCE_HASH_MISMATCH", "원본 이미지 무결성을 확인하지 못했습니다.")
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > MAX_PIXELS:
                raise ValueError()
            if image.size != (asset.width_px, asset.height_px):
                raise ValueError()
            image.load()
            pixels = ImageOps.exif_transpose(image).size
    except Exception:
        raise APIError(422, "ASSET_INVALID", "원본 이미지의 크기와 파일 상태를 확인해 주세요.") from None
    metrics = image_quality_metrics(pixels, obj, asset.metadata_json)
    warnings = []
    if metrics["original_effective_ppi"] + 1e-7 < target_ppi:
        warnings.append({"code": "ORIGINAL_LOW_PPI", "message": "픽셀 확대는 원본 디테일을 복원하지 않습니다. 원본 해상도 부족은 계속 표시됩니다."})
    if metrics["extended"]:
        warnings.append({"code": "SYNTHETIC_BLEED", "message": "가장자리 반복·반사로 만든 영역입니다. 경계와 투명도를 미리보기에서 확인해 주세요."})
    missing = bleed_missing(face, obj)
    if missing is None:
        warnings.append({"code": "ROTATED_BLEED_UNSUPPORTED", "message": "회전된 이미지는 회전을 해제한 뒤 도련을 보완해 주세요."})
    elif any(missing.values()):
        warnings.append({"code": "ARTWORK_BLEED_MISSING", "message": "재단선에 닿는 이미지가 3mm 도련 끝까지 이어지지 않습니다."})
    return {"source": {"id": asset.id, "sha256": digest, "width_px": pixels[0], "height_px": pixels[1]},
            "placed_mm": {key: obj[key] for key in ("x_mm", "y_mm", "width_mm", "height_mm")},
            "effective_ppi": metrics["effective_ppi"], "original_effective_ppi": metrics["original_effective_ppi"],
            "crop": obj.get("crop"), "visible_pixels": metrics["visible_pixels"],
            "resampled": metrics["resampled"], "extended": metrics["extended"],
            "bleed_missing_mm": missing, "target_ppi": target_ppi,
            "required_pixels": {"width": ceil(obj["width_mm"] * target_ppi / 25.4), "height": ceil(obj["height_mm"] * target_ppi / 25.4)},
            "warnings": warnings, "detail_recovery_claimed": False}


def _pad_axis(image, before, after, horizontal, mode):
    """Symmetric reflection repeats boundary pixels; edge mode repeats one pixel."""
    if not before and not after:
        return image
    length = image.width if horizontal else image.height
    out = Image.new(image.mode, (image.width + before + after, image.height) if horizontal else (image.width, image.height + before + after))
    out.paste(image, (before, 0) if horizontal else (0, before))
    for count, start, reverse in ((before, 0, True), (after, before + length, False)):
        if not count:
            continue
        if mode == "edge":
            edge = image.crop((0 if reverse else image.width-1, 0, 1 if reverse else image.width, image.height)) if horizontal else image.crop((0, 0 if reverse else image.height-1, image.width, 1 if reverse else image.height))
            out.paste(edge.resize((count, image.height) if horizontal else (image.width, count), Image.Resampling.NEAREST), (start, 0) if horizontal else (0, start))
            continue
        mirrored = ImageOps.mirror(image) if horizontal else ImageOps.flip(image)
        # Work from the original image boundary outwards; crop partial far tiles.
        filled = 0
        while filled < count:
            tile = mirrored if (filled // length) % 2 == 0 else image
            take = min(length, count - filled)
            tile_start = length - take if reverse else 0
            crop = tile.crop((tile_start, 0, tile_start + take, image.height)) if horizontal else tile.crop((0, tile_start, image.width, tile_start + take))
            offset = before - filled - take if reverse else start + filled
            out.paste(crop, (offset, 0) if horizontal else (0, offset))
            filled += take
    return out


def make_derivative(raw, face, obj, metadata, *, target_ppi=300, resample=True, bleed_mode="none"):
    missing = bleed_missing(face, obj)
    if bleed_mode != "none" and missing is None:
        raise APIError(422, "ROTATED_BLEED_UNSUPPORTED", "회전된 이미지는 픽셀 확대만 지원합니다. 도련 보완 전 회전을 해제해 주세요.")
    pads = missing if bleed_mode != "none" else None
    pads = pads or {key: 0 for key in ("left", "right", "top", "bottom")}
    patch = {"x_mm": round(obj["x_mm"]-pads["left"], 4), "y_mm": round(obj["y_mm"]-pads["top"], 4),
             "width_mm": round(obj["width_mm"]+pads["left"]+pads["right"], 4), "height_mm": round(obj["height_mm"]+pads["top"]+pads["bottom"], 4)}
    with Image.open(BytesIO(raw)) as source:
        if source.mode not in {"RGB", "RGBA", "L", "LA", "P"}:
            raise APIError(422, "IMAGE_COLOR_SPACE_UNSUPPORTED", "이 보완 도구는 RGB 이미지만 지원합니다. CMYK 색상 변환은 제조사 프로필이 필요합니다.")
        if source.getexif().get(274, 1) != 1:
            raise APIError(422, "IMAGE_ORIENTATION_REQUIRED", "회전 메타데이터가 없는 이미지로 저장해 다시 업로드해 주세요.")
        source.load()
        source_pixels = source.size
        crop_x, crop_y, width, height = crop_pixels(source_pixels, obj)
        target_w = max(ceil(width), ceil(obj["width_mm"]*target_ppi/25.4)) if resample else ceil(width)
        target_h = max(ceil(height), ceil(obj["height_mm"]*target_ppi/25.4)) if resample else ceil(height)
        padding = {key: ceil(value * (target_w/obj["width_mm"] if key in {"left", "right"} else target_h/obj["height_mm"])) for key, value in pads.items()}
        total_w, total_h = target_w+padding["left"]+padding["right"], target_h+padding["top"]+padding["bottom"]
        if total_w * total_h > MAX_PIXELS or max(total_w, total_h) > 40_000:
            raise APIError(422, "IMAGE_PIXEL_LIMIT", "보완 결과가 4천만 픽셀 한도를 초과합니다. 목표 ppi를 낮춰 주세요.")
        if target_w == width and target_h == height and not any(padding.values()):
            raise APIError(422, "IMAGE_QUALITY_NO_CHANGE", "이미 목표 픽셀 크기와 선택한 도련 조건을 충족합니다.")
        image = source.convert("RGBA" if source.mode in {"RGBA", "LA", "P"} else "RGB")
        source_box = (crop_x, crop_y, crop_x+width, crop_y+height)
        if (target_w, target_h) == (width, height) and all(value == int(value) for value in source_box):
            image = image.crop(tuple(int(value) for value in source_box))
        else:
            image = image.resize((target_w, target_h), Image.Resampling.LANCZOS, box=source_box)
        image = _pad_axis(image, padding["left"], padding["right"], True, bleed_mode)
        image = _pad_axis(image, padding["top"], padding["bottom"], False, bleed_mode)
        output = BytesIO()
        image.save(output, format="PNG", icc_profile=source.info.get("icc_profile"), compress_level=6)
    result = output.getvalue()
    if len(result) > MAX_BYTES:
        raise APIError(422, "IMAGE_BYTE_LIMIT", "보완 이미지가 20MiB 한도를 초과합니다. 목표 ppi를 낮춰 주세요.")
    previous = (metadata or {}).get("image_quality") or {}
    native = crop_pixels(previous.get("native_equivalent_pixels", source_pixels), obj)[2:]
    source_quality = image_quality_metrics(source_pixels, obj, metadata)
    root_rect = previous.get("original_content_rect_px", [0, 0, *source_pixels])
    left, top = max(crop_x, root_rect[0]), max(crop_y, root_rect[1])
    right, bottom = min(crop_x+width, root_rect[0]+root_rect[2]), min(crop_y+height, root_rect[1]+root_rect[3])
    root_rect = [max(0, left-crop_x), max(0, top-crop_y), max(0, right-left), max(0, bottom-top)]
    provenance = {"version": 1, "algorithm": "pillow-lanczos-edge-symmetric-v1", "detail_recovery_claimed": False,
                  "resampled": bool(previous.get("resampled")) or (target_w, target_h) != (width, height),
                  "extended": bool(previous.get("extended")) or any(padding.values()),
                  "native_equivalent_pixels": [native[0]*patch["width_mm"]/obj["width_mm"], native[1]*patch["height_mm"]/obj["height_mm"]],
                  "parent_pixels": list(source_pixels), "output_pixels": [total_w, total_h],
                  "source_crop": obj.get("crop"), "source_visible_pixels": [width, height],
                  "source_effective_ppi": source_quality["effective_ppi"],
                  "source_original_effective_ppi": source_quality["original_effective_ppi"],
                  "target_ppi": target_ppi, "bleed_mode": bleed_mode, "extended_mm": pads,
                  "extended_pixels": padding, "source_content_rect_px": [padding["left"], padding["top"], target_w, target_h],
                  "original_content_rect_px": [padding["left"]+root_rect[0]*target_w/width,
                      padding["top"]+root_rect[1]*target_h/height, root_rect[2]*target_w/width, root_rect[3]*target_h/height],
                  "root_source_asset_id": previous.get("root_source_asset_id", obj["asset_id"]),
                  "root_source_sha256": previous.get("root_source_sha256", sha256(raw).hexdigest()),
                  "original_source_pixels": previous.get("original_source_pixels", list(source_pixels)),
                  "parent_asset_id": obj["asset_id"], "parent_sha256": sha256(raw).hexdigest(),
                  "parent_placed_mm": {key: obj[key] for key in patch}, "output_placed_mm": patch}
    if obj.get("crop") is not None:
        # The derivative contains this visible region; applying the old crop again would crop twice.
        patch = {**patch, "crop": None}
    return result, patch, provenance


def install_image_quality_routes(app, db_session, asset_payload):
    router = APIRouter(prefix="/v1/image-quality", tags=["image-quality"])
    storage = app.state.storage

    def context(body, db, user, lock=False):
        project = owned_record(db, Project, body.project_id, user.tenant_id)
        if lock:
            won = db.execute(update(Project).where(Project.id==project.id, Project.base_revision==body.base_revision).values(base_revision=body.base_revision).execution_options(synchronize_session=False))
            if won.rowcount != 1:
                raise APIError(409, "REVISION_CONFLICT", "프로젝트가 변경되었습니다. 저장 후 다시 진단해 주세요.")
            db.refresh(project)
        if project.base_revision != body.base_revision:
            raise APIError(409, "REVISION_CONFLICT", "프로젝트가 변경되었습니다. 저장 후 다시 진단해 주세요.")
        scene = validate_scene(Scene.model_validate(project.scene).model_dump(mode="json"))
        face = next((face for face in scene["faces"] if face["id"] == body.face_id), None)
        obj = next((obj for obj in face["objects"] if obj["id"] == body.object_id), None) if face else None
        if not obj or obj["type"] != "image":
            raise APIError(422, "IMAGE_OBJECT_REQUIRED", "저장된 이미지 객체를 선택해 주세요.")
        asset = owned_record(db, Asset, obj["asset_id"], user.tenant_id)
        return project, scene, face, obj, asset

    def read(asset):
        try:
            return storage.get_limited(asset.storage_key, MAX_BYTES)
        except Exception:
            raise APIError(422, "ASSET_UNAVAILABLE", "원본 이미지 파일을 읽을 수 없습니다.") from None

    def identity(body):
        return {"base_revision": body.base_revision, "face_id": body.face_id, "object_id": body.object_id}

    @router.post("/inspect")
    def inspect(body: InspectBody, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True, authorize_write=False)
        _, _, face, obj, asset = context(body, db, user)
        quality = inspect_image(face, obj, asset, read(asset), body.target_ppi)
        return {"data": {**identity(body), **quality}, "request_id": request.state.request_id}

    @router.post("/preview", status_code=201)
    def preview(body: PreviewBody, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        # Serializes idempotency, quota and processing load for this tenant on SQLite and PostgreSQL.
        db.execute(update(Tenant).where(Tenant.id==user.tenant_id).values(name=Tenant.name).execution_options(synchronize_session=False))
        project = owned_record(db, Project, body.project_id, user.tenant_id)
        enforce_edit_lease(db, project, request)
        project, scene, face, obj, source = context(body, db, user, lock=True)
        derivative_id = str(uuid5(NAMESPACE_URL, "phoenix:image-quality:"+user.tenant_id+":"+body.operation_key))
        request_hash = sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        existing = db.get(Asset, derivative_id)
        if existing:
            existing = owned_record(db, Asset, derivative_id, user.tenant_id)
            if existing.metadata_json.get("quality_request_hash") != request_hash:
                raise APIError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 식별자로 다른 보완 작업을 보낼 수 없습니다.")
            return {"data": {**existing.metadata_json["quality_response"], "asset": asset_payload(existing)}, "request_id": request.state.request_id}
        count = db.scalar(select(func.count()).select_from(Asset).where(Asset.tenant_id==user.tenant_id, Asset.source=="image_quality", Asset.created_at>utcnow()-timedelta(hours=1)))
        if count >= 20:
            raise APIError(429, "IMAGE_QUALITY_RATE_LIMIT", "이미지 보완 요청이 많습니다. 잠시 후 다시 시도해 주세요.")
        raw = read(source)
        before = inspect_image(face, obj, source, raw, body.target_ppi)
        if before["source"]["sha256"] != body.source_sha256:
            raise APIError(409, "SOURCE_HASH_MISMATCH", "원본이 변경되었습니다. 다시 진단해 주세요.")
        result, patch, provenance = make_derivative(raw, face, obj, source.metadata_json,
            target_ppi=body.target_ppi, resample=body.resample, bleed_mode=body.bleed_mode)
        patch = {"asset_id": derivative_id, **patch}
        candidate = deepcopy(scene)
        next(o for f in candidate["faces"] for o in f["objects"] if o["id"]==body.object_id).update(patch)
        validate_scene(candidate)
        used = db.scalar(select(func.coalesce(func.sum(Asset.byte_size), 0)).where(Asset.tenant_id==user.tenant_id))
        pending = db.scalar(select(func.coalesce(func.sum(UploadSession.byte_size), 0)).where(
            UploadSession.tenant_id==user.tenant_id, UploadSession.status=="pending", UploadSession.expires_at>utcnow()))
        if used + pending + len(result) > QUOTA_BYTES:
            raise APIError(422, "ASSET_QUOTA_EXCEEDED", "작업 공간의 200MiB 저장 한도를 초과했습니다.")
        key = f"{user.tenant_id}/assets/{derivative_id}"
        derivative = Asset(id=derivative_id, tenant_id=user.tenant_id, workspace_id=project.workspace_id,
            storage_key=key, original_name="quality-preview.png", content_type="image/png", byte_size=len(result),
            width_px=provenance["output_pixels"][0], height_px=provenance["output_pixels"][1], source="image_quality",
            metadata_json={"sha256": sha256(result).hexdigest(), "image_quality": provenance, "quality_request_hash": request_hash})
        quality = inspect_image(face, {**obj, **patch}, derivative, result, body.target_ppi)
        response = {**identity(body), "source": before["source"], "patch": patch,
                    "quality": quality, "provenance": provenance, "credits_charged": 0}
        derivative.metadata_json = {**derivative.metadata_json, "quality_response": response}
        storage.put(key, result, "image/png")
        if sha256(storage.get_limited(key, MAX_BYTES)).hexdigest() != derivative.metadata_json["sha256"]:
            raise APIError(503, "STORAGE_VERIFICATION_FAILED", "보완 이미지 저장을 확인하지 못했습니다.")
        db.add(derivative)
        db.commit()
        return {"data": {**response, "asset": asset_payload(derivative)}, "request_id": request.state.request_id}

    app.include_router(router)
