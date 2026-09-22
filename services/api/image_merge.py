"""Flatten stacked image layers into one opaque image.

A cut-out layer carries an alpha channel, which the CMYK production adapter rejects
(``PRINT_TRANSPARENCY_UNSUPPORTED``). Merging composites the selected layers exactly the
way the review PDF paints them and stores the result as one opaque asset. Free, deterministic,
no AI call: the same selection on the same revision always yields the same pixels.
"""
from .contracts.base import Envelope, ERROR_RESPONSES
from .contracts import images as I
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import json
from math import ceil, cos, radians, sin
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Request
from PIL import Image, ImageColor, ImageOps
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update

from .auth import require_auth
from .business import owned_record
from .database import utcnow
from .errors import APIError
from .editor_sessions import enforce_edit_lease
from .geometry import validate_scene
from .geometry.validation import _corners
from .image_crop import crop_pixels
from .models import Asset, Project, Tenant
from .feature_models import UploadSession
from .schemas import Scene, FaceId, Identifier
from .uploads import MAX_BYTES, QUOTA_BYTES

MAX_SOURCE_PIXELS = 40_000_000
MAX_OUTPUT_PIXELS = 30_000_000
MAX_OUTPUT_EDGE = 20_000
MAX_LAYERS = 12


class MergeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    base_revision: int = Field(ge=1)
    face_id: FaceId
    object_ids: list[Identifier] = Field(min_length=2, max_length=MAX_LAYERS)
    operation_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    target_ppi: int = Field(default=300, ge=72, le=600, strict=True)


def placed_bounds(objects):
    points = [point for obj in objects for point in _corners(obj)]
    return (min(x for x, _ in points), min(y for _, y in points),
            max(x for x, _ in points), max(y for _, y in points))


def _layer_bitmap(raw, obj, scale):
    """The object's pixels as the review PDF paints them: cropped, placed-size, opacity applied."""
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"} or source.width * source.height > MAX_SOURCE_PIXELS:
                raise ValueError()
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGBA")
    except Exception:
        raise APIError(422, "ASSET_INVALID", "레이어 원본 이미지의 형식이나 크기를 확인해 주세요.") from None
    crop_x, crop_y, crop_w, crop_h = crop_pixels(image.size, obj)
    width = max(1, round(obj["width_mm"] * scale))
    height = max(1, round(obj["height_mm"] * scale))
    image = image.resize((width, height), Image.Resampling.LANCZOS,
                         box=(crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
    if obj["opacity"] < 1:
        image.putalpha(image.getchannel("A").point(lambda value: round(value * obj["opacity"])))
    return image


def _composite(canvas, layer, left, top):
    """Alpha-composite `layer` at (left, top), clipped to the canvas, without a full-size copy."""
    x0, y0 = int(round(left)), int(round(top))
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(canvas.width, x0 + layer.width), min(canvas.height, y0 + layer.height)
    if cx0 >= cx1 or cy0 >= cy1:
        return
    part = layer.crop((cx0 - x0, cy0 - y0, cx1 - x0, cy1 - y0))
    canvas.paste(Image.alpha_composite(canvas.crop((cx0, cy0, cx1, cy1)), part), (cx0, cy0))


def merge_layers(objects, sources, background, target_ppi, *, opaque_only=False):
    """Composite bottom-to-top into one bitmap covering the union of the placed layers.

    `opaque_only` means other art sits under this stack, so filling the gaps with the face
    colour would hide it; the caller must add the covering layer to the selection instead.
    Returns (png_bytes, pixels, effective_ppi, background_filled)."""
    left, top, right, bottom = placed_bounds(objects)
    width_mm, height_mm = right - left, bottom - top
    scale = target_ppi / 25.4
    width, height = max(1, ceil(width_mm * scale - 1e-9)), max(1, ceil(height_mm * scale - 1e-9))
    if width * height > MAX_OUTPUT_PIXELS or max(width, height) > MAX_OUTPUT_EDGE:
        raise APIError(422, "IMAGE_PIXEL_LIMIT", "병합 결과가 픽셀 한도를 초과합니다. 목표 ppi를 낮춰 주세요.")
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for obj, raw in zip(objects, sources):
        bitmap = _layer_bitmap(raw, obj, scale)
        angle = obj["rotation_deg"] % 360
        if abs(angle) < 1e-9:
            _composite(canvas, bitmap, (obj["x_mm"] - left) * scale, (obj["y_mm"] - top) * scale)
            continue
        # The scene rotates around the object's top-left corner; PIL rotates around the centre.
        radian = radians(obj["rotation_deg"])
        cosine, sine = cos(radian), sin(radian)
        half_x, half_y = bitmap.width / 2, bitmap.height / 2
        moved_x, moved_y = half_x * cosine - half_y * sine, half_x * sine + half_y * cosine
        rotated = bitmap.rotate(-obj["rotation_deg"], resample=Image.Resampling.BICUBIC, expand=True)
        _composite(canvas, rotated,
                   (obj["x_mm"] - left) * scale + moved_x - rotated.width / 2,
                   (obj["y_mm"] - top) * scale + moved_y - rotated.height / 2)
    filled = canvas.getchannel("A").getextrema() != (255, 255)
    if filled and opaque_only:
        raise APIError(422, "MERGE_LAYERS_BELOW", "병합 결과에 투명한 부분이 남고 그 아래에 다른 레이어가 있습니다. 아래 레이어도 함께 선택해 주세요.")
    if filled:
        # Nothing of the customer's own art sits under this stack, so the face colour is what
        # the printed sheet shows there. Flattening onto it is what makes the result printable.
        base = Image.new("RGBA", canvas.size, ImageColor.getrgb(background) + (255,))
        canvas = Image.alpha_composite(base, canvas)
    result = BytesIO()
    canvas.convert("RGB").save(result, format="PNG", optimize=True)
    effective_ppi = min(width / width_mm, height / height_mm) * 25.4
    return result.getvalue(), (width, height), round(effective_ppi, 4), filled


def install_image_merge_routes(app, db_session, asset_payload):
    router = APIRouter(prefix="/v1/image-quality", tags=["image-quality"])
    storage = app.state.storage

    @router.post("/merge", status_code=201, response_model=Envelope[I.ImageMergeResult], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def merge(body: MergeBody, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        db.execute(update(Tenant).where(Tenant.id == user.tenant_id).values(name=Tenant.name).execution_options(synchronize_session=False))
        project = owned_record(db, Project, body.project_id, user.tenant_id)
        enforce_edit_lease(db, project, request)
        won = db.execute(update(Project).where(Project.id == project.id, Project.base_revision == body.base_revision)
                         .values(base_revision=body.base_revision).execution_options(synchronize_session=False))
        if won.rowcount != 1:
            raise APIError(409, "REVISION_CONFLICT", "프로젝트가 변경되었습니다. 저장 후 다시 병합해 주세요.")
        db.refresh(project)
        scene = validate_scene(Scene.model_validate(project.scene).model_dump(mode="json"), structure_snapshot=getattr(project, "structure_snapshot", None))
        face = next((face for face in scene["faces"] if face["id"] == body.face_id), None)
        if face is None:
            raise APIError(422, "FACE_NOT_FOUND", "면을 찾을 수 없습니다.")
        if len(set(body.object_ids)) != len(body.object_ids):
            raise APIError(422, "MERGE_DUPLICATE_LAYER", "같은 레이어를 두 번 선택할 수 없습니다.")
        by_id = {obj["id"]: obj for obj in face["objects"]}
        chosen = [by_id.get(object_id) for object_id in body.object_ids]
        if any(obj is None or obj["type"] != "image" for obj in chosen):
            raise APIError(422, "IMAGE_OBJECT_REQUIRED", "이 면에 저장된 이미지 레이어만 병합할 수 있습니다.")
        if any(obj["locked"] for obj in chosen):
            raise APIError(422, "LAYER_LOCKED", "잠긴 레이어는 병합할 수 없습니다. 먼저 잠금을 풀어 주세요.")
        if any(not obj["visible"] or not obj["print_enabled"] for obj in chosen):
            raise APIError(422, "LAYER_NOT_PRINTED", "숨김이거나 인쇄에서 제외된 레이어는 병합할 수 없습니다.")

        order = {obj["id"]: index for index, obj in enumerate(face["objects"])}
        chosen.sort(key=lambda obj: (obj["z_index"], order[obj["id"]]))
        lowest, highest = chosen[0], chosen[-1]
        span = (chosen[0]["z_index"], order[chosen[0]["id"]]), (chosen[-1]["z_index"], order[chosen[-1]["id"]])
        box = placed_bounds(chosen)
        selected = {obj["id"] for obj in chosen}
        covered = False
        for other in face["objects"]:
            if other["id"] in selected or not other["visible"] or not other["print_enabled"]:
                continue
            other_box = placed_bounds([other])
            if not (box[0] < other_box[2] and other_box[0] < box[2] and box[1] < other_box[3] and other_box[1] < box[3]):
                continue
            rank = (other["z_index"], order[other["id"]])
            if span[0] < rank < span[1]:
                raise APIError(422, "MERGE_LAYERS_BETWEEN", "선택한 레이어 사이에 다른 레이어가 겹쳐 있습니다. 그 레이어도 함께 선택해 주세요.")
            covered = covered or rank < span[0]

        assets = [owned_record(db, Asset, obj["asset_id"], user.tenant_id) for obj in chosen]
        merged_id = str(uuid5(NAMESPACE_URL, "phoenix:image-merge:" + user.tenant_id + ":" + body.operation_key))
        request_hash = sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        existing = db.get(Asset, merged_id)
        if existing:
            existing = owned_record(db, Asset, merged_id, user.tenant_id)
            if existing.metadata_json.get("merge_request_hash") != request_hash:
                raise APIError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 식별자로 다른 병합을 보낼 수 없습니다.")
            return {"data": {**existing.metadata_json["merge_response"], "asset": asset_payload(existing)}, "request_id": request.state.request_id}
        count = db.scalar(select(func.count()).select_from(Asset).where(
            Asset.tenant_id == user.tenant_id, Asset.source == "image_merge", Asset.created_at > utcnow() - timedelta(hours=1)))
        if count >= 20:
            raise APIError(429, "IMAGE_MERGE_RATE_LIMIT", "레이어 병합 요청이 많습니다. 잠시 후 다시 시도해 주세요.")

        sources = []
        for asset in assets:
            try:
                sources.append(storage.get_limited(asset.storage_key, MAX_BYTES))
            except Exception:
                raise APIError(422, "ASSET_UNAVAILABLE", "레이어 원본 이미지 파일을 읽을 수 없습니다.") from None
        result, pixels, effective_ppi, background_filled = merge_layers(chosen, sources, face["background"], body.target_ppi, opaque_only=covered)

        patch = {"asset_id": merged_id, "x_mm": round(box[0], 4), "y_mm": round(box[1], 4),
                 "width_mm": round(box[2] - box[0], 4), "height_mm": round(box[3] - box[1], 4),
                 "rotation_deg": 0.0, "z_index": highest["z_index"], "opacity": 1.0}
        removed = [obj["id"] for obj in chosen if obj["id"] != lowest["id"]]
        candidate = deepcopy(scene)
        target = next(f for f in candidate["faces"] if f["id"] == face["id"])
        target["objects"] = [{**obj, **patch, "crop": None} if obj["id"] == lowest["id"] else obj
                             for obj in target["objects"] if obj["id"] not in removed]
        validate_scene(candidate, structure_snapshot=getattr(project, "structure_snapshot", None))

        from .retention.deletion import available_asset_clause
        used = db.scalar(select(func.coalesce(func.sum(Asset.byte_size), 0)).where(Asset.tenant_id == user.tenant_id, available_asset_clause(include_deleting=True)))
        pending = db.scalar(select(func.coalesce(func.sum(UploadSession.byte_size), 0)).where(
            UploadSession.tenant_id == user.tenant_id, UploadSession.status == "pending", UploadSession.expires_at > utcnow()))
        if used + pending + len(result) > QUOTA_BYTES:
            raise APIError(422, "ASSET_QUOTA_EXCEEDED", "작업 공간의 200MiB 저장 한도를 초과했습니다.")

        key = f"{user.tenant_id}/assets/{merged_id}"
        asset = Asset(id=merged_id, tenant_id=user.tenant_id, workspace_id=project.workspace_id,
                      storage_key=key, original_name="merged-layers.png", content_type="image/png", byte_size=len(result),
                      width_px=pixels[0], height_px=pixels[1], source="image_merge",
                      metadata_json={"sha256": sha256(result).hexdigest(), "merge_request_hash": request_hash, "has_alpha": False,
                                     "image_merge": {"source_asset_ids": [item.id for item in assets], "object_ids": [obj["id"] for obj in chosen],
                                                     "face_id": face["id"], "target_ppi": body.target_ppi,
                                                     "background": face["background"], "background_filled": background_filled}})
        response = {"base_revision": body.base_revision, "face_id": face["id"], "object_ids": [obj["id"] for obj in chosen],
                    "keep_object_id": lowest["id"], "remove_object_ids": removed, "patch": patch,
                    "output_pixels": list(pixels), "effective_ppi": effective_ppi,
                    "background_filled": background_filled, "credits_charged": 0}
        asset.metadata_json = {**asset.metadata_json, "merge_response": response}
        storage.put(key, result, "image/png")
        if sha256(storage.get_limited(key, MAX_BYTES)).hexdigest() != asset.metadata_json["sha256"]:
            raise APIError(503, "STORAGE_VERIFICATION_FAILED", "병합 이미지 저장을 확인하지 못했습니다.")
        db.add(asset)
        db.commit()
        return {"data": {**response, "asset": asset_payload(asset)}, "request_id": request.state.request_id}

    app.include_router(router)
