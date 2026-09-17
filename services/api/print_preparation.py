"""Product barcode declarations and evidence-backed manufacturing readiness.

Checksum calculation never issues a GS1 number. Registration records are the
customer's declaration, not an automated GS1 ownership verification.
"""
from .contracts.base import Envelope, ERROR_RESPONSES
from .contracts import business as B
from .contracts import registry as R
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy import select, update

from .auth import require_auth
from .business import owned_record
from .database import utcnow
from .errors import APIError
from .feature_models import AuditEvent, RegistryVersion, Variant
from .models import Project, Tenant
from .geometry.barcodes import validate_ean13
from .geometry.validation import GeometryValidationError
from .exporters.preflight import CAPABILITIES, validate_output_requirements
from .registry import approved_conditions


VERIFICATION_LINKS = {
    "koreannet": "https://www.koreannet.or.kr/",
    "verified_by_gs1": "https://www.gs1kr.org/front/service/appl/VBGService.asp",
}
BARCODE_NOTICE = "번호 계산과 등록 기록은 GS1 발급·소유권 인증을 대신하지 않습니다. 정식으로 배정받은 번호만 사용하세요."
BARCODE_ALLOCATION_FIELDS = ("sku", "net_weight", "net_quantity", "net_unit", "ingredients", "allergens", "storage", "manufacturer")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ComposeBody(Body):
    company_prefix: str = Field(pattern=r"^[0-9]{6,11}$")
    item_reference: str = Field(pattern=r"^[0-9]{1,6}$")
    registered_prefix_confirmed: bool = Field(default=False, strict=True)


class RegistrationBody(Body):
    barcode: str = Field(pattern=r"^[0-9]{13}$")
    holder_name: str = Field(min_length=1, max_length=160)
    registration_reference: str = Field(min_length=3, max_length=2000)
    source_url: HttpUrl | None = None
    confirmed_rights: bool = Field(default=False, strict=True)
    expected_updated_at: datetime


def lock_catalog(db, tenant_id):
    # A real no-op UPDATE serializes both PostgreSQL and SQLite catalog writers.
    db.execute(update(Tenant).where(Tenant.id == tenant_id).values(name=Tenant.name))


def check_retail_barcode(value):
    try:
        validate_ean13(value)
    except GeometryValidationError as exc:
        raise APIError(422, exc.code, exc.message) from None
    if value.startswith("952"):
        raise APIError(422, "SAMPLE_BARCODE_NOT_RETAIL", "952 예시 번호는 실제 상품 번호로 등록할 수 없습니다.")
    return value


def duplicate_variants(db, tenant_id, value, exclude_id=None):
    if not value:
        return []
    return [{"id": row.id, "name": row.name} for row in db.scalars(
        select(Variant).where(Variant.tenant_id == tenant_id)
    ) if row.id != exclude_id and row.details.get("barcode") == value]


def ensure_unique_barcode(db, tenant_id, value, exclude_id=None):
    if duplicate_variants(db, tenant_id, value, exclude_id):
        raise APIError(409, "BARCODE_ALREADY_ASSIGNED", "이 번호를 사용하는 다른 상품 변형이 있습니다. 상품별 번호를 확인해 주세요.")


def barcode_registration_payload(db, variant):
    code = variant.details.get("barcode", "")
    registration = variant.details.get("barcode_registration")
    if not isinstance(registration, dict) or registration.get("barcode") != code:
        registration = None
    return {"variant_id": variant.id, "name": variant.name, "barcode": code,
            "updated_at": variant.updated_at.isoformat(), "registration": registration,
            "duplicate_variants": duplicate_variants(db, variant.tenant_id, code, variant.id),
            "verification_links": VERIFICATION_LINKS, "notice": BARCODE_NOTICE}


def check(key, label, status, detail):
    return {"key": key, "label": label, "status": status, "detail": detail}


def preparation_payload(db, project):
    variant = owned_record(db, Variant, project.product_variant_id, project.tenant_id) if project.product_variant_id else None
    registered = barcode_registration_payload(db, variant) if variant else None
    code = registered["barcode"] if registered else ""
    registration = registered["registration"] if registered else None
    scene_values = sorted({obj.get("barcode_value", "") for face in project.scene.get("faces", [])
                           for obj in face["objects"] if obj["type"] == "barcode"
                           and obj.get("visible", True) and obj.get("print_enabled", True)})
    checks = [check("variant", "상품 연결", "pass" if variant else "needs_input",
                    variant.name if variant else "상품 정보에서 브랜드와 상품 변형을 먼저 연결하세요.")]
    valid = False
    if code:
        try:
            check_retail_barcode(code)
            valid = True
        except APIError:
            pass
    checks.append(check("barcode_format", "상품 번호 형식", "pass" if valid else "needs_input",
                        code if valid else "체크 숫자가 맞는 정식 EAN-13 번호가 필요합니다."))
    checks.append(check("registration", "번호 사용 권한 기록", "pass" if registration else "needs_input",
                        "사용자가 등록 근거를 확인했습니다. GS1 자동 인증은 아닙니다." if registration else "보유 기업명과 번호 배정 근거를 기록하세요."))
    duplicate = registered["duplicate_variants"] if registered else []
    checks.append(check("duplicate", "상품별 번호 중복", "blocked" if duplicate else "pass" if code else "needs_input",
                        "다른 상품 변형과 번호가 겹칩니다." if duplicate else "현재 팀의 상품 목록 내 검사입니다."))
    checks.append(check("scene_match", "디자인과 상품 번호 일치", "pass" if valid and scene_values == [code] else "needs_input",
                        "인쇄할 모든 바코드가 상품 번호와 일치합니다." if valid and scene_values == [code] else "샘플 바코드를 정식 상품 번호로 교체하고 저장하세요."))
    conditions = approved_conditions(db, project, "readiness", project.scene.get("reviewed_face_ids", []))
    template, profile = conditions["template"], conditions["profile"]
    manufacturing = []
    for key, label, item in (("template", "승인 도면", template), ("profile", "승인 인쇄 조건", profile)):
        approved = item.get("status") == "approved" and item.get("is_demo") is False
        manufacturing.append(check(key, label, "pass" if approved else "needs_input",
                                    item.get("name", "승인된 자료를 선택하세요.") if approved else "제조사 자료 등록 및 증빙 확인 후 승인된 버전을 선택하세요."))
    material_match = bool(project.material and project.material == template.get("material") == profile.get("material"))
    manufacturing.append(check("material", "포장 소재 일치", "pass" if material_match else "needs_input",
                                project.material if material_match else "현재 소재와 도면·인쇄 조건의 승인 소재가 일치해야 합니다."))
    maker_match = bool(template.get("manufacturer") and template.get("manufacturer") == profile.get("manufacturer"))
    manufacturing.append(check("manufacturer", "제조사 일치", "pass" if maker_match else "needs_input",
                                template.get("manufacturer", "") if maker_match else "같은 제조사의 도면과 인쇄 조건을 연결하세요."))
    actual_dims = {key: getattr(project, key) for key in ("width_mm", "height_mm", "bottom_mm", "depth_mm") if getattr(project, key) is not None}
    approved_dims = template.get("approved_dimensions", {})
    dims_match = bool(approved_dims) and all(approved_dims.get(key) == val for key, val in actual_dims.items()) and all(key in actual_dims or value in (0, None) for key, value in approved_dims.items())
    manufacturing.append(check("dimensions", "완성 규격 일치", "pass" if dims_match else "needs_input",
                                "현재 치수가 승인 도면과 일치합니다." if dims_match else "폭·높이·바닥/깊이의 확정 치수를 확인하세요."))
    requirements = profile.get("requirements", {})
    output_issues = validate_output_requirements(requirements)["issues"]
    unsupported = [item["message"] for item in output_issues]
    output_complete = all(key in requirements for key in ("pdf_standard", "color_space", "font_mode"))
    manufacturing.append(check("output", "출력 형식 지원", "needs_input" if not requirements else "blocked" if unsupported else "pass" if output_complete else "needs_input",
                                "현재 출력기 미지원: " + ", ".join(unsupported) if unsupported else "최종 검수에서 모든 세부 조건을 다시 검사합니다."))
    if project.scene.get("holes") or project.scene.get("pouch_features"):
        manufacturing.append(check("finishing", "가공 승인", "blocked", "구멍·지퍼·절취 홈은 검토 구조입니다. 실제 제조 칼선·가공 승인과 제작 출력 지원이 필요합니다."))
    return {"base_revision": project.base_revision,
            "barcode": {"variant_id": variant.id if variant else None, "code": code, "registration": registration, "scene_values": scene_values, "checks": checks, "verification_links": VERIFICATION_LINKS},
            "manufacturing": {"template": template, "profile": profile, "material": project.material,
                              "dimensions": actual_dims, "checks": manufacturing, "admin_url": "/admin"},
            "capabilities": CAPABILITIES,
            "notice": "준비 상태는 제작 승인서가 아닙니다. 최종 검수는 저장된 최신 디자인·상품 정보·제조사 증빙을 다시 확인합니다."}


def install_print_preparation_routes(app, db_session):
    router = APIRouter(prefix="/v1", tags=["print-preparation"])

    def result(request, value):
        return {"data": value, "request_id": request.state.request_id}

    @router.post("/barcodes/gtin13/compose", response_model=Envelope[B.ComposedBarcode], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def compose(body: ComposeBody, request: Request, db=Depends(db_session)):
        require_auth(request, db, mutate=True)
        if not body.registered_prefix_confirmed:
            raise APIError(422, "PREFIX_CONFIRMATION_REQUIRED", "정식 배정받은 업체코드인지 먼저 확인하세요.")
        stem = body.company_prefix + body.item_reference
        if len(stem) != 12:
            raise APIError(422, "GTIN_STEM_LENGTH", "업체코드와 상품 참조번호는 합계 12자리여야 합니다. 앞의 0도 유지하세요.")
        digit = str((10 - (sum(map(int, stem[::2])) + 3 * sum(map(int, stem[1::2]))) % 10) % 10)
        value = check_retail_barcode(stem + digit)
        return result(request, {"value": value, "check_digit": digit, "issued": False, "notice": BARCODE_NOTICE})

    @router.get("/variants/{identity}/barcode-registration", response_model=Envelope[B.VariantBarcodeData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def registration(identity: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        variant = owned_record(db, Variant, identity, user.tenant_id)
        return result(request, barcode_registration_payload(db, variant))

    @router.put("/variants/{identity}/barcode-registration", response_model=Envelope[B.VariantBarcodeData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def register(identity: UUID, body: RegistrationBody, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        if not body.confirmed_rights:
            raise APIError(422, "BARCODE_RIGHTS_REQUIRED", "이 상품에 번호를 사용할 권한과 배정 근거를 확인해 주세요.")
        if body.source_url and (body.source_url.scheme != "https" or body.source_url.username or body.source_url.password or len(str(body.source_url)) > 2000):
            raise APIError(422, "BARCODE_SOURCE_URL", "인증 정보가 없는 HTTPS 출처 주소를 입력하세요.")
        code = check_retail_barcode(body.barcode)
        lock_catalog(db, user.tenant_id)
        variant = owned_record(db, Variant, identity, user.tenant_id)
        from .billing.policy import aware
        if aware(variant.updated_at) != aware(body.expected_updated_at):
            raise APIError(409, "VARIANT_CHANGED", "상품 정보가 변경되었습니다. 최신 정보를 불러와 다시 확인하세요.")
        ensure_unique_barcode(db, user.tenant_id, code, variant.id)
        declaration = {"barcode": code, "holder_name": body.holder_name,
                       "registration_reference": body.registration_reference,
                       "source_url": str(body.source_url) if body.source_url else None,
                       "confirmed_by": user.id, "confirmed_at": utcnow().isoformat(),
                       "verification": "user_attested"}
        variant.details = {**variant.details, "barcode": code, "barcode_registration": declaration}
        variant.updated_at = utcnow()
        db.add(AuditEvent(tenant_id=user.tenant_id, actor_id=user.id, action="barcode_registration_saved", entity_id=variant.id,
                          details={"barcode": code, "verification": "user_attested"}))
        db.commit()
        return result(request, barcode_registration_payload(db, variant))

    @router.get("/projects/{identity}/print-preparation", response_model=Envelope[R.PrintPreparation], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def preparation(identity: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        project = owned_record(db, Project, identity, user.tenant_id)
        return result(request, preparation_payload(db, project))

    app.include_router(router)
