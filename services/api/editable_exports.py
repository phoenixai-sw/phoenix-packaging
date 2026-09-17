"""Free, private, immutable editable archives. No rendering or rights expansion."""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from PIL import Image
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from .asset_reconciliation import ensure_asset_available
from .billing.payments import enforce_membership_entitlement
from .billing.service import canonical_hash, lock_wallet
from .business import owned_record, workspace_access
from .config import ROOT
from .database import utcnow
from .editor_sessions import enforce_edit_lease
from .errors import APIError
from .feature_models import AuditEvent, Membership, WorkspaceMember
from .models import Asset, Job, Project, User
from .storage import validate_key

FORMAT_VERSION = "1.0"
BUNDLE_VERSION = "phoenix-editable-v1"
EXPORT_KINDS = ("review_export", "production_export", "editable_export")
MAX_ASSETS = 2000
MAX_ASSET_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 32 * 1024 * 1024
RIGHTS_NOTICE = "원본 이미지·문구의 권리는 기존 권리자에게 있습니다. 이 다운로드는 추가 이용·재배포 권리나 제조 승인을 부여하지 않습니다. 포함 글꼴에는 동봉한 SIL OFL 1.1이 적용됩니다."
FONT_FILES = {
    400: ("NotoSansKR-Regular.ttf", "8e4000a13809588d46c1b791e874cd4567b6283eeb9d2e6835a3136a871a6bd0"),
    700: ("NotoSansKR-Bold.ttf", "f83cb7d28cc6c5ab36629da7bbed2d925f4c740665d0ae0de7455dadd9630efc"),
}
MIME_EXT = {"image/png": ("png", "PNG"), "image/jpeg": ("jpg", "JPEG"), "image/webp": ("webp", "WEBP")}


def _json(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_JSON_BYTES:
        raise APIError(422, "EDITABLE_SIZE_LIMIT", "편집 자료의 JSON 크기 한도를 초과했습니다.")
    return raw


def _asset_identity(asset):
    return {"id": asset.id, "storage_key": asset.storage_key, "content_type": asset.content_type,
            "byte_size": asset.byte_size, "width_px": asset.width_px, "height_px": asset.height_px,
            "sha256": (asset.metadata_json or {}).get("sha256")}


def _lineage(metadata):
    quality = metadata.get("image_quality") or {}
    return {str(value) for value in (metadata.get("reference_asset_id"), quality.get("root_source_asset_id"), quality.get("parent_asset_id")) if value}


def _safe_metadata(metadata):
    # Prompts, provider responses, URLs, actor/session IDs and storage keys are
    # deliberately excluded. Quality provenance is server-authored geometry.
    keys = ("image_quality", "model", "requested_quality", "actual_quality", "actual_size", "output_size",
            "reference_asset_id", "edit_mode", "edit_region", "edit_pixel_box", "preservation_scope")
    return {key: deepcopy(metadata[key]) for key in keys if key in metadata}


def _font_inventory(scene):
    weights = {400}
    for face in scene["faces"]:
        for obj in face["objects"]:
            if obj.get("type") != "text":
                continue
            weight = obj.get("font_weight", 400)
            if obj.get("font_id", "NotoSansKR") != "NotoSansKR" or type(weight) is not int or weight not in FONT_FILES:
                raise APIError(422, "EDITABLE_FONT_UNSUPPORTED", "재배포가 확인된 NotoSansKR 400/700 글꼴만 묶을 수 있습니다.")
            weights.add(weight)
    return [{"id": "NotoSansKR", "weight": weight, "path": "fonts/" + FONT_FILES[weight][0],
             "sha256": FONT_FILES[weight][1], "license": "OFL-1.1"} for weight in sorted(weights)]


def _inventory(db, user, scene):
    placed = {str(obj["asset_id"]) for face in scene["faces"] for obj in face["objects"] if obj.get("asset_id")}
    pending, rows, total = set(placed), {}, 0
    while pending:
        identity = pending.pop()
        if identity in rows:
            continue
        try:
            if str(UUID(identity)) != identity:
                raise ValueError()
        except (ValueError, TypeError):
            raise APIError(422, "EDITABLE_ASSET_INVALID", "원본 자산 식별자가 올바르지 않습니다.") from None
        asset = owned_record(db, Asset, identity, user.tenant_id)
        ensure_asset_available(asset)
        validate_key(asset.storage_key)
        if not asset.storage_key.startswith(user.tenant_id + "/") or asset.content_type not in MIME_EXT:
            raise APIError(422, "EDITABLE_ASSET_INVALID", "원본 자산의 저장 위치 또는 파일 형식을 확인해 주세요.")
        if not 0 < asset.byte_size <= MAX_ASSET_BYTES:
            raise APIError(422, "EDITABLE_SIZE_LIMIT", "원본 파일의 편집 묶음 크기 한도를 초과했습니다.")
        total += asset.byte_size
        if len(rows) >= MAX_ASSETS or total > MAX_BUNDLE_BYTES:
            raise APIError(422, "EDITABLE_SIZE_LIMIT", "편집 묶음은 원본 2,000개와 합계 256MiB 이하를 지원합니다.")
        metadata = asset.metadata_json or {}
        rows[identity] = {**_asset_identity(asset), "name": asset.original_name, "source": asset.source,
                          "role": "placed" if identity in placed else "source", "metadata": _safe_metadata(metadata)}
        pending.update(_lineage(metadata) - rows.keys())
    return [rows[key] for key in sorted(rows)]


def create_editable_export(db, user, body, request, project, project_payload, snapshot_revision):
    if body.quote_id is not None or body.reviewed_face_ids:
        raise APIError(422, "EDITABLE_INPUT_INVALID", "편집 자료는 제작 견적이나 인쇄 확인 없이 저장된 리비전으로 요청합니다.")
    operation = request.headers.get("idempotency-key", f"editable:{project.id}:{body.base_revision}:{BUNDLE_VERSION}")
    if not 1 <= len(operation) <= 160:
        raise APIError(422, "IDEMPOTENCY_KEY_INVALID", "요청 식별자가 올바르지 않습니다.")
    digest = canonical_hash({"project_id": project.id, "base_revision": body.base_revision, "kind": "editable", "bundle_version": BUNDLE_VERSION})

    def existing():
        job = db.scalar(select(Job).where(Job.tenant_id == user.tenant_id, Job.operation_key == operation))
        if job and job.request_hash != digest:
            raise APIError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 식별자로 다른 작업을 보낼 수 없습니다.")
        return job

    old = existing()
    if old:
        return old
    enforce_edit_lease(db, project, request)  # wallet -> project, locks through commit
    old = existing()
    if old:
        return old
    if project.base_revision != body.base_revision:
        raise APIError(409, "REVISION_CONFLICT", "디자인이 변경되었습니다. 저장 후 다시 다운로드해 주세요.")
    count = db.scalar(select(func.count()).select_from(Job).where(Job.tenant_id == user.tenant_id,
        Job.kind.in_(("review_export", "editable_export")), Job.created_at > utcnow() - timedelta(hours=1)))
    if count >= 30:
        raise APIError(429, "EXPORT_RATE_LIMIT", "무료 파일 요청이 많습니다. 잠시 후 다시 시도해 주세요.", retryable=True)
    snapshot = deepcopy(project_payload(project))
    # Forward compatible with the server-owned StructureDefinitionV2 snapshot.
    if getattr(project, "structure_snapshot", None):
        snapshot["structure_snapshot"] = deepcopy(project.structure_snapshot)
    assets, fonts = _inventory(db, user, snapshot["scene"]), _font_inventory(snapshot["scene"])
    snapshot.update({"actor_id": user.id, "bundle_version": BUNDLE_VERSION, "editable_assets": assets,
                     "editable_fonts": fonts, "rights_notice": RIGHTS_NOTICE})
    _json(snapshot)
    try:
        revision = snapshot_revision(db, project, "editable_export")
        snapshot["revision_id"] = revision.id
        job = Job(tenant_id=user.tenant_id, project_id=project.id, revision_id=revision.id, kind="editable_export",
                  operation_key=operation, request_hash=digest, snapshot=snapshot)
        db.add(job)
        db.flush()
        db.add(AuditEvent(tenant_id=user.tenant_id, actor_id=user.id, action="editable_export_requested", entity_id=job.id,
                         details={"revision_id": revision.id, "asset_count": len(assets), "rights_notice": RIGHTS_NOTICE, "credits_charged": 0}))
        db.commit()
        return job
    except IntegrityError:
        db.rollback()
        old = existing()
        if old:
            return old
        raise APIError(409, "EXPORT_CONFLICT", "요청을 다시 확인해 주세요.", retryable=True) from None


def _actor(db, tenant_id, actor_id, *, lock=False):
    statement = select(User).where(User.id == actor_id).execution_options(populate_existing=True)
    actor = db.scalar(statement.with_for_update() if lock else statement)
    if not actor or not actor.is_active:
        raise APIError(403, "ACTOR_UNAVAILABLE", "요청한 계정의 접근 권한이 변경되었습니다.")
    role = actor.role
    if actor.tenant_id != tenant_id:
        statement = select(Membership).where(Membership.tenant_id == tenant_id, Membership.user_id == actor_id, Membership.is_active.is_(True)).execution_options(populate_existing=True)
        membership = db.scalar(statement.with_for_update() if lock else statement)
        if membership is None:
            raise APIError(403, "MEMBERSHIP_UNAVAILABLE", "팀 접근 권한이 변경되었습니다.")
        role = membership.role
    return SimpleNamespace(id=actor_id, tenant_id=tenant_id, role=role)


def check_archive_access(db, user, snapshot, *, creating=False, lock=False):
    """Recheck current ACL, not current revision: the archive is frozen history."""
    if creating and user.role not in {"owner", "editor"}:
        raise APIError(403, "ROLE_FORBIDDEN", "편집 자료를 준비할 권한이 변경되었습니다.")
    enforce_membership_entitlement(db, user)
    def accessible(workspace_id):
        if not lock or user.role == "owner" or workspace_id is None:
            return workspace_access(db, user, workspace_id)
        return db.scalar(select(WorkspaceMember.id).where(WorkspaceMember.tenant_id == user.tenant_id,
            WorkspaceMember.user_id == user.id, WorkspaceMember.workspace_id == workspace_id).with_for_update()) is not None

    statement = select(Project).where(Project.id == snapshot["id"], Project.tenant_id == user.tenant_id).execution_options(populate_existing=True)
    project = db.scalar(statement.with_for_update() if lock else statement)
    if project is None or not accessible(project.workspace_id):
        raise APIError(404, "NOT_FOUND", "프로젝트 접근 권한을 확인해 주세요.")
    for frozen in snapshot["editable_assets"]:
        statement = select(Asset).where(Asset.id == frozen["id"], Asset.tenant_id == user.tenant_id).execution_options(populate_existing=True)
        asset = db.scalar(statement.with_for_update() if lock else statement)
        if asset is None or not accessible(asset.workspace_id):
            raise APIError(404, "NOT_FOUND", "원본 자산 접근 권한을 확인해 주세요.")
        if creating:
            ensure_asset_available(asset)
            if _asset_identity(asset) != {key: frozen[key] for key in _asset_identity(asset)}:
                raise APIError(409, "EDITABLE_SOURCE_CHANGED", "요청 후 원본 자산이 변경되었습니다. 새 편집 묶음을 요청해 주세요.")


def _read_asset(storage, frozen):
    raw = storage.get_limited(frozen["storage_key"], MAX_ASSET_BYTES)
    digest = sha256(raw).hexdigest()
    if len(raw) != frozen["byte_size"] or (frozen["sha256"] and digest != frozen["sha256"]):
        raise APIError(422, "EDITABLE_ASSET_CORRUPT", "원본 파일의 크기 또는 해시가 일치하지 않습니다.")
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format != MIME_EXT[frozen["content_type"]][1] or image.size != (frozen["width_px"], frozen["height_px"]) or image.width * image.height > 40_000_000:
                raise ValueError()
            image.verify()
    except Exception:
        raise APIError(422, "EDITABLE_ASSET_CORRUPT", "원본 이미지 파일을 확인하지 못했습니다.") from None
    return raw, digest


def build_editable_archive(snapshot, storage, output):
    """Only server UUIDs/constant font paths are archive paths; originals unchanged."""
    files, asset_manifest, total = [], [], 0
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=6) as archive:
        def write(name, raw, stored=False):
            nonlocal total
            total += len(raw)
            if total > MAX_BUNDLE_BYTES:
                raise APIError(422, "EDITABLE_SIZE_LIMIT", "편집 묶음의 합계 256MiB 한도를 초과했습니다.")
            archive.writestr(name, raw, compress_type=ZIP_STORED if stored else ZIP_DEFLATED)
            files.append({"path": name, "byte_size": len(raw), "sha256": sha256(raw).hexdigest()})

        project = {key: deepcopy(value) for key, value in snapshot.items() if key not in {
            "scene", "geometry", "structure_snapshot", "actor_id", "editable_assets", "editable_fonts", "rights_notice"}}
        project.update({"scene_path": "scene.json", "geometry_path": "geometry.json", "asset_index_path": "assets.json", "review_only": True})
        if snapshot.get("structure_snapshot"):
            project["structure_path"] = "structure.json"
        write("project.json", _json(project))
        write("scene.json", _json(snapshot["scene"]))
        write("geometry.json", _json(snapshot["geometry"]))
        if snapshot.get("structure_snapshot"):
            write("structure.json", _json(snapshot["structure_snapshot"]))
        for frozen in snapshot["editable_assets"]:
            raw, digest = _read_asset(storage, frozen)
            name = f"assets/{str(UUID(frozen['id']))}.{MIME_EXT[frozen['content_type']][0]}"
            write(name, raw, stored=True)
            asset_manifest.append({key: deepcopy(value) for key, value in frozen.items() if key not in {"storage_key", "sha256"}} |
                {"path": name, "sha256": digest, "integrity_baseline": "stored" if frozen["sha256"] else "computed_at_export"})
        write("assets.json", _json({"items": asset_manifest}))
        for font in snapshot["editable_fonts"]:
            expected = FONT_FILES.get(font["weight"])
            if expected is None or font["path"] != "fonts/" + expected[0] or font["sha256"] != expected[1]:
                raise APIError(422, "EDITABLE_FONT_UNSUPPORTED", "허용된 글꼴 파일이 아닙니다.")
            raw = (ROOT / "fixtures" / "fonts" / expected[0]).read_bytes()
            if sha256(raw).hexdigest() != expected[1]:
                raise APIError(422, "EDITABLE_FONT_CHANGED", "검증된 글꼴 파일의 해시가 일치하지 않습니다.")
            write(font["path"], raw)
        for name in ("OFL.txt", "README.md"):
            write("fonts/" + name, (ROOT / "fixtures" / "fonts" / name).read_bytes())
        write("README.ko.txt", ("Phoenix Packaging 편집용 프로젝트 자료\n\n" + RIGHTS_NOTICE +
            "\n\n선택한 저장본의 모든 면·숨김 객체·문구·crop·스타일을 scene.json에 보존했습니다. assets.json은 장면 asset_id와 원본 파일 경로를 연결합니다. "
            "파생 이미지의 참조·부모·최초 원본도 접근 가능한 범위에서 함께 보관합니다. 이미지 파일은 다시 압축하거나 잘라내지 않았습니다. "
            "프로젝트 정보는 project.json, 도면은 geometry.json입니다. 실제 사용 글꼴과 라이선스는 fonts/에 있습니다.\n\n"
            "이 묶음은 제조용 PDF나 Illustrator/Photoshop 형식이 아닙니다. JSON을 지원하는 프로그램에서 복원하거나 후속 편집 연동에 사용할 수 있는 Phoenix 자료입니다. "
            "개인 작업의 전체 백업이나 전체 리비전 이력을 포함하지 않습니다. 현재 앱에는 ZIP 재가져오기 UI가 없습니다. "
            "픽셀 확대·합성 도련의 원본 품질 한계와 샘플 바코드의 검토 전용 표시는 유지됩니다.\n").encode("utf-8"))
        manifest = {"format": "phoenix-editable", "format_version": FORMAT_VERSION, "bundle_version": snapshot["bundle_version"],
                    "project_id": snapshot["id"], "revision_id": snapshot["revision_id"], "revision_number": snapshot["base_revision"],
                    "created_at": utcnow().isoformat(), "review_only": True, "production_approved": False, "credits_charged": 0,
                    "rights_notice": RIGHTS_NOTICE, "fonts": snapshot["editable_fonts"], "files": list(files)}
        write("manifest.json", _json(manifest))
    if output.stat().st_size > MAX_BUNDLE_BYTES:
        raise APIError(422, "EDITABLE_SIZE_LIMIT", "압축 파일의 256MiB 한도를 초과했습니다.")
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise APIError(503, "EDITABLE_ZIP_INVALID", "편집 묶음의 무결성을 확인하지 못했습니다.")
    return {"format": "phoenix-editable", "format_version": FORMAT_VERSION, "revision_number": snapshot["base_revision"],
            "asset_count": len(asset_manifest), "font_count": len(snapshot["editable_fonts"]), "rights_notice": RIGHTS_NOTICE,
            "review_only": True, "credits_charged": 0, "media_type": "application/zip"}


def process_editable_jobs(sessions, storage, limit=1):
    processed = 0
    for _ in range(max(1, min(limit, 3))):
        lease, claimed = str(uuid4()), None
        for attempt in range(40):
            with sessions() as db:
                db.execute(update(Job).where(Job.kind == "editable_export", Job.status == "running", Job.updated_at < utcnow() - timedelta(minutes=15))
                           .values(status="queued", lease_id=None, updated_at=utcnow()))
                active = select(Job.tenant_id).where(Job.status == "running", Job.kind.in_(EXPORT_KINDS))
                candidate = db.scalar(select(Job).where(Job.kind == "editable_export", Job.status == "queued", Job.tenant_id.not_in(active))
                                      .order_by(Job.created_at, Job.id).limit(1).with_for_update(skip_locked=True))
                if candidate is None:
                    db.commit()
                    break
                try:
                    won = db.execute(update(Job).where(Job.id == candidate.id, Job.status == "queued").values(status="running", lease_id=lease, updated_at=utcnow()).execution_options(synchronize_session=False))
                    if won.rowcount != 1:
                        db.rollback()
                        continue
                    claimed = (candidate.id, candidate.tenant_id, deepcopy(candidate.snapshot))
                    db.commit()
                    break
                except IntegrityError:
                    db.rollback()
        if claimed is None:
            break
        identity, tenant_id, snapshot = claimed
        key = None
        published = False
        try:
            with sessions() as db:
                check_archive_access(db, _actor(db, tenant_id, snapshot["actor_id"]), snapshot, creating=True)
            with TemporaryDirectory(prefix="phoenix-editable-") as directory:
                output = Path(directory) / "editable.zip"
                summary = build_editable_archive(snapshot, storage, output)
                raw = output.read_bytes()
                digest = sha256(raw).hexdigest()
                key = f"{tenant_id}/exports/{identity}/{lease}.zip"
                storage.put(key, raw, "application/zip")
                del raw
                stored = storage.get_limited(key, MAX_BUNDLE_BYTES)
                if sha256(stored).hexdigest() != digest:
                    raise APIError(503, "STORAGE_VERIFICATION_FAILED", "편집 묶음 보관을 확인하지 못했습니다.")
                byte_size = len(stored)
                del stored
            with sessions() as db:
                lock_wallet(db, tenant_id)
                check_archive_access(db, _actor(db, tenant_id, snapshot["actor_id"], lock=True), snapshot, creating=True, lock=True)
                won = db.execute(update(Job).where(Job.id == identity, Job.status == "running", Job.lease_id == lease)
                    .values(status="succeeded", result={**summary, "storage_key": key, "sha256": digest, "byte_size": byte_size}, error=None, updated_at=utcnow()).execution_options(synchronize_session=False))
                if won.rowcount == 1:
                    db.add(AuditEvent(tenant_id=tenant_id, actor_id=snapshot["actor_id"], action="editable_export_succeeded", entity_id=identity,
                        details={"revision_id": snapshot["revision_id"], "sha256": digest, "credits_charged": 0, "rights_notice": RIGHTS_NOTICE}))
                    db.commit()
                    published = True
                else:
                    db.rollback()
        except Exception as error:
            message = error.message if isinstance(error, APIError) else "편집 자료를 준비하지 못했습니다. 원본 파일과 저장소 상태를 확인한 뒤 다시 시도해 주세요."
            with sessions() as db:
                won = db.execute(update(Job).where(Job.id == identity, Job.status == "running", Job.lease_id == lease)
                    .values(status="failed", error=message[:500], updated_at=utcnow()).execution_options(synchronize_session=False))
                if won.rowcount == 1:
                    db.add(AuditEvent(tenant_id=tenant_id, actor_id=snapshot["actor_id"], action="editable_export_failed", entity_id=identity,
                        details={"code": error.code if isinstance(error, APIError) else "EDITABLE_EXPORT_FAILED"}))
                db.commit()
        finally:
            # Delete only this lease's private object, never another worker's result.
            if key and not published:
                try:
                    # A DB connection can fail after COMMIT actually succeeded.
                    # Never remove an object a durable result references.
                    with sessions() as db:
                        current = db.get(Job, identity)
                        referenced = bool(current and (current.result or {}).get("storage_key") == key)
                    if not referenced:
                        if hasattr(storage, "delete"):
                            storage.delete(key)
                        else:
                            storage.path(key).unlink(missing_ok=True)
                except Exception:
                    pass  # Unreachable orphan; never falsely publish success.
        processed += 1
    return processed
