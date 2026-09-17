"""Versioned HTTP API for the first complete packaging workflow."""
from contextlib import asynccontextmanager
from datetime import timedelta
from hashlib import sha256
import hmac
from io import BytesIO
import json
import logging
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, Form, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from PIL import Image, ImageDraw, UnidentifiedImageError
from sqlalchemy import delete, exists, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from .auth import COOKIE_NAME, auth_payload, require_auth
from .config import Settings
from .database import Base, build_database, utcnow
from .errors import APIError
from .geometry import GeometryValidationError, validate_dimensions, build_geometry, geometry_for_scene, new_scene
from .google_auth import install_google_auth
from .models import Asset, Job, LoginSession, Project, Revision, Tenant, User
from .schemas import CreateProjectInput, DemoBackgroundInput, ExportInput, RevisionInput, SaveDraftInput
from .storage import SupabaseStorage, build_storage
from . import feature_models
from .billing import models as billing_models
from .business import enforce_item_access, validate_project_links

logger = logging.getLogger("phoenix.api")


def envelope(request, data):
    return {"data": data, "request_id": request.state.request_id}


def project_payload(project):
    geometry = geometry_for_scene(project.scene)
    keys=("id","name","product_name","brand_name","description","width_mm","height_mm","bottom_mm","depth_mm","template_id","template_version_id","print_profile_version_id","brand_id","product_variant_id","workspace_id","material","base_revision")
    return {**{key:getattr(project,key) for key in keys}, "scene":project.scene,"geometry":geometry,"created_at":project.created_at.isoformat(),"updated_at":project.updated_at.isoformat(),"approval_status":"requires_preflight" if project.template_version_id else "demo_unapproved","review_only":not bool(project.template_version_id and project.print_profile_version_id)}


def asset_payload(asset):
    metadata = asset.metadata_json or {}
    public_image = {key: metadata[key] for key in ("model", "requested_quality", "actual_quality", "output_size", "actual_size") if key in metadata}
    return {"id": asset.id, "name": asset.original_name, "content_type": asset.content_type, "byte_size": asset.byte_size, "width_px": asset.width_px, "height_px": asset.height_px, "source": asset.source, "url": f"/v1/assets/{asset.id}/content", **public_image}


def job_payload(job):
    result = {key: value for key, value in job.result.items() if key != "storage_key"} if job.result else None
    # History exposes the frozen request settings, never the full snapshot,
    # prompt, confirmed OCR text, actor IDs or private storage references.
    image_settings = {}
    if job.kind == "ai_generation":
        from .image_provider import image_settings_payload
        image_settings = {"image_settings": image_settings_payload(job.snapshot or {})}
    return {"id": job.id, "project_id": job.project_id, "kind": job.kind, "status": job.status, "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat(), "result": result, "error": job.error, "download_url": f"/v1/exports/{job.id}/download" if job.status == "succeeded" and job.kind.endswith("_export") else None, **{key:(result or {}).get(key,0) for key in ("credit_reserved","credit_charged","credit_returned")},"cancelable":job.kind=="ai_generation" and any(u.get("status")=="queued" for u in (result or {}).get("units",[])), **image_settings}


def owned(db, model, item_id, tenant_id):
    item = db.scalar(select(model).where(model.id == str(item_id), model.tenant_id == tenant_id))
    if item is None:
        raise APIError(404, "NOT_FOUND", "요청한 항목을 찾을 수 없습니다.")
    enforce_item_access(db,item)
    return item


def ensure_revision(project, number):
    if project.base_revision != number:
        raise APIError(409, "REVISION_CONFLICT", "다른 화면에서 저장한 내용이 있습니다. 서버의 최신 내용을 확인해 주세요.", {"base_revision": {"server_revision": project.base_revision}})


def snapshot_revision(db, project, reason):
    revision = db.scalar(select(Revision).where(Revision.project_id == project.id, Revision.number == project.base_revision))
    if revision is None:
        revision = Revision(project_id=project.id, tenant_id=project.tenant_id, number=project.base_revision, scene=project.scene, reason=reason)
        db.add(revision)
        db.flush()
    return revision


def initial_scene(project):
    from .exporters.review_pdf import _layout_text
    from .geometry import validate_scene
    w, h = project.width_mm, project.height_mm
    common = {"rotation_deg": 0, "visible": True, "print_enabled": True}
    def label(identity, face, value, y, size, color="#243A32"):
        return {**common, "id": identity, "type": "text", "face_id": face, "x_mm": 16, "y_mm": y, "width_mm": w - 32, "height_mm": max(12, size * 0.8), "z_index": 5, "text": value, "font_size_pt": size, "font_id": "NotoSansKR", "color": color, "align": "center"}
    front = [label("brand", "front", project.brand_name or "브랜드 이름", h * 0.18, 13), label("product", "front", project.product_name, h * 0.34, 27), label("front-note", "front", "문구를 클릭해 직접 수정하세요", h * 0.69, 10)]
    back = [label("back-title", "back", project.product_name, h * 0.16, 16), label("back-info", "back", "상품 정보\n\n원재료 · 확인 필요\n중량 · 확인 필요\n보관 방법 · 확인 필요\n제조사 · 확인 필요", h * 0.31, 10)]
    back[-1]["height_mm"] = h * 0.5
    front[0]["binding_key"]="brand_name";front[1]["binding_key"]="product_name";back[0]["binding_key"]="product_name"
    scene=new_scene(project.template_id,w,h,bottom_mm=project.bottom_mm,depth_mm=project.depth_mm)
    geometry=geometry_for_scene(scene)
    for face in scene["faces"]:
        face["objects"]=front if face["id"]=="front" else back if face["id"]=="back" else []
        safe=next(item for item in geometry["faces"] if item["id"]==face["id"])["regions"]["safe"]
        for index,obj in enumerate(face["objects"]):
            if w<100 or h<120:
                # Compact labels use disjoint slots inside this structure's safe area.
                slots=[(.04,.18),(.27,.43),(.77,.19)] if face["id"]=="front" else [(.02,.22),(.28,.70)]
                start,fraction=slots[index]
                obj.update(x_mm=safe["x_mm"]+1,width_mm=safe["width_mm"]-2,
                           y_mm=safe["y_mm"]+1+(safe["height_mm"]-2)*start,
                           height_mm=(safe["height_mm"]-2)*fraction)
            # Measure the original text with the same font/wrapping as the PDF.
            # Never clip or replace customer content when the face is small.
            obj.update(line_height=1.2,letter_spacing=0)
            while True:
                try:
                    _layout_text(obj)
                    break
                except GeometryValidationError as exc:
                    if exc.code!="TEXT_OVERFLOW" or obj["font_size_pt"]<=4: raise
                    obj["font_size_pt"]=max(4,round(obj["font_size_pt"]-.5,2))
    for key in ("brand_id","product_variant_id","workspace_id"): scene[key]=getattr(project,key)
    return validate_scene(scene)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.validate()
    engine, session_factory = build_database(settings)
    storage = build_storage(settings)

    @asynccontextmanager
    async def lifespan(app):
        # Hosted schemas are migrated explicitly by Alembic before a deployment.
        if settings.environment in {"development", "test"}:
            Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(title="Phoenix Packaging API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.storage = storage
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"])

    def db_session():
        with session_factory() as db:
            yield db

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid4())
        declared_length = request.headers.get("content-length")
        if declared_length and (not declared_length.isdigit() or int(declared_length) > settings.upload_limit + 1024 * 1024):
            return JSONResponse(status_code=413, content={"code": "REQUEST_TOO_LARGE", "message": "요청 크기가 제한을 초과합니다.", "field_errors": {}, "retryable": False, "request_id": request.state.request_id})
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(APIError)
    async def domain_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message, "field_errors": exc.field_errors, "retryable": exc.retryable, "request_id": request.state.request_id})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        fields = {".".join(map(str, e["loc"][1:])): e["msg"] for e in exc.errors()}
        return JSONResponse(status_code=422, content={"code": "VALIDATION_ERROR", "message": "입력 내용을 확인해 주세요.", "field_errors": fields, "retryable": False, "request_id": request.state.request_id})

    @app.exception_handler(GeometryValidationError)
    async def geometry_error(request, exc):
        return JSONResponse(status_code=422, content={"code": exc.code, "message": exc.message, "field_errors": {exc.field: exc.message}, "retryable": False, "request_id": request.state.request_id})

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"code": "HTTP_ERROR", "message": "요청을 처리할 수 없습니다.", "field_errors": {}, "retryable": False, "request_id": request.state.request_id})

    @app.exception_handler(Exception)
    async def server_error(request, exc):
        # Do not log SQL parameters, request payloads, tokens, or signed URLs.
        logger.error("request_failed request_id=%s error_type=%s", request.state.request_id, type(exc).__name__)
        return JSONResponse(status_code=500, content={"code": "INTERNAL_ERROR", "message": "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.", "field_errors": {}, "retryable": True, "request_id": request.state.request_id})

    @app.get("/v1/health")
    def health(request: Request, db=Depends(db_session)):
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            raise APIError(503, "DATABASE_UNAVAILABLE", "데이터 저장소에 연결할 수 없습니다.", retryable=True)
        return envelope(request, {"status": "ok", "database": "connected", "environment": settings.environment})

    @app.get("/v1/config")
    def config(request: Request):
        from .image_provider import get_capabilities
        return envelope(request, {"demo_mode": settings.demo_mode, "ai_provider": settings.ai_provider,"ai_capabilities":get_capabilities(settings), "billing_provider":app.state.billing_settings.provider, "production_export_enabled":settings.enable_production_export, "review_export_credits": 0,"direct_upload":isinstance(storage,SupabaseStorage), "upload_max_bytes":20*1024*1024 if isinstance(storage,SupabaseStorage) else settings.upload_limit, "supported_upload_types": ["image/png", "image/jpeg", "image/webp"], "auth_provider": "google", "google_login_enabled": bool(settings.google_client_id), "google_client_id": settings.google_client_id or None})

    install_google_auth(app, db_session)

    @app.post("/v1/auth/logout")
    def logout(request: Request, response: Response, db=Depends(db_session)):
        _, session = require_auth(request, db, mutate=True, authorize_write=False,enforce_membership=False)
        db.delete(session)
        db.commit()
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
        return envelope(request, {"logged_out": True})

    @app.get("/v1/me")
    def me(request: Request, db=Depends(db_session)):
        user, session = require_auth(request, db)
        return envelope(request, auth_payload(db, user, session))

    @app.get("/v1/templates")
    def templates(request: Request, db=Depends(db_session)):
        from .registry import registry_payload
        rows=[{"id":identity,"name":name,"version":"demo-1","status":"demo","approval_status":"demo_unapproved","review_only":True,"description":"제조사 미승인 데모 구조","faces":faces,"default_width_mm":160,"default_height_mm":230,"min_width_mm":60,"max_width_mm":600,"min_height_mm":80,"max_height_mm":800,"seal_mm":10,"safe_mm":5,"bleed_mm":3} for identity,name,faces in [("three-side-seal","3면 실링 봉투",["front","back"]),("stand-up-pouch","스탠드 파우치",["front","back","bottom"]),("folding-box","접이식 박스",["front","back","left","right","top","bottom"])]]
        rows += [registry_payload(row) for row in db.scalars(select(feature_models.RegistryVersion).where(feature_models.RegistryVersion.kind=="template",feature_models.RegistryVersion.status=="approved",feature_models.RegistryVersion.is_demo.is_(False)))]
        return envelope(request,{"items":rows})

    @app.post("/v1/geometry/validate")
    def validate_geometry(body:dict, request:Request):
        if set(body)-{"template_id","width_mm","height_mm","bottom_mm","depth_mm","unit","holes","pouch_features"}: raise APIError(422,"GEOMETRY_FIELDS_INVALID","규격 입력 항목을 확인해 주세요.")
        return envelope(request,build_geometry(body.get("template_id","three-side-seal"),body.get("width_mm"),body.get("height_mm"),body.get("unit","mm"),bottom_mm=body.get("bottom_mm"),depth_mm=body.get("depth_mm"),holes=body.get("holes",[]),pouch_features=body.get("pouch_features")))

    @app.post("/v1/geometry/barcode")
    def barcode(body:dict, request:Request):
        from .geometry import barcode_geometry
        from .geometry.barcodes import sample_ean13
        if set(body)-{"value","module_mm","bar_height_mm","scene","face_id","x_mm","y_mm","barcode_usage"}: raise APIError(422,"BARCODE_FIELDS_INVALID","바코드 입력 항목을 확인해 주세요.")
        if "scene" not in body and any(key in body for key in ("face_id","x_mm","y_mm")):
            raise APIError(422,"BARCODE_SCENE_REQUIRED","배치를 검증할 현재 디자인이 필요합니다.")
        usage=body.get("barcode_usage","retail")
        value=body.get("value")
        if value is None and usage=="sample": value=sample_ean13()
        result=barcode_geometry(value,module_mm=body.get("module_mm",0.33),bar_height_mm=body.get("bar_height_mm",22.85),barcode_usage=usage)
        if "scene" in body:
            from pydantic import ValidationError
            from .schemas import Scene
            from .geometry.barcode_placement import place_barcode
            try:
                scene=Scene.model_validate(body["scene"]).model_dump(mode="json",exclude_none=True)
            except ValidationError as exc:
                fields={"scene."+".".join(map(str,error["loc"])):error["msg"] for error in exc.errors()}
                raise APIError(422,"BARCODE_SCENE_INVALID","현재 디자인의 면과 객체 정보를 확인해 주세요.",fields) from None
            result["placement"]=place_barcode(scene,body.get("face_id"),result,x_mm=body.get("x_mm"),y_mm=body.get("y_mm"))
        return envelope(request,result)

    @app.get("/v1/projects")
    def projects(request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        query = select(Project).where(Project.tenant_id == user.tenant_id)
        if user.role != "owner":
            permitted_workspace = exists(select(feature_models.WorkspaceMember.id).where(feature_models.WorkspaceMember.workspace_id == Project.workspace_id, feature_models.WorkspaceMember.user_id == user.id, feature_models.WorkspaceMember.tenant_id == user.tenant_id))
            query = query.where(or_(Project.workspace_id.is_(None), permitted_workspace))
        rows = db.scalars(query.order_by(Project.updated_at.desc()).limit(100)).all()
        return envelope(request, {"items": [project_payload(row) for row in rows]})

    @app.post("/v1/projects", status_code=201)
    def create_project(body: CreateProjectInput, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        validate_project_links(db,user,body.model_dump())
        project = Project(tenant_id=user.tenant_id, created_by=user.id, **body.model_dump(mode="json"))
        project.scene = initial_scene(project)
        db.add(project)
        db.flush()
        snapshot_revision(db, project, "created")
        db.commit()
        return envelope(request, project_payload(project))

    @app.get("/v1/projects/{project_id}")
    def get_project(project_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        return envelope(request, project_payload(owned(db, Project, project_id, user.tenant_id)))

    @app.patch("/v1/projects/{project_id}/draft")
    def save_draft(project_id: UUID, body: SaveDraftInput, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        project = owned(db, Project, project_id, user.tenant_id)
        ensure_revision(project, body.base_revision)
        scene = body.scene.model_dump(mode="json", exclude_none=True)
        scene["template_version_id"] = project.template_version_id or project.template_id+"-demo-v1"
        scene["template_kind"] = project.template_id
        scene["bottom_mm"]=project.bottom_mm;scene["depth_mm"]=project.depth_mm
        for key in ("brand_id","product_variant_id","workspace_id"): scene[key]=getattr(project,key)
        geometry = geometry_for_scene(scene)
        scene["geometry_hash"] = geometry["geometry_hash"]
        expected=build_geometry(project.template_id,project.width_mm,project.height_mm,bottom_mm=project.bottom_mm,depth_mm=project.depth_mm)
        expected_faces={f["id"]:f for f in expected["faces"]}
        if set(f["id"] for f in scene["faces"])!=set(expected_faces): raise APIError(422,"FACE_SET_MISMATCH","모든 편집 면을 포함해 주세요.")
        for face in scene["faces"]:
            if face["width_mm"] != expected_faces[face["id"]]["width_mm"] or face["height_mm"] != expected_faces[face["id"]]["height_mm"]:
                raise APIError(422, "FACE_DIMENSIONS_MISMATCH", "포장 규격과 편집 면의 크기가 다릅니다.")
            for obj in face["objects"]:
                if obj.get("asset_id"):
                    owned(db, Asset, obj["asset_id"], user.tenant_id)
        values = {"scene": scene, "base_revision": body.base_revision + 1, "updated_at": utcnow()}
        if body.name is not None:
            if not body.name.strip():
                raise APIError(422, "NAME_REQUIRED", "프로젝트 이름을 입력해 주세요.")
            values["name"] = body.name.strip()
        result = db.execute(update(Project).where(Project.id == project.id, Project.tenant_id == user.tenant_id, Project.base_revision == body.base_revision).values(**values).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            db.rollback()
            db.refresh(project)
            ensure_revision(project, body.base_revision)
            raise APIError(409, "REVISION_CONFLICT", "서버의 최신 내용을 다시 열어 주세요.")
        # The successful CAS holds the project write lock. Preserve both sides
        # atomically, including a previously unsnapshotted legacy draft.
        snapshot_revision(db, project, "before_autosave")
        db.refresh(project)
        snapshot_revision(db, project, "autosave")
        db.commit()
        return envelope(request, project_payload(project))

    @app.post("/v1/projects/{project_id}/revisions", status_code=201)
    def create_revision(project_id: UUID, body: RevisionInput, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        project = owned(db, Project, project_id, user.tenant_id)
        ensure_revision(project, body.base_revision)
        try:
            revision = snapshot_revision(db, project, body.reason)
            db.commit()
        except IntegrityError:
            db.rollback()
            revision = db.scalar(select(Revision).where(Revision.project_id == project.id, Revision.number == body.base_revision))
        return envelope(request, {"id": revision.id, "project_id": revision.project_id, "number": revision.number, "scene": revision.scene, "created_at": revision.created_at.isoformat()})

    @app.get("/v1/projects/{project_id}/revisions")
    def revisions(project_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        project = owned(db, Project, project_id, user.tenant_id)
        rows = db.scalars(select(Revision).where(Revision.project_id == project.id, Revision.tenant_id == user.tenant_id).order_by(Revision.number.desc()).limit(100)).all()
        return envelope(request, {"items": [{"id": row.id, "number": row.number, "reason": row.reason, "scene": row.scene, "created_at": row.created_at.isoformat()} for row in rows]})

    @app.post("/v1/assets", status_code=201)
    def upload_asset(request: Request, file: UploadFile = File(...), project_id: str | None=Form(default=None), db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        project=owned(db,Project,project_id,user.tenant_id) if project_id else None
        body = file.file.read(settings.upload_limit + 1)
        if len(body) > settings.upload_limit:
            raise APIError(413, "ASSET_TOO_LARGE", f"파일은 {settings.upload_limit // (1024 * 1024)}MiB 이하로 올려 주세요.")
        if file.content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise APIError(422, "ASSET_TYPE_UNSUPPORTED", "PNG, JPEG, WebP 이미지만 지원합니다.")
        try:
            with Image.open(BytesIO(body)) as image:
                actual_type = Image.MIME.get(image.format)
                width, height = image.size
                if actual_type != file.content_type or width * height > 40_000_000 or width < 1 or height < 1:
                    raise ValueError("Image type or dimensions invalid")
                image.verify()
        except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
            raise APIError(422, "ASSET_INVALID", "파일 형식 또는 이미지 크기를 확인해 주세요.")
        total_size = db.scalar(select(func.coalesce(func.sum(Asset.byte_size), 0)).where(Asset.tenant_id == user.tenant_id))
        if total_size + len(body) > 200 * 1024 * 1024:
            raise APIError(422, "ASSET_QUOTA_EXCEEDED", "개발 데모의 저장 한도 200MiB를 초과했습니다.")
        asset_id = str(uuid4())
        key = f"{user.tenant_id}/assets/{asset_id}"
        storage.put(key, body, file.content_type)
        asset = Asset(id=asset_id, tenant_id=user.tenant_id,workspace_id=project.workspace_id if project else None, storage_key=key, original_name=Path(file.filename or "image").name[:160], content_type=file.content_type, byte_size=len(body), width_px=width, height_px=height)
        db.add(asset)
        db.commit()
        return envelope(request, asset_payload(asset))

    @app.get("/v1/assets/{asset_id}/content")
    def asset_content(asset_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        asset = owned(db, Asset, asset_id, user.tenant_id)
        from .asset_reconciliation import ensure_asset_available
        ensure_asset_available(asset)
        if isinstance(storage, SupabaseStorage):
            return RedirectResponse(storage.signed_url(asset.storage_key, ttl=60), status_code=307)
        return Response(content=storage.get(asset.storage_key), media_type=asset.content_type, headers={"Content-Disposition": "inline"})

    @app.post("/v1/projects/{project_id}/demo-background", status_code=201)
    def demo_background(project_id: UUID, body: DemoBackgroundInput, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        project=owned(db, Project, project_id, user.tenant_id)
        if not settings.demo_mode:
            raise APIError(422, "DEMO_DISABLED", "운영 환경에서는 데모 이미지를 생성할 수 없습니다.")
        palettes = {"forest": ("#E8EDDB", "#4B6849", "#C8D5A4"), "citrus": ("#FCEDD1", "#E8994F", "#F2C768"), "berry": ("#F2E5EA", "#86596E", "#D0A9C0")}
        base, dark, light = palettes[body.palette]
        image = Image.new("RGB", (768, 1024), base)
        draw = ImageDraw.Draw(image)
        for x, y, radius in [(650, 850, 280), (-50, 600, 180), (730, 180, 140)]:
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=light)
        draw.ellipse((200, 790, 900, 1370), fill=dark)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        raw = buffer.getvalue()
        asset_id = str(uuid4())
        key = f"{user.tenant_id}/assets/{asset_id}"
        storage.put(key, raw, "image/png")
        asset = Asset(id=asset_id, tenant_id=user.tenant_id, workspace_id=project.workspace_id,storage_key=key, original_name=f"데모 배경-{body.palette}.png", content_type="image/png", byte_size=len(raw), width_px=768, height_px=1024, source="fixture")
        db.add(asset)
        db.commit()
        return envelope(request, {**asset_payload(asset), "demo": True, "label": "자체 제작 데모 이미지 · AI 생성 아님", "credits_charged": 0})

    @app.post("/v1/exports", status_code=202)
    def create_export(body: ExportInput, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        if body.kind=="production":
            from .production_routes import create_production_export
            return envelope(request,job_payload(create_production_export(db,user,body,request,project_payload,snapshot_revision)))
        project = owned(db, Project, body.project_id, user.tenant_id)
        ensure_revision(project, body.base_revision)
        from .exporters.public_profiles import BASIC_REVIEW_PROFILE_ID
        operation_key = request.headers.get("idempotency-key", f"review:{project.id}:{body.base_revision}:{BASIC_REVIEW_PROFILE_ID}")
        if not 1 <= len(operation_key) <= 160:
            raise APIError(422, "IDEMPOTENCY_KEY_INVALID", "요청 식별자가 올바르지 않습니다.")
        request_hash = sha256(json.dumps({**body.model_dump(mode="json"), "review_profile_id": BASIC_REVIEW_PROFILE_ID}, sort_keys=True).encode()).hexdigest()
        existing = db.scalar(select(Job).where(Job.tenant_id == user.tenant_id, Job.operation_key == operation_key))
        if existing:
            if existing.request_hash != request_hash:
                raise APIError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 식별자로 다른 작업을 보낼 수 없습니다.")
            return envelope(request, job_payload(existing))
        count = db.scalar(select(func.count()).select_from(Job).where(Job.tenant_id == user.tenant_id,Job.kind=="review_export", Job.created_at > utcnow() - timedelta(hours=1)))
        if count >= 30:
            raise APIError(429, "EXPORT_RATE_LIMIT", "검토 파일 요청이 많습니다. 잠시 후 다시 시도해 주세요.", retryable=True)
        try:
            revision = snapshot_revision(db, project, "review_export")
            job = Job(tenant_id=user.tenant_id, project_id=project.id, revision_id=revision.id, operation_key=operation_key, request_hash=request_hash, snapshot={**project_payload(project), "revision_id": revision.id, "review_profile_id": BASIC_REVIEW_PROFILE_ID})
            db.add(job)
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(select(Job).where(Job.tenant_id == user.tenant_id, Job.operation_key == operation_key))
            if existing and existing.request_hash == request_hash:
                return envelope(request, job_payload(existing))
            raise APIError(409, "EXPORT_CONFLICT", "요청을 다시 확인해 주세요.", retryable=True)
        # A durable worker consumes this row; no long-running PDF work in web requests.
        return envelope(request, job_payload(job))

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        return envelope(request, job_payload(owned(db, Job, job_id, user.tenant_id)))

    @app.post("/v1/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        job = owned(db, Job, job_id, user.tenant_id)
        if job.status != "failed" or job.kind!="review_export":
            raise APIError(409, "JOB_NOT_RETRYABLE", "실패한 작업만 다시 시도할 수 있습니다.")
        changed = db.execute(update(Job).where(Job.id == job.id, Job.status == "failed").values(status="queued", lease_id=None, error=None, updated_at=utcnow()).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise APIError(409, "JOB_NOT_RETRYABLE", "다른 요청에서 이미 재시도했습니다.")
        db.commit()
        db.refresh(job)
        return envelope(request, job_payload(job))

    @app.get("/v1/projects/{project_id}/exports")
    def exports(project_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        owned(db, Project, project_id, user.tenant_id)
        rows = db.scalars(select(Job).where(Job.tenant_id == user.tenant_id, Job.project_id == str(project_id),Job.kind.in_(["review_export","production_export"])).order_by(Job.created_at.desc()).limit(100)).all()
        return envelope(request, {"items": [job_payload(row) for row in rows]})

    @app.get("/v1/exports/{job_id}/download")
    def download(job_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        job = owned(db, Job, job_id, user.tenant_id)
        if job.status != "succeeded" or not job.result or not job.result.get("storage_key"):
            raise APIError(409, "EXPORT_NOT_READY", "검토 파일을 준비하고 있습니다.", retryable=True)
        extension="zip" if job.kind=="production_export" else "pdf"
        name=f"phoenix-{job.kind}-{job.id}.{extension}"
        if isinstance(storage, SupabaseStorage):
            return RedirectResponse(storage.signed_url(job.result["storage_key"], ttl=60, download_name=name), status_code=307)
        content = storage.get(job.result["storage_key"])
        return Response(content=content, media_type="application/zip" if extension=="zip" else "application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/v1/internal/jobs/process")
    @app.post("/v1/internal/jobs/process")
    def process_jobs(request: Request):
        supplied = request.headers.get("authorization", "")
        if not settings.worker_secret or not hmac.compare_digest(supplied, f"Bearer {settings.worker_secret}"):
            raise APIError(404, "NOT_FOUND", "요청한 항목을 찾을 수 없습니다.")
        from .worker_service import process_all_jobs
        breakdown=process_all_jobs(session_factory,storage,settings)
        if any(value=="retry_pending" for value in breakdown.values()):
            raise APIError(503,"WORKER_RETRY_PENDING","일부 백그라운드 작업을 다음 실행에서 다시 확인합니다.",{"breakdown":breakdown},retryable=True)
        return envelope(request, {"processed":sum(v for v in breakdown.values() if isinstance(v,int)),"breakdown":breakdown})

    from .billing.routes import install_billing_routes
    from .business import install_business_routes
    from .registry import install_registry_routes
    from .ai_routes import install_ai_routes, prepare_ai_quote
    from .production_routes import install_production_routes, prepare_production_quote
    def prepare_quote(db,user,body):
        if body["action"].startswith("image."): return prepare_ai_quote(db,user,body,settings)
        if body["action"].startswith("export.production"):
            return prepare_production_quote(db,user,body,settings,project_payload,snapshot_revision,storage=storage)
        raise APIError(422,"QUOTE_ACTION_INVALID","이 작업에는 견적이 필요하지 않습니다.")
    app.state.prepare_quote=prepare_quote
    install_billing_routes(app,db_session)
    install_business_routes(app,db_session,project_payload,snapshot_revision)
    install_registry_routes(app,db_session,project_payload,snapshot_revision)
    install_ai_routes(app,db_session,job_payload,snapshot_revision)
    install_production_routes(app,db_session,owned,project_payload,snapshot_revision)
    from .uploads import install_upload_routes
    install_upload_routes(app,db_session,asset_payload)
    from .print_preparation import install_print_preparation_routes
    install_print_preparation_routes(app,db_session)
    from .image_quality import install_image_quality_routes
    install_image_quality_routes(app,db_session,asset_payload)

    return app


app = create_app()
