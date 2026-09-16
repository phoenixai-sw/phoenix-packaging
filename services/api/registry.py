"""Evidence-backed print conditions. Client-supplied approval flags are never trusted."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, File, Request, UploadFile, Response
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, update
from .auth import require_auth
from .database import utcnow
from .errors import APIError
from .feature_models import RegistryVersion, Evidence, AuditEvent, ProviderAttempt, IntakeRecord, Product, Variant, Brand
from .models import Job, Project, User
from .business import owned_record
from .geometry import geometry_for_scene
from .printer_intakes import intake_metadata, intake_metrics


def registry_payload(row):
    return {"id":row.id,"kind":row.kind,"name":row.name,"manufacturer":row.manufacturer,"status":row.status,"is_demo":row.is_demo,**row.details,"approval":row.approval}


def approved_conditions(db,project,revision_id,reviewed_face_ids):
    conditions={"registry_verified":True,"confirmed_revision_id":str(revision_id),"reviewed_face_ids":list(reviewed_face_ids),"material":project.material}
    for key,identity,kind in (("template",project.template_version_id or project.scene.get("template_version_id"),"template"),("profile",project.print_profile_version_id,"profile")):
        row=db.get(RegistryVersion,identity) if identity else None
        if row and row.kind==kind:
            value=registry_payload(row)
            evidence=db.get(Evidence,row.approval.get("evidence_asset_id")) if row.approval else None
            if not evidence or evidence.sha256!=row.approval.get("evidence_sha256"):
                value["status"]="evidence_missing"
            conditions[key]=value
        else:
            conditions[key]={"id":identity,"status":"unapproved","is_demo":True}
    return conditions


def canonical_production_identity(db,project):
    if not project.brand_id or not project.product_variant_id: raise APIError(422,"PRODUCTION_IDENTITY_REQUIRED","브랜드와 상품 변형을 연결해 주세요.")
    variant=owned_record(db,Variant,project.product_variant_id,project.tenant_id)
    product=owned_record(db,Product,variant.product_id,project.tenant_id)
    owned_record(db,Brand,project.brand_id,project.tenant_id)
    if product.brand_id!=project.brand_id: raise APIError(422,"BRAND_VARIANT_MISMATCH","연결한 브랜드와 상품이 일치하지 않습니다.")
    version=db.get(RegistryVersion,project.template_version_id)
    if not version or version.kind!="template" or not version.details.get("billing_family_key"):
        raise APIError(422,"APPROVED_TEMPLATE_REQUIRED","승인된 제작 템플릿을 선택해 주세요.")
    barcode=variant.details.get("barcode","")
    actual={obj.get("barcode_value","") for face in project.scene["faces"] for obj in face["objects"] if obj["type"]=="barcode" and obj.get("visible",True) and obj.get("print_enabled",True)}
    if actual and actual!={barcode}: raise APIError(422,"BARCODE_VARIANT_MISMATCH","편집기의 바코드와 상품 변형의 바코드를 일치시켜 주세요.")
    holes=[]
    for hole in project.scene.get("holes",[]):
        x=hole["center_x_mm"] if hole["face_id"]!="back" else project.width_mm-hole["center_x_mm"]
        item={"face_id":"front" if hole["face_id"] in {"front","back"} else hole["face_id"],"x_mm":round(x,4),"y_mm":hole["center_y_mm"],"diameter_mm":hole["diameter_mm"]}
        if item not in holes: holes.append(item)
    return {"brand_id":project.brand_id,"product_variant_id":variant.id,"billing_family_key":version.details["billing_family_key"],"content_amount":variant.details.get("net_quantity"),"content_unit":variant.details.get("net_unit"),"barcode":{"symbology":"EAN13","data":barcode} if barcode else {},"width_mm":project.width_mm,"height_mm":project.height_mm,"bottom_mm":project.bottom_mm or 0,"depth_mm":project.depth_mm or 0,"holes":holes}


class Body(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)


class VersionBody(Body):
    name: str=Field(min_length=1,max_length=160)
    manufacturer: str=Field(min_length=1,max_length=160)
    is_demo: bool=True
    geometry_template_id: Literal["three-side-seal","stand-up-pouch","folding-box"] | None=None
    billing_family_key: str | None=Field(default=None,min_length=1,max_length=100)
    requirements: dict=Field(default_factory=dict)
    approved_dimensions: dict=Field(default_factory=dict)
    source: str=Field(min_length=1,max_length=2000)
    license: str=Field(min_length=1,max_length=2000)
    material: str=Field(default="",max_length=120)


class ApprovalBody(Body):
    evidence_asset_id: UUID
    notes: str=Field(min_length=3,max_length=4000)
    approved_by_name: str=Field(min_length=1,max_length=160)


class RevokeBody(Body):
    reason: str=Field(min_length=3,max_length=4000)


class SettingsBody(Body):
    base_revision: int=Field(ge=1)
    template_version_id: str | None=Field(default=None,max_length=100)
    print_profile_version_id: str | None=Field(default=None,max_length=100)
    material: str | None=Field(default=None,max_length=120)


class IntakeBody(Body):
    project_id: UUID
    job_id: UUID | None=None
    status: Literal["submitted","accepted","rejected"]
    category: Literal["geometry","font","color","resolution","content","file","other"]
    manufacturer: str=Field(min_length=1,max_length=160)
    notes: str=Field(min_length=3,max_length=4000)
    evidence_id: UUID | None=None
    record_source: Literal["manufacturer","test"]="test"
    rejection_kind: Literal["technical","aesthetic"] | None=None

    @model_validator(mode="after")
    def validate_rejection_kind(self):
        if self.status=="rejected" and self.rejection_kind is None:
            raise ValueError("수정 요청은 기술적 반려와 미적 선호 수정을 구분해 주세요.")
        if self.status!="rejected" and self.rejection_kind is not None:
            raise ValueError("반려 구분은 수정 요청 상태에서만 지정할 수 있습니다.")
        return self


def install_registry_routes(app,db_session,project_payload,snapshot_revision):
    router=APIRouter(prefix="/v1",tags=["registry"])
    settings=app.state.settings
    def result(request,data): return {"data":data,"request_id":request.state.request_id}
    def admin(request,db,mutate=False):
        user,_=require_auth(request,db,mutate=mutate)
        if not user.is_admin: raise APIError(403,"ADMIN_REQUIRED","플랫폼 관리자 권한이 필요합니다.")
        return user
    def kind_for(collection):
        if collection not in {"template-versions","print-profiles"}: raise APIError(404,"NOT_FOUND","요청한 항목을 찾을 수 없습니다.")
        return "template" if collection=="template-versions" else "profile"
    def get_version(db,collection,identity):
        row=db.get(RegistryVersion,identity)
        if not row or row.kind!=kind_for(collection): raise APIError(404,"NOT_FOUND","버전을 찾을 수 없습니다.")
        return row
    def audit(db,user,action,identity,details=None): db.add(AuditEvent(tenant_id=user.tenant_id,actor_id=user.id,action=action,entity_id=identity,details=details or {}))

    @router.get("/print-profiles")
    def profiles(request:Request,db=Depends(db_session)):
        require_auth(request,db)
        rows=db.scalars(select(RegistryVersion).where(RegistryVersion.kind=="profile",RegistryVersion.status=="approved",RegistryVersion.is_demo.is_(False)))
        return result(request,{"items":[registry_payload(r) for r in rows]})

    @router.patch("/projects/{identity}/settings")
    def project_settings(identity:UUID,body:SettingsBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True);project=owned_record(db,Project,identity,user.tenant_id)
        values=body.model_dump(exclude_unset=True,exclude={"base_revision"});scene=deepcopy(project.scene)
        for field,kind in (("template_version_id","template"),("print_profile_version_id","profile")):
            if field in values and values[field]:
                row=db.get(RegistryVersion,values[field])
                if not row or row.kind!=kind or row.status!="approved" or row.is_demo: raise APIError(422,"APPROVED_VERSION_REQUIRED","승인된 버전만 선택할 수 있습니다.")
                if kind=="template" and row.details.get("geometry_template_id")!=project.template_id: raise APIError(422,"GEOMETRY_FAMILY_MISMATCH","포장 구조와 승인 도면이 일치하지 않습니다.")
        if "template_version_id" in values: scene["template_version_id"]=values["template_version_id"] or project.template_id+"-demo-v1"
        scene["template_kind"]=project.template_id
        scene["geometry_hash"]=geometry_for_scene(scene)["geometry_hash"];scene["reviewed_face_ids"]=[]
        won=db.execute(update(Project).where(Project.id==project.id,Project.base_revision==body.base_revision).values(**values,scene=scene,base_revision=body.base_revision+1,updated_at=utcnow()).execution_options(synchronize_session=False))
        if won.rowcount!=1: raise APIError(409,"REVISION_CONFLICT","프로젝트가 변경되었습니다. 최신 내용을 확인해 주세요.")
        db.refresh(project);snapshot_revision(db,project,"print_settings");db.commit();return result(request,project_payload(project))

    @router.get("/admin/overview")
    def overview(request:Request,db=Depends(db_session)):
        admin(request,db)
        versions=list(db.scalars(select(RegistryVersion)))
        billing=app.state.billing_settings.capabilities()
        readiness=[{"key":"google_login","label":"Google 로그인","ready":bool(settings.google_client_id),"detail":"Google 계정 로그인 연결됨" if settings.google_client_id else "Google 로그인 클라이언트 연결 필요"},{"key":"payments","label":"결제 연동","ready":billing["checkout_available"],"detail":billing["provider"] if "provider" in billing else app.state.billing_settings.provider},{"key":"policy","label":"운영 정책 확정","ready":settings.policy_approved,"detail":"약관·환불·개인정보 운영 정책 승인"},{"key":"templates","label":"제조사 승인 도면","ready":any(v.kind=="template" and v.status=="approved" and not v.is_demo for v in versions),"detail":"제조사 증빙과 별도 관리자 승인 필요"},{"key":"profiles","label":"인쇄 조건","ready":any(v.kind=="profile" and v.status=="approved" and not v.is_demo for v in versions),"detail":"지원 가능한 출력 조건만 승인"},{"key":"ai","label":"AI 제공자","ready":settings.ai_provider=="openai","detail":settings.image_model if settings.ai_provider=="openai" else settings.ai_provider}]
        events=list(db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(100)))
        costs=db.execute(select(ProviderAttempt.provider,ProviderAttempt.model,func.count(),func.sum(ProviderAttempt.cost_usd)).group_by(ProviderAttempt.provider,ProviderAttempt.model)).all()
        intake=db.execute(select(IntakeRecord.status,IntakeRecord.category,func.count()).group_by(IntakeRecord.status,IntakeRecord.category)).all()
        return result(request,{"readiness":readiness,"counts":{"users":db.scalar(select(func.count()).select_from(User)),"projects":db.scalar(select(func.count()).select_from(Project)),"jobs":db.scalar(select(func.count()).select_from(Job))},"audit":[{"id":e.id,"action":e.action,"entity_id":e.entity_id,"details":e.details,"created_at":e.created_at.isoformat()} for e in events],"provider_costs":[{"provider":p,"model":m,"attempts":n,"cost_usd":c,"estimated":True} for p,m,n,c in costs],"intake_stats":[{"status":s,"category":c,"count":n} for s,c,n in intake],"intake_metrics":intake_metrics(db)})

    @router.post("/admin/evidence",status_code=201)
    def evidence(request:Request,file:UploadFile=File(...),db=Depends(db_session)):
        user=admin(request,db,True);content=file.file.read(settings.upload_limit+1)
        if not content or len(content)>settings.upload_limit: raise APIError(413,"EVIDENCE_SIZE","증빙 파일 크기를 확인해 주세요.")
        if file.content_type=="application/pdf":
            import pypdfium2 as pdfium
            try:
                with pdfium.PdfDocument(content) as pdf:
                    if not 0<len(pdf)<=200: raise ValueError()
                    # PDF is a private attachment, never executed or inserted as browser HTML.
                    if b"/JavaScript" in content or b"/JS" in content or b"/Launch" in content: raise ValueError()
            except Exception: raise APIError(422,"EVIDENCE_INVALID","암호화·실행 동작이 없는 PDF를 올려 주세요.") from None
        elif file.content_type in {"image/png","image/jpeg"}:
            try:
                with Image.open(BytesIO(content)) as image:
                    if Image.MIME.get(image.format)!=file.content_type or image.width*image.height>40000000: raise ValueError()
                    image.verify()
            except Exception: raise APIError(422,"EVIDENCE_INVALID","유효한 PNG/JPEG 증빙을 올려 주세요.") from None
        else: raise APIError(422,"EVIDENCE_TYPE","PDF, PNG, JPEG 증빙을 지원합니다.")
        identity=str(uuid4());key=f"{user.tenant_id}/evidence/{identity}";app.state.storage.put(key,content,file.content_type)
        row=Evidence(id=identity,tenant_id=user.tenant_id,uploaded_by=user.id,storage_key=key,name=Path(file.filename or "evidence").name[:180],sha256=sha256(content).hexdigest(),content_type=file.content_type,byte_size=len(content));db.add(row);audit(db,user,"evidence_uploaded",identity);db.commit()
        return result(request,{"id":identity,"name":row.name,"sha256":row.sha256})

    @router.get("/admin/evidence/{identity}/content")
    def evidence_content(identity:UUID,request:Request,db=Depends(db_session)):
        admin(request,db);row=db.get(Evidence,str(identity))
        if not row: raise APIError(404,"NOT_FOUND","증빙을 찾을 수 없습니다.")
        return Response(app.state.storage.get(row.storage_key),media_type="application/octet-stream",headers={"Content-Disposition":f'attachment; filename="evidence-{row.id}"'})

    @router.get("/admin/{collection}")
    def versions(collection:str,request:Request,db=Depends(db_session)):
        admin(request,db);kind=kind_for(collection)
        return result(request,{"items":[registry_payload(r) for r in db.scalars(select(RegistryVersion).where(RegistryVersion.kind==kind).order_by(RegistryVersion.created_at.desc()))]})
    @router.post("/admin/{collection}",status_code=201)
    def create_version(collection:str,body:VersionBody,request:Request,db=Depends(db_session)):
        user=admin(request,db,True);kind=kind_for(collection)
        if kind=="template" and (not body.geometry_template_id or not body.billing_family_key): raise APIError(422,"TEMPLATE_FIELDS_REQUIRED","구조 종류와 과금 구조 식별자가 필요합니다.")
        if kind=="profile" and not body.requirements: raise APIError(422,"PRINT_REQUIREMENTS_REQUIRED","제조사 인쇄 조건을 입력해 주세요.")
        row=RegistryVersion(kind=kind,name=body.name,manufacturer=body.manufacturer,is_demo=body.is_demo,created_by=user.id,details=body.model_dump(exclude={"name","manufacturer","is_demo"}));db.add(row);db.flush();audit(db,user,"registry_created",row.id);db.commit();return result(request,registry_payload(row))
    @router.post("/admin/{collection}/{identity}/approve")
    def approve(collection:str,identity:str,body:ApprovalBody,request:Request,db=Depends(db_session)):
        user=admin(request,db,True)
        row=db.scalar(select(RegistryVersion).where(RegistryVersion.id==identity,RegistryVersion.kind==kind_for(collection)).with_for_update().execution_options(populate_existing=True))
        if not row: raise APIError(404,"NOT_FOUND","버전을 찾을 수 없습니다.")
        if row.is_demo: raise APIError(422,"DEMO_NOT_APPROVABLE","데모 구조는 제작용으로 승인할 수 없습니다. 제조사 도면의 별도 버전을 등록해 주세요.")
        if row.status!="draft": raise APIError(409,"VERSION_IMMUTABLE","승인·폐기한 버전은 수정할 수 없습니다. 새 버전을 등록해 주세요.")
        if not row.details.get("material"): raise APIError(422,"MATERIAL_REQUIRED","제조사가 승인한 재질을 기록해 주세요.")
        if row.kind=="template":
            from .geometry import build_geometry
            dims=row.details.get("approved_dimensions",{})
            build_geometry(row.details["geometry_template_id"],dims.get("width_mm"),dims.get("height_mm"),bottom_mm=dims.get("bottom_mm"),depth_mm=dims.get("depth_mm"))
        evidence=db.get(Evidence,str(body.evidence_asset_id))
        if not evidence: raise APIError(404,"EVIDENCE_REQUIRED","제조사 승인 증빙을 먼저 등록해 주세요.")
        if sha256(app.state.storage.get(evidence.storage_key)).hexdigest()!=evidence.sha256: raise APIError(409,"EVIDENCE_CHANGED","증빙 파일의 무결성을 확인하지 못했습니다.")
        approval={"evidence_asset_id":evidence.id,"evidence_sha256":evidence.sha256,"approved_by":user.id,"approved_by_name":body.approved_by_name,"approved_at":utcnow().isoformat(),"source":row.details["source"],"license":row.details["license"],"notes":body.notes}
        changed=db.execute(update(RegistryVersion).where(RegistryVersion.id==row.id,RegistryVersion.status=="draft").values(status="approved",approval=approval,updated_at=utcnow()).execution_options(synchronize_session=False))
        if changed.rowcount!=1: raise APIError(409,"VERSION_IMMUTABLE","승인 중 도면 상태가 변경되었습니다. 새 버전을 등록해 주세요.")
        db.refresh(row);audit(db,user,"registry_approved",row.id,{"evidence_asset_id":evidence.id});db.commit();return result(request,registry_payload(row))
    @router.post("/admin/{collection}/{identity}/revoke")
    def revoke(collection:str,identity:str,body:RevokeBody,request:Request,db=Depends(db_session)):
        user=admin(request,db,True)
        row=db.scalar(select(RegistryVersion).where(RegistryVersion.id==identity,RegistryVersion.kind==kind_for(collection)).with_for_update())
        if not row: raise APIError(404,"NOT_FOUND","버전을 찾을 수 없습니다.")
        row.status="revoked";row.updated_at=utcnow();audit(db,user,"registry_revoked",identity,{"reason":body.reason});db.commit();return result(request,registry_payload(row))

    @router.post("/printer-intakes",status_code=201)
    def intake(body:IntakeBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True);owned_record(db,Project,body.project_id,user.tenant_id)
        job=None
        if body.job_id:
            job=owned_record(db,Job,body.job_id,user.tenant_id)
            if job.project_id!=str(body.project_id): raise APIError(422,"JOB_PROJECT_MISMATCH","프로젝트의 출력 작업을 선택해 주세요.")
        if body.evidence_id: owned_record(db,Evidence,body.evidence_id,user.tenant_id)
        metadata=intake_metadata(body,job,settings)
        row=IntakeRecord(tenant_id=user.tenant_id,**body.model_dump(mode="json",exclude={"record_source","rejection_kind"}));db.add(row);db.flush();audit(db,user,"printer_intake_recorded",row.id,metadata);db.commit()
        return result(request,{"id":row.id,"status":row.status,"record_source":body.record_source,"rejection_kind":body.rejection_kind,"verification":metadata["verification"]})
    app.include_router(router)
