"""Versioned HTTP API for the first complete packaging workflow."""
from .contracts import jobs as J
from .contracts.base import Envelope, ERROR_RESPONSES, binary_responses
from .contracts import core as C
from .contracts import geometry as G
from .contracts import images as I
from .contracts import registry as R
from contextlib import asynccontextmanager
from datetime import timedelta
from hashlib import sha256
import hmac
from io import BytesIO
import json
import logging
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, Form, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import delete, exists, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from .auth import COOKIE_NAME, auth_payload, require_auth
from .config import ROOT, Settings
from .database import Base, build_database, utcnow
from .errors import APIError
from .geometry import GeometryValidationError, validate_dimensions, build_geometry, geometry_for_scene, new_scene
from .google_auth import install_google_auth
from .models import Asset, Job, LoginSession, Project, Revision, Tenant, User
from .schemas import CreateProjectInput, DemoBackgroundInput, ExportInput, RevisionInput, SaveDraftInput
from .storage import SupabaseStorage, build_storage
from . import feature_models
from . import inquiries as inquiry_models  # noqa: F401  (table registration)
from .service_orders import models as service_order_models
from .operations import models as operation_models
from .metrics import models as metric_models
from .retention import models as retention_models
from .font_assets.routes import install_font_routes
from .font_assets.service import freeze_fonts, validate_scene_fonts
from .billing import models as billing_models
from .business import enforce_item_access, validate_project_links
from .editor_sessions import enforce_edit_lease, install_editor_routes
from .retention.deletion import available_asset_clause

logger = logging.getLogger("phoenix.api")


def envelope(request, data):
    return {"data": data, "request_id": request.state.request_id}


def project_payload(project):
    from .geometry.snapshots import project_geometry
    geometry = project_geometry(project)
    keys=("id","name","product_name","brand_name","description","width_mm","height_mm","bottom_mm","depth_mm","template_id","template_version_id","print_profile_version_id","brand_id","product_variant_id","workspace_id","material","base_revision")
    return {**{key:getattr(project,key) for key in keys}, "scene":project.scene,"geometry":geometry,"structure_snapshot":getattr(project,"structure_snapshot",None),"created_at":project.created_at.isoformat(),"updated_at":project.updated_at.isoformat(),"approval_status":"registered_review_only" if getattr(project,"structure_snapshot",None) else "requires_preflight" if project.template_version_id else "demo_unapproved","review_only":bool(getattr(project,"structure_snapshot",None)) or not bool(project.template_version_id and project.print_profile_version_id)}


def asset_payload(asset):
    metadata = asset.metadata_json or {}
    public_image = {key: metadata[key] for key in ("model", "requested_quality", "actual_quality", "output_size", "actual_size") if key in metadata}
    return {"id": asset.id, "name": asset.original_name, "content_type": asset.content_type, "byte_size": asset.byte_size, "width_px": asset.width_px, "height_px": asset.height_px, "source": asset.source, "url": f"/v1/assets/{asset.id}/content", **public_image}


def job_payload(job, *, current_approval=None, current_intake=None):
    from .export_reconciliation import download_is_available, export_availability
    result = {key: value for key, value in job.result.items() if key not in {"storage_key", "_retention", "_integrity"}} if job.result else None
    # History exposes the frozen request settings, never the full snapshot,
    # prompt, confirmed OCR text, actor IDs or private storage references.
    image_settings = {}
    if job.kind == "ai_generation":
        from .image_provider import image_settings_payload
        image_settings = {"image_settings": image_settings_payload(job.snapshot or {})}
    approval = {"current_approval": current_approval} if job.kind == "production_export" and current_approval is not None else {}
    intake = {"current_intake": current_intake} if job.kind in {"review_export", "production_export"} and current_intake is not None else {}
    availability = export_availability(job)
    return {"id": job.id, "project_id": job.project_id, "kind": job.kind, "status": job.status, "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat(), "result": result, "error": job.error, "download_url": f"/v1/exports/{job.id}/download" if download_is_available(job) else None, **{key:(result or {}).get(key,0) for key in ("credit_reserved","credit_charged","credit_returned")},"cancelable":job.kind=="ai_generation" and any(u.get("status")=="queued" for u in (result or {}).get("units",[])), **image_settings, **approval, **intake, **({"availability": availability} if availability is not None else {})}


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
        revision = Revision(project_id=project.id, tenant_id=project.tenant_id, number=project.base_revision, scene=project.scene, structure_snapshot=getattr(project,"structure_snapshot",None), reason=reason)
        db.add(revision)
        db.flush()
    return revision


def normalize_draft_scene(db, user, project, body_scene, *, links=None, restoring=False):
    scene = body_scene.model_dump(mode="json", exclude_none=True)
    scene["template_version_id"] = project.template_version_id or project.template_id+"-demo-v1"
    scene["template_kind"] = project.template_id
    scene["bottom_mm"] = project.bottom_mm
    scene["depth_mm"] = project.depth_mm
    for key in ("brand_id", "product_variant_id", "workspace_id"):
        scene[key] = (links or {}).get(key, getattr(project, key))
    structure_snapshot=getattr(project,"structure_snapshot",None)
    if structure_snapshot is not None:
        from .geometry.snapshots import structure_ref
        scene["structure_ref"]=structure_ref(structure_snapshot)
        scene["geometry_hash"]=structure_snapshot["geometry_hash"]
        expected=geometry_for_scene(scene,structure_snapshot=structure_snapshot)
    else:
        scene.pop("structure_ref",None)
        scene["geometry_hash"] = geometry_for_scene(scene)["geometry_hash"]
        expected = build_geometry(project.template_id, project.width_mm, project.height_mm, bottom_mm=project.bottom_mm, depth_mm=project.depth_mm)
    expected_faces = {face["id"]: face for face in expected["faces"]}
    if set(face["id"] for face in scene["faces"]) != set(expected_faces):
        raise APIError(422, "FACE_SET_MISMATCH", "모든 편집 면을 포함해 주세요.")
    for face in scene["faces"]:
        if face["width_mm"] != expected_faces[face["id"]]["width_mm"] or face["height_mm"] != expected_faces[face["id"]]["height_mm"]:
            raise APIError(422, "FACE_DIMENSIONS_MISMATCH", "포장 규격과 편집 면의 크기가 다릅니다.")
        for obj in face["objects"]:
            if obj.get("asset_id"):
                asset=owned(db, Asset, obj["asset_id"], user.tenant_id)
                if asset.content_type=="image/svg+xml":
                    raise APIError(422,"SVG_SOURCE_NOT_PLACEABLE","정화 SVG 원본은 기록용입니다. 함께 생성된 PNG 이미지를 배치해 주세요.")
    validate_scene_fonts(db,user,scene,project.scene,enforce_brand=not restoring)
    return scene


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

    app = FastAPI(title="Phoenix Package Design API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.storage = storage
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "X-Editor-Lease"])

    def db_session(request: Request):
        with session_factory() as db:
            db.info["request"] = request
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
        if exc.code == "INSUFFICIENT_CREDITS" and getattr(request.state, "metric_tenant_id", None):
            from starlette.concurrency import run_in_threadpool
            from .metrics.service import record_credit_failure
            await run_in_threadpool(record_credit_failure, request.app.state.session_factory,
                request.state.metric_tenant_id, request.state.request_id, exc.field_errors)
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

    @app.get("/v1/health", response_model=Envelope[C.HealthData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def health(request: Request, db=Depends(db_session)):
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            raise APIError(503, "DATABASE_UNAVAILABLE", "데이터 저장소에 연결할 수 없습니다.", retryable=True)
        return envelope(request, {"status": "ok", "database": "connected", "environment": settings.environment})

    @app.get("/v1/config", response_model=Envelope[I.PublicConfig], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def config(request: Request):
        from .image_provider import get_capabilities
        return envelope(request, {"demo_mode": settings.demo_mode, "ai_provider": settings.ai_provider,"ai_capabilities":get_capabilities(settings), "billing_provider":app.state.billing_settings.provider, "production_export_enabled":settings.enable_production_export, "review_export_credits": 0,"direct_upload":isinstance(storage,SupabaseStorage), "upload_max_bytes":20*1024*1024 if isinstance(storage,SupabaseStorage) else settings.upload_limit, "supported_upload_types": ["image/png", "image/jpeg", "image/webp", "image/svg+xml"], "auth_provider": "google", "google_login_enabled": bool(settings.google_client_id), "google_client_id": settings.google_client_id or None})

    install_google_auth(app, db_session)

    @app.post("/v1/auth/logout", response_model=Envelope[C.LogoutData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def logout(request: Request, response: Response, db=Depends(db_session)):
        _, session = require_auth(request, db, mutate=True, authorize_write=False,enforce_membership=False)
        db.delete(session)
        db.commit()
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
        return envelope(request, {"logged_out": True})

    @app.get("/v1/me", response_model=Envelope[C.SessionData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def me(request: Request, db=Depends(db_session)):
        user, session = require_auth(request, db)
        return envelope(request, auth_payload(db, user, session))

    @app.get("/v1/templates", response_model=Envelope[C.Items[R.DemoTemplate | R.RegistryVersionData]], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def templates(request: Request, db=Depends(db_session)):
        from .registry import registry_payload
        rows=[{"id":identity,"name":name,"version":"demo-1","status":"demo","approval_status":"demo_unapproved","review_only":True,"description":"제조사 미승인 데모 구조","faces":faces,"default_width_mm":160,"default_height_mm":230,"min_width_mm":60,"max_width_mm":600,"min_height_mm":80,"max_height_mm":800,"seal_mm":10,"safe_mm":5,"bleed_mm":3} for identity,name,faces in [("three-side-seal","3면 실링 봉투",["front","back"]),("stand-up-pouch","스탠드 파우치",["front","back","bottom"]),("folding-box","접이식 박스",["front","back","left","right","top","bottom"])]]
        rows += [registry_payload(row) for row in db.scalars(select(feature_models.RegistryVersion).where(feature_models.RegistryVersion.kind=="template",feature_models.RegistryVersion.status=="approved",feature_models.RegistryVersion.is_demo.is_(False)))]
        return envelope(request,{"items":rows})

    @app.post("/v1/geometry/validate", response_model=Envelope[G.Geometry], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def validate_geometry(body:dict, request:Request):
        if set(body)-{"template_id","width_mm","height_mm","bottom_mm","depth_mm","unit","holes","pouch_features"}: raise APIError(422,"GEOMETRY_FIELDS_INVALID","규격 입력 항목을 확인해 주세요.")
        return envelope(request,build_geometry(body.get("template_id","three-side-seal"),body.get("width_mm"),body.get("height_mm"),body.get("unit","mm"),bottom_mm=body.get("bottom_mm"),depth_mm=body.get("depth_mm"),holes=body.get("holes",[]),pouch_features=body.get("pouch_features")))

    @app.post("/v1/geometry/barcode", response_model=Envelope[G.BarcodeGeometry], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def barcode(body:dict, request:Request, db=Depends(db_session)):
        from .geometry import barcode_geometry
        from .geometry.barcodes import sample_ean13
        if set(body)-{"value","module_mm","bar_height_mm","scene","face_id","x_mm","y_mm","barcode_usage","project_id","base_revision"}: raise APIError(422,"BARCODE_FIELDS_INVALID","바코드 입력 항목을 확인해 주세요.")
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
            structure_snapshot=None
            if scene.get("structure_ref"):
                user,_=require_auth(request,db)
                project=owned(db,Project,body.get("project_id"),user.tenant_id)
                ensure_revision(project,body.get("base_revision"))
                structure_snapshot=project.structure_snapshot
            result["placement"]=place_barcode(scene,body.get("face_id"),result,x_mm=body.get("x_mm"),y_mm=body.get("y_mm"),structure_snapshot=structure_snapshot)
        return envelope(request,result)

    @app.get("/v1/projects", response_model=Envelope[C.Items[C.ProjectData]], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def projects(request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        query = select(Project).where(Project.tenant_id == user.tenant_id)
        if user.role != "owner":
            permitted_workspace = exists(select(feature_models.WorkspaceMember.id).where(feature_models.WorkspaceMember.workspace_id == Project.workspace_id, feature_models.WorkspaceMember.user_id == user.id, feature_models.WorkspaceMember.tenant_id == user.tenant_id))
            query = query.where(or_(Project.workspace_id.is_(None), permitted_workspace))
        rows = db.scalars(query.order_by(Project.updated_at.desc()).limit(100)).all()
        return envelope(request, {"items": [project_payload(row) for row in rows]})

    @app.post("/v1/projects", status_code=201, response_model=Envelope[C.ProjectData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def create_project(body: CreateProjectInput, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        validate_project_links(db,user,body.model_dump())
        project = Project(tenant_id=user.tenant_id, created_by=user.id, **body.model_dump(mode="json"))
        project.scene = initial_scene(project)
        db.add(project)
        db.flush()
        snapshot_revision(db, project, "created")
        from .metrics.service import record_project_created
        record_project_created(db, project)
        db.commit()
        return envelope(request, project_payload(project))

    @app.get("/v1/projects/{project_id}", response_model=Envelope[C.ProjectData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def get_project(project_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        return envelope(request, project_payload(owned(db, Project, project_id, user.tenant_id)))

    @app.patch("/v1/projects/{project_id}/draft", response_model=Envelope[C.ProjectData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def save_draft(project_id: UUID, body: SaveDraftInput, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        project = owned(db, Project, project_id, user.tenant_id)
        enforce_edit_lease(db, project, request)
        ensure_revision(project, body.base_revision)
        previous_scene = project.scene
        scene = normalize_draft_scene(db, user, project, body.scene)
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
        revision = snapshot_revision(db, project, "autosave")
        from .metrics.service import record_revision
        record_revision(db, project, revision, previous_scene)
        db.commit()
        return envelope(request, project_payload(project))

    @app.post("/v1/projects/{project_id}/revisions", status_code=201, response_model=Envelope[C.RevisionData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def create_revision(project_id: UUID, body: RevisionInput, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        project = owned(db, Project, project_id, user.tenant_id)
        enforce_edit_lease(db, project, request)
        ensure_revision(project, body.base_revision)
        try:
            revision = snapshot_revision(db, project, body.reason)
            db.commit()
        except IntegrityError:
            db.rollback()
            revision = db.scalar(select(Revision).where(Revision.project_id == project.id, Revision.number == body.base_revision))
        return envelope(request, {"id": revision.id, "project_id": revision.project_id, "number": revision.number, "scene": revision.scene, "created_at": revision.created_at.isoformat()})

    @app.post("/v1/assets", status_code=201, response_model=Envelope[C.AssetData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def upload_asset(request: Request, file: UploadFile = File(...), project_id: str | None=Form(default=None), db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        project=owned(db,Project,project_id,user.tenant_id) if project_id else None
        body = file.file.read(settings.upload_limit + 1)
        if len(body) > settings.upload_limit:
            raise APIError(413, "ASSET_TOO_LARGE", f"파일은 {settings.upload_limit // (1024 * 1024)}MiB 이하로 올려 주세요.")
        from .svg_import import prepare_image_upload, store_prepared_image
        prepared=prepare_image_upload(body,file.content_type)
        asset=store_prepared_image(db,storage,user,project,file.filename or "image",prepared)
        db.commit()
        return envelope(request, asset_payload(asset))

    @app.get("/v1/assets/{asset_id}/content", response_class=Response, responses=binary_responses("image/png", "image/jpeg", "image/webp", "application/octet-stream"))
    def asset_content(asset_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        asset = owned(db, Asset, asset_id, user.tenant_id)
        from .asset_reconciliation import ensure_asset_available
        ensure_asset_available(asset)
        from .svg_import import SVG_SOURCE
        if asset.source==SVG_SOURCE:
            if isinstance(storage,SupabaseStorage):
                return RedirectResponse(storage.signed_url(asset.storage_key,ttl=60,download_name="sanitized-source.svg"),status_code=307)
            return Response(content=storage.get(asset.storage_key),media_type="application/octet-stream",headers={"Content-Disposition":'attachment; filename="sanitized-source.svg"',"X-Content-Type-Options":"nosniff"})
        if isinstance(storage, SupabaseStorage):
            return RedirectResponse(storage.signed_url(asset.storage_key, ttl=60), status_code=307)
        return Response(content=storage.get(asset.storage_key), media_type=asset.content_type, headers={"Content-Disposition": "inline"})

    @app.post("/v1/projects/{project_id}/demo-background", status_code=201, response_model=Envelope[C.AssetData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def demo_background(project_id: UUID, body: DemoBackgroundInput, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        project=owned(db, Project, project_id, user.tenant_id)
        if not settings.demo_mode:
            raise APIError(422, "DEMO_DISABLED", "운영 환경에서는 데모 이미지를 생성할 수 없습니다.")
        # Generated once with GPT Image 2.5 (docs/demo-backgrounds.json) and shipped as static fixtures; no API call here.
        source = ROOT / "fixtures" / "demo-backgrounds" / f"{body.palette}.webp"
        raw = source.read_bytes()
        with Image.open(BytesIO(raw)) as image:
            width_px, height_px = image.size
        asset_id = str(uuid4())
        key = f"{user.tenant_id}/assets/{asset_id}"
        storage.put(key, raw, "image/webp")
        asset = Asset(id=asset_id, tenant_id=user.tenant_id, workspace_id=project.workspace_id,storage_key=key, original_name=f"예시 배경-{body.palette}.webp", content_type="image/webp", byte_size=len(raw), width_px=width_px, height_px=height_px, source="fixture")
        db.add(asset)
        db.commit()
        return envelope(request, {**asset_payload(asset), "demo": True, "label": "예시 배경 · 미리 생성한 AI 이미지(크레딧 차감 없음)", "credits_charged": 0})

    @app.post("/v1/exports", status_code=202, response_model=Envelope[J.JobData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def create_export(body: ExportInput, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        project = owned(db, Project, body.project_id, user.tenant_id)
        if body.kind == "editable":
            from .editable_exports import create_editable_export
            return envelope(request, job_payload(create_editable_export(db, user, body, request, project, project_payload, snapshot_revision)))
        enforce_edit_lease(db, project, request)
        if body.kind=="production":
            from .production_routes import create_production_export
            from .export_approvals import current_export_approvals
            job = create_production_export(db,user,body,request,project_payload,snapshot_revision)
            return envelope(request,job_payload(job,current_approval=current_export_approvals(db,[job]).get(job.id)))
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
        count = db.scalar(select(func.count()).select_from(Job).where(Job.tenant_id == user.tenant_id,Job.kind.in_(["review_export", "editable_export"]), Job.created_at > utcnow() - timedelta(hours=1)))
        if count >= 30:
            raise APIError(429, "EXPORT_RATE_LIMIT", "검토 파일 요청이 많습니다. 잠시 후 다시 시도해 주세요.", retryable=True)
        try:
            revision = snapshot_revision(db, project, "review_export")
            job = Job(tenant_id=user.tenant_id, project_id=project.id, revision_id=revision.id, operation_key=operation_key, request_hash=request_hash, snapshot={**project_payload(project), "revision_id": revision.id, "review_profile_id": BASIC_REVIEW_PROFILE_ID, "font_assets":freeze_fonts(db,user.tenant_id,project.scene)})
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

    @app.get("/v1/jobs/{job_id}", response_model=Envelope[J.JobData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def get_job(job_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        job = owned(db, Job, job_id, user.tenant_id)
        from .export_approvals import current_export_approvals
        from .export_intakes import batch_intake_summaries
        intakes = batch_intake_summaries(db, tenant_id=user.tenant_id, project_id=job.project_id, job_ids=[job.id])
        return envelope(request, job_payload(job, current_approval=current_export_approvals(db, [job]).get(job.id), current_intake=intakes.get(job.id)))

    @app.get("/v1/jobs/{job_id}/printer-intakes", response_model=Envelope[J.PrinterIntakeHistory], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def export_intake_history(job_id: UUID, request: Request, limit: int = Query(30, ge=1, le=100), before: str | None = Query(None, max_length=36), db=Depends(db_session)):
        user, _ = require_auth(request, db)
        job = owned(db, Job, job_id, user.tenant_id)
        if job.kind not in {"review_export", "production_export"}:
            raise APIError(404, "NOT_FOUND", "입고 기록 대상 출력 파일을 찾을 수 없습니다.")
        from .export_intakes import list_export_intakes
        return envelope(request, list_export_intakes(db, tenant_id=user.tenant_id, project_id=job.project_id, job_id=job.id, limit=limit, before=before))

    @app.post("/v1/jobs/{job_id}/retry", status_code=202, response_model=Envelope[J.JobData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def retry_job(job_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        job = owned(db, Job, job_id, user.tenant_id)
        if job.status != "failed" or job.kind not in {"review_export", "editable_export"}:
            raise APIError(409, "JOB_NOT_RETRYABLE", "실패한 작업만 다시 시도할 수 있습니다.")
        retry_values = {"status": "queued", "lease_id": None, "error": None, "updated_at": utcnow()}
        if job.kind == "editable_export":
            from .editable_exports import check_archive_access
            enforce_edit_lease(db, owned(db, Project, job.project_id, user.tenant_id), request)
            check_archive_access(db, user, job.snapshot, creating=True)
            retry_values["snapshot"] = {**job.snapshot, "actor_id": user.id}
            db.add(feature_models.AuditEvent(tenant_id=user.tenant_id, actor_id=user.id, action="editable_export_retried", entity_id=job.id, details={"revision_id": job.revision_id}))
        elif job.snapshot.get("print_output"):
            from .print_engine import check_test_access
            enforce_edit_lease(db, owned(db, Project, job.project_id, user.tenant_id), request)
            candidate = {**job.snapshot, "actor_id": user.id}
            check_test_access(db, user.tenant_id, candidate)
            retry_values["snapshot"] = candidate
        changed = db.execute(update(Job).where(Job.id == job.id, Job.status == "failed").values(**retry_values).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise APIError(409, "JOB_NOT_RETRYABLE", "다른 요청에서 이미 재시도했습니다.")
        db.commit()
        db.refresh(job)
        return envelope(request, job_payload(job))

    @app.get("/v1/projects/{project_id}/exports", response_model=Envelope[C.Items[J.JobData]], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def exports(project_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        owned(db, Project, project_id, user.tenant_id)
        rows = db.scalars(select(Job).where(Job.tenant_id == user.tenant_id, Job.project_id == str(project_id),Job.kind.in_(["review_export","production_export","editable_export"])).order_by(Job.created_at.desc()).limit(100)).all()
        from .export_approvals import current_export_approvals
        from .export_intakes import batch_intake_summaries
        approvals = current_export_approvals(db, rows)
        intakes = batch_intake_summaries(db, tenant_id=user.tenant_id, project_id=str(project_id), job_ids=[row.id for row in rows])
        return envelope(request, {"items": [job_payload(row, current_approval=approvals.get(row.id), current_intake=intakes.get(row.id)) for row in rows]})

    @app.get("/v1/exports/{job_id}/download", response_class=Response, responses=binary_responses("application/pdf", "application/zip"))
    def download(job_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        job = owned(db, Job, job_id, user.tenant_id)
        from .retention.deletion import ensure_object_available
        ensure_object_available(job)
        if job.kind not in {"review_export", "production_export", "editable_export"}:
            raise APIError(404, "NOT_FOUND", "요청한 출력 파일을 찾을 수 없습니다.")
        from .export_reconciliation import ensure_export_available,check_export_download
        ensure_export_available(job)
        if job.status != "succeeded" or not job.result or not job.result.get("storage_key"):
            raise APIError(409, "EXPORT_NOT_READY", "검토 파일을 준비하고 있습니다.", retryable=True)
        if job.kind == "editable_export":
            from .editable_exports import check_archive_access
            check_archive_access(db, user, job.snapshot)
        engine_test = job.result.get("format") == "print_engine_zip"
        extension="zip" if job.kind in {"production_export", "editable_export"} or engine_test else "pdf"
        name=f"phoenix-{job.kind}-{job.id}.{extension}"
        if engine_test:
            from .print_engine import check_test_access
            check_test_access(db, user.tenant_id, {**job.snapshot, "actor_id": user.id})
            name=f"phoenix-print-engine-test-{job.id}.zip"
        content=check_export_download(db,job,storage)
        if isinstance(storage, SupabaseStorage):
            return RedirectResponse(storage.signed_url(job.result["storage_key"], ttl=60, download_name=name), status_code=307)
        return Response(content=content, media_type="application/zip" if extension=="zip" else "application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/v1/internal/jobs/process", response_model=Envelope[C.WorkerResult], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    @app.post("/v1/internal/jobs/process", response_model=Envelope[C.WorkerResult], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def process_jobs(request: Request):
        supplied = request.headers.get("authorization", "")
        if not settings.worker_secret or not hmac.compare_digest(supplied, f"Bearer {settings.worker_secret}"):
            raise APIError(404, "NOT_FOUND", "요청한 항목을 찾을 수 없습니다.")
        from .worker_service import process_all_jobs
        breakdown=process_all_jobs(session_factory,storage,settings)
        if any(value=="retry_pending" for value in breakdown.values()):
            raise APIError(503,"WORKER_RETRY_PENDING","일부 백그라운드 작업을 다음 실행에서 다시 확인합니다.",{"breakdown":breakdown},retryable=True)
        maintenance = {"retention_checked", "orphan_candidates_checked", "orphan_files_deleted", "deletion_requests_checked", "requested_files_deleted", "font_uploads_cleaned", "exports_checked"}
        return envelope(request, {"processed":sum(v for k,v in breakdown.items() if isinstance(v,int) and k not in maintenance),"breakdown":breakdown})

    from .billing.routes import install_billing_routes
    from .business import install_business_routes
    from .registry import install_registry_routes
    from .ai_routes import install_ai_routes, prepare_ai_quote
    from .production_routes import install_production_routes, prepare_production_quote
    def prepare_quote(db,user,body):
        if body.get("project_id"):
            project = owned(db, Project, body["project_id"], user.tenant_id)
            enforce_edit_lease(db, project)
        if body["action"].startswith("image."): return prepare_ai_quote(db,user,body,settings)
        if body["action"].startswith("export.production"):
            return prepare_production_quote(db,user,body,settings,project_payload,snapshot_revision,storage=storage)
        raise APIError(422,"QUOTE_ACTION_INVALID","이 작업에는 견적이 필요하지 않습니다.")
    app.state.prepare_quote=prepare_quote
    install_billing_routes(app,db_session)
    install_business_routes(app,db_session,project_payload,snapshot_revision)
    from .operations.routes import install_operation_routes
    install_operation_routes(app,db_session)
    from .metrics.routes import install_metrics_routes
    install_metrics_routes(app,db_session)
    # Specific admin endpoints must precede the registry's /admin/{collection} route.
    from .retention.routes import install_retention_routes
    install_retention_routes(app,db_session)
    from .service_orders.routes import install_service_order_routes
    install_service_order_routes(app,db_session)
    from .inquiries import install_inquiry_routes
    install_inquiry_routes(app,db_session)
    from .print_engine import install_print_engine_routes
    install_print_engine_routes(app,db_session,project_payload,snapshot_revision)
    install_registry_routes(app,db_session,project_payload,snapshot_revision)
    from .structure_routes import install_structure_routes
    install_structure_routes(app,db_session,project_payload,snapshot_revision)
    install_ai_routes(app,db_session,job_payload,snapshot_revision)
    install_production_routes(app,db_session,owned,project_payload,snapshot_revision)
    from .uploads import install_upload_routes
    install_upload_routes(app,db_session,asset_payload)
    from .print_preparation import install_print_preparation_routes
    install_print_preparation_routes(app,db_session)
    from .image_quality import install_image_quality_routes
    install_image_quality_routes(app,db_session,asset_payload)
    from .image_merge import install_image_merge_routes
    install_image_merge_routes(app,db_session,asset_payload)
    install_editor_routes(app,db_session,project_payload,snapshot_revision,normalize_draft_scene)
    install_font_routes(app,db_session)
    from .workspace_views import install_workspace_views
    install_workspace_views(app,db_session,asset_payload)

    return app


app = create_app()
