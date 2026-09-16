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

from fastapi import Depends, FastAPI, File, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from PIL import Image, ImageDraw, UnidentifiedImageError
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from .auth import COOKIE_NAME, DUMMY_HASH, PASSWORDS, auth_payload, check_origin, create_session, hash_token, require_auth, throttle_auth, verify_password
from .config import Settings
from .database import Base, build_database, utcnow
from .errors import APIError
from .geometry import GeometryValidationError, validate_dimensions
from .mail import issue_email_token, mail_available
from .models import Asset, AuthToken, Job, LoginSession, Project, Revision, Tenant, User
from .schemas import AuthTokenInput, CreateProjectInput, DemoBackgroundInput, EmailInput, ExportInput, LoginInput, RegisterInput, ResetPasswordInput, RevisionInput, SaveDraftInput
from .storage import SupabaseStorage, build_storage

logger = logging.getLogger("phoenix.api")


def envelope(request, data):
    return {"data": data, "request_id": request.state.request_id}


def project_payload(project):
    geometry = validate_dimensions(project.width_mm, project.height_mm, "mm")
    return {"id": project.id, "name": project.name, "product_name": project.product_name, "brand_name": project.brand_name, "description": project.description, "width_mm": project.width_mm, "height_mm": project.height_mm, "template_id": project.template_id, "base_revision": project.base_revision, "scene": project.scene, "geometry": geometry, "created_at": project.created_at.isoformat(), "updated_at": project.updated_at.isoformat(), "approval_status": "demo_unapproved", "review_only": True}


def asset_payload(asset):
    return {"id": asset.id, "name": asset.original_name, "content_type": asset.content_type, "byte_size": asset.byte_size, "width_px": asset.width_px, "height_px": asset.height_px, "source": asset.source, "url": f"/v1/assets/{asset.id}/content"}


def job_payload(job):
    result = {key: value for key, value in job.result.items() if key != "storage_key"} if job.result else None
    return {"id": job.id, "project_id": job.project_id, "kind": job.kind, "status": job.status, "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat(), "result": result, "error": job.error, "download_url": f"/v1/exports/{job.id}/download" if job.status == "succeeded" else None}


def owned(db, model, item_id, tenant_id):
    item = db.scalar(select(model).where(model.id == str(item_id), model.tenant_id == tenant_id))
    if item is None:
        raise APIError(404, "NOT_FOUND", "요청한 항목을 찾을 수 없습니다.")
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
    w, h = project.width_mm, project.height_mm
    common = {"rotation_deg": 0, "visible": True, "print_enabled": True}
    def label(identity, face, value, y, size, color="#243A32"):
        return {**common, "id": identity, "type": "text", "face_id": face, "x_mm": 16, "y_mm": y, "width_mm": w - 32, "height_mm": max(12, size * 0.8), "z_index": 5, "text": value, "font_size_pt": size, "font_id": "NotoSansKR", "color": color, "align": "center"}
    front = [label("brand", "front", project.brand_name or "브랜드 이름", h * 0.18, 13), label("product", "front", project.product_name, h * 0.34, 27), label("front-note", "front", "문구를 클릭해 직접 수정하세요", h * 0.69, 10)]
    back = [label("back-title", "back", project.product_name, h * 0.16, 16), label("back-info", "back", "상품 정보\n\n원재료 · 확인 필요\n중량 · 확인 필요\n보관 방법 · 확인 필요\n제조사 · 확인 필요", h * 0.31, 10)]
    back[-1]["height_mm"] = h * 0.5
    geometry = validate_dimensions(w, h, "mm")
    return {"schema_version": "1.0", "template_version_id": "three-side-seal-demo-v1", "geometry_hash": geometry["geometry_hash"], "active_face_id": "front", "faces": [{"id": "front", "name": "앞면", "width_mm": w, "height_mm": h, "background": "#F2EFE5", "objects": front}, {"id": "back", "name": "뒷면", "width_mm": w, "height_mm": h, "background": "#F2EFE5", "objects": back}]}


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
        return envelope(request, {"demo_mode": settings.demo_mode, "ai_provider": "fixture" if settings.demo_mode else "unconfigured", "billing_provider": "disabled", "production_export_enabled": False, "review_export_credits": 0, "upload_max_bytes": settings.upload_limit, "supported_upload_types": ["image/png", "image/jpeg", "image/webp"], "email_verification_available": mail_available(settings), "mail_mode": "smtp" if settings.smtp_host else "local_outbox" if mail_available(settings) else "unconfigured"})

    @app.post("/v1/auth/register", status_code=201)
    def register(body: RegisterInput, request: Request, response: Response, db=Depends(db_session)):
        check_origin(request)
        email = str(body.email).lower()
        throttle_auth(db, email)
        tenant = Tenant(name=f"{body.name}의 작업 공간")
        db.add(tenant)
        db.flush()
        user = User(tenant_id=tenant.id, name=body.name, email=email, password_hash=PASSWORDS.hash(body.password), role="owner")
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise APIError(409, "EMAIL_UNAVAILABLE", "이 이메일로 가입할 수 없습니다. 로그인해 주세요.")
        session = create_session(db, user, response, settings)
        db.commit()
        delivery = "unconfigured"
        if mail_available(settings):
            try:
                delivery = issue_email_token(db, user, "verify", settings)
            except APIError:
                db.rollback()
                delivery = "unavailable"
        return envelope(request, {**auth_payload(db, user, session), "verification_delivery": delivery})

    @app.post("/v1/auth/login")
    def login(body: LoginInput, request: Request, response: Response, db=Depends(db_session)):
        check_origin(request)
        email = str(body.email).lower()
        throttle_auth(db, email)
        user = db.scalar(select(User).where(User.email == email))
        valid = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
        if user is None or not valid:
            raise APIError(401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호를 확인해 주세요.")
        if PASSWORDS.check_needs_rehash(user.password_hash):
            user.password_hash = PASSWORDS.hash(body.password)
        session = create_session(db, user, response, settings)
        db.commit()
        return envelope(request, auth_payload(db, user, session))

    @app.post("/v1/auth/logout")
    def logout(request: Request, response: Response, db=Depends(db_session)):
        _, session = require_auth(request, db, mutate=True, authorize_write=False)
        db.delete(session)
        db.commit()
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
        return envelope(request, {"logged_out": True})

    @app.post("/v1/auth/request-verification")
    def request_verification(request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True, authorize_write=False)
        if user.email_verified_at is not None:
            return envelope(request, {"email_verified": True})
        throttle_auth(db, "verify:" + user.email)
        delivery = issue_email_token(db, user, "verify", settings)
        return envelope(request, {"email_verified": False, "delivery": delivery})

    def consume_token(db, raw_token, kind):
        # Single SQL claim prevents replay even when two requests arrive together.
        now = utcnow()
        user_id = db.scalar(update(AuthToken).where(AuthToken.token_hash == hash_token(raw_token), AuthToken.kind == kind, AuthToken.consumed_at.is_(None), AuthToken.expires_at > now).values(consumed_at=now).returning(AuthToken.user_id))
        if user_id is None:
            raise APIError(422, "TOKEN_INVALID_OR_EXPIRED", "링크가 만료되었거나 이미 사용되었습니다. 새 링크를 요청해 주세요.")
        user = db.get(User, user_id)
        if user is None:
            raise APIError(422, "TOKEN_INVALID_OR_EXPIRED", "사용할 수 없는 링크입니다.")
        return user

    @app.post("/v1/auth/verify-email")
    def verify_email(body: AuthTokenInput, request: Request, db=Depends(db_session)):
        check_origin(request)
        user = consume_token(db, body.token, "verify")
        user.email_verified_at = utcnow()
        db.commit()
        return envelope(request, {"email_verified": True})

    @app.post("/v1/auth/request-password-reset", status_code=202)
    def request_password_reset(body: EmailInput, request: Request, db=Depends(db_session)):
        check_origin(request)
        if not mail_available(settings):
            raise APIError(503, "MAIL_NOT_CONFIGURED", "이메일 인증 서비스 연결을 준비하고 있습니다.")
        email = str(body.email).lower()
        throttle_auth(db, "reset:" + email)
        user = db.scalar(select(User).where(User.email == email))
        if user is not None:
            try:
                issue_email_token(db, user, "reset", settings)
            except APIError:
                db.rollback()
                logger.warning("password_reset_delivery_unavailable request_id=%s", request.state.request_id)
        return envelope(request, {"accepted": True, "message": "가입한 이메일인 경우 비밀번호 재설정 링크를 보내 드립니다."})

    @app.post("/v1/auth/reset-password")
    def reset_password(body: ResetPasswordInput, request: Request, response: Response, db=Depends(db_session)):
        check_origin(request)
        user = consume_token(db, body.token, "reset")
        user.password_hash = PASSWORDS.hash(body.password)
        db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        db.commit()
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
        return envelope(request, {"password_reset": True, "requires_login": True})

    @app.get("/v1/me")
    def me(request: Request, db=Depends(db_session)):
        user, session = require_auth(request, db)
        return envelope(request, auth_payload(db, user, session))

    @app.get("/v1/credits")
    def credits(request: Request, db=Depends(db_session)):
        require_auth(request, db)
        return envelope(request, {"balance": 0, "reserved": 0, "mode": "demo" if settings.demo_mode else "unconfigured", "review_export_cost": 0, "payments_enabled": False, "message": "개발 데모에서는 과금과 크레딧 차감이 발생하지 않습니다."})

    @app.get("/v1/templates")
    def templates(request: Request):
        return envelope(request, {"items": [{"id": "three-side-seal", "name": "3면 실링 봉투", "version": "demo-1", "status": "demo", "approval_status": "demo_unapproved", "review_only": True, "description": "앞면과 뒷면을 편집하는 데모 구조 · 제조사 미승인", "faces": ["front", "back"], "default_width_mm": 160, "default_height_mm": 230, "min_width_mm": 60, "max_width_mm": 600, "min_height_mm": 80, "max_height_mm": 800, "seal_mm": 10, "safe_mm": 5, "bleed_mm": 3}]})

    @app.get("/v1/projects")
    def projects(request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        rows = db.scalars(select(Project).where(Project.tenant_id == user.tenant_id).order_by(Project.updated_at.desc()).limit(100)).all()
        return envelope(request, {"items": [project_payload(row) for row in rows]})

    @app.post("/v1/projects", status_code=201)
    def create_project(body: CreateProjectInput, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        validate_dimensions(body.width_mm, body.height_mm, "mm")
        project = Project(tenant_id=user.tenant_id, created_by=user.id, **body.model_dump())
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
        geometry = validate_dimensions(project.width_mm, project.height_mm, "mm")
        scene["template_version_id"] = "three-side-seal-demo-v1"
        scene["geometry_hash"] = geometry["geometry_hash"]
        for face in scene["faces"]:
            if face["width_mm"] != project.width_mm or face["height_mm"] != project.height_mm:
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
        db.commit()
        db.refresh(project)
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
    def upload_asset(request: Request, file: UploadFile = File(...), db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
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
        asset = Asset(id=asset_id, tenant_id=user.tenant_id, storage_key=key, original_name=Path(file.filename or "image").name[:160], content_type=file.content_type, byte_size=len(body), width_px=width, height_px=height)
        db.add(asset)
        db.commit()
        return envelope(request, asset_payload(asset))

    @app.get("/v1/assets/{asset_id}/content")
    def asset_content(asset_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        asset = owned(db, Asset, asset_id, user.tenant_id)
        if isinstance(storage, SupabaseStorage):
            return RedirectResponse(storage.signed_url(asset.storage_key, ttl=60), status_code=307)
        return Response(content=storage.get(asset.storage_key), media_type=asset.content_type, headers={"Content-Disposition": "inline"})

    @app.post("/v1/projects/{project_id}/demo-background", status_code=201)
    def demo_background(project_id: UUID, body: DemoBackgroundInput, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        owned(db, Project, project_id, user.tenant_id)
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
        asset = Asset(id=asset_id, tenant_id=user.tenant_id, storage_key=key, original_name=f"데모 배경-{body.palette}.png", content_type="image/png", byte_size=len(raw), width_px=768, height_px=1024, source="fixture")
        db.add(asset)
        db.commit()
        return envelope(request, {**asset_payload(asset), "demo": True, "label": "자체 제작 데모 이미지 · AI 생성 아님", "credits_charged": 0})

    @app.post("/v1/exports", status_code=202)
    def create_export(body: ExportInput, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        project = owned(db, Project, body.project_id, user.tenant_id)
        ensure_revision(project, body.base_revision)
        operation_key = request.headers.get("idempotency-key", f"review:{project.id}:{body.base_revision}")
        if not 1 <= len(operation_key) <= 160:
            raise APIError(422, "IDEMPOTENCY_KEY_INVALID", "요청 식별자가 올바르지 않습니다.")
        request_hash = sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        existing = db.scalar(select(Job).where(Job.tenant_id == user.tenant_id, Job.operation_key == operation_key))
        if existing:
            if existing.request_hash != request_hash:
                raise APIError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 식별자로 다른 작업을 보낼 수 없습니다.")
            return envelope(request, job_payload(existing))
        count = db.scalar(select(func.count()).select_from(Job).where(Job.tenant_id == user.tenant_id, Job.created_at > utcnow() - timedelta(hours=1)))
        if count >= 30:
            raise APIError(429, "EXPORT_RATE_LIMIT", "검토 파일 요청이 많습니다. 잠시 후 다시 시도해 주세요.", retryable=True)
        try:
            revision = snapshot_revision(db, project, "review_export")
            job = Job(tenant_id=user.tenant_id, project_id=project.id, revision_id=revision.id, operation_key=operation_key, request_hash=request_hash, snapshot={**project_payload(project), "revision_id": revision.id})
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
        if job.status != "failed":
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
        rows = db.scalars(select(Job).where(Job.tenant_id == user.tenant_id, Job.project_id == str(project_id)).order_by(Job.created_at.desc()).limit(100)).all()
        return envelope(request, {"items": [job_payload(row) for row in rows]})

    @app.get("/v1/exports/{job_id}/download")
    def download(job_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        job = owned(db, Job, job_id, user.tenant_id)
        if job.status != "succeeded" or not job.result or not job.result.get("storage_key"):
            raise APIError(409, "EXPORT_NOT_READY", "검토 파일을 준비하고 있습니다.", retryable=True)
        if isinstance(storage, SupabaseStorage):
            return RedirectResponse(storage.signed_url(job.result["storage_key"], ttl=60, download_name=f"phoenix-review-{job.id}.pdf"), status_code=307)
        content = storage.get(job.result["storage_key"])
        return Response(content=content, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="phoenix-review-{job.id}.pdf"'})

    @app.get("/v1/internal/jobs/process")
    @app.post("/v1/internal/jobs/process")
    def process_jobs(request: Request):
        supplied = request.headers.get("authorization", "")
        if not settings.worker_secret or not hmac.compare_digest(supplied, f"Bearer {settings.worker_secret}"):
            raise APIError(404, "NOT_FOUND", "요청한 항목을 찾을 수 없습니다.")
        from .jobs import process_pending_jobs
        return envelope(request, {"processed": process_pending_jobs(session_factory, storage, limit=2)})

    return app


app = create_app()
