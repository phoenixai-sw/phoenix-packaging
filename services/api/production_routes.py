"""Production routes freeze revisions, load server registry evidence, then reserve credits."""
from datetime import timedelta
from hashlib import sha256
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,Request
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,update,func
from sqlalchemy.exc import IntegrityError
from .auth import require_auth
from .business import owned_record
from .database import utcnow
from .errors import APIError
from .models import Project,Job,Asset
from .feature_models import PreflightRun,AuditEvent
from .registry import approved_conditions,canonical_production_identity
from .exporters import preflight_project
from .billing.service import canonical_hash,create_quote,quote_payload,reserve


class PreflightBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    project_id:UUID
    base_revision:int=Field(ge=1)
    kind:str=Field(default="production",pattern="^(review|production)$")
    reviewed_face_ids:list[str]=Field(default_factory=list,max_length=6)


def production_input_data(db,project,reviewed_face_ids):
    return {"production_identity":canonical_production_identity(db,project),"reviewed_face_ids":sorted(set(reviewed_face_ids)),
            "template_version_id":project.template_version_id,"print_profile_version_id":project.print_profile_version_id,"material":project.material}


def _project(db,user,identity,base_revision):
    project=owned_record(db,Project,identity,user.tenant_id)
    # The no-op conditional write locks on both SQLite and PostgreSQL before snapshots/quotes.
    won=db.execute(update(Project).where(Project.id==project.id,Project.tenant_id==user.tenant_id,Project.base_revision==base_revision).values(base_revision=base_revision).execution_options(synchronize_session=False))
    if won.rowcount!=1:raise APIError(409,"REVISION_CONFLICT","프로젝트가 변경되었습니다. 저장 후 다시 확인해 주세요.")
    db.refresh(project)
    return project


def _snapshot(project,revision,project_payload):
    return {**project_payload(project),"revision_id":revision.id,"scene":revision.scene,
            **{key:getattr(project,key) for key in ("tenant_id","template_version_id","print_profile_version_id","material","brand_id","product_variant_id","workspace_id","bottom_mm","depth_mm")}}


def _preflight(db,project,revision,reviewed,project_payload,storage,settings):
    conditions=approved_conditions(db,project,revision.id,sorted(set(reviewed)))
    def resolver(asset_id):
        asset=owned_record(db,Asset,asset_id,project.tenant_id)
        return storage.get(asset.storage_key)
    snapshot=_snapshot(project,revision,project_payload)
    report=preflight_project(snapshot,conditions,resolver)
    if not settings.enable_production_export:
        report["issues"].append({"code":"PRODUCTION_DISABLED","message":"제작용 출력은 운영 승인 후 활성화됩니다.","severity":"error","scope":"production"})
        report["production_allowed"]=False;report["status"]="blocked"
    run=PreflightRun(tenant_id=project.tenant_id,project_id=project.id,revision_id=revision.id,conditions_hash=canonical_hash(conditions),result=report)
    db.add(run);db.flush();report={**report,"id":run.id}
    return snapshot,conditions,report


def public_preflight(report,kind="production"):
    """Review can remain available while manufacturing-only conditions are blocked."""
    blockers=[issue for issue in report["issues"] if issue["severity"]=="error" and (kind=="production" or issue["scope"]=="review")]
    warnings=[issue for issue in report["issues"] if issue not in blockers]
    return {**report,"kind":kind,"status":"blocked" if blockers else "pass","blockers":blockers,"warnings":warnings,"checks":report["issues"]}


def _require_pass(report):
    if not report["production_allowed"]:
        raise APIError(422,"PRODUCTION_PREFLIGHT_BLOCKED","제작용 검수 항목을 확인해 주세요.",{"preflight":report})


def prepare_production_quote(db,user,body,settings,project_payload,snapshot_revision,storage=None):
    """Generic quote hook. storage is required whenever the scene contains an image."""
    if user.role not in {"owner","editor"}:raise APIError(403,"ROLE_FORBIDDEN","제작용 출력을 요청할 권한이 없습니다.")
    if body.get("units",body.get("requested_units",1))!=1:raise APIError(422,"INVALID_EXPORT_UNITS","제작 출력은 한 항목씩 요청해 주세요.")
    project=_project(db,user,body.get("project_id"),body.get("base_revision"))
    data=body.get("input_data") or {};reviewed=body.get("reviewed_face_ids",data.get("reviewed_face_ids",[]))
    if not isinstance(reviewed,list) or any(not isinstance(face,str) for face in reviewed):raise APIError(422,"INVALID_FACES","확인한 면 목록이 필요합니다.")
    revision=snapshot_revision(db,project,"production_preflight")
    _,_,report=_preflight(db,project,revision,reviewed,project_payload,storage,settings)
    _require_pass(report)
    action=body.get("action","export.production.first")
    if action not in {"export.production.first","export.production.repeat"}:raise APIError(422,"INVALID_ACTION","제작 출력 작업을 확인해 주세요.")
    return {"action":action,"units":1,"project_id":project.id,"base_revision":project.base_revision,"input_data":production_input_data(db,project,reviewed)}


def create_production_export(db,user,body,request,project_payload,snapshot_revision):
    from .billing.models import Quote
    if user.role not in {"owner","editor"}:raise APIError(403,"ROLE_FORBIDDEN","제작용 출력을 요청할 권한이 없습니다.")
    operation=request.headers.get("idempotency-key")
    if not operation or len(operation)>160:raise APIError(422,"IDEMPOTENCY_KEY_REQUIRED","중복 출력을 방지할 요청 식별자가 필요합니다.")
    request_hash=canonical_hash(body.model_dump(mode="json"))
    existing=db.scalar(select(Job).where(Job.tenant_id==user.tenant_id,Job.operation_key==operation))
    if existing:
        owned_record(db,Project,existing.project_id,user.tenant_id)
        if existing.request_hash!=request_hash or existing.kind!="production_export":raise APIError(409,"IDEMPOTENCY_CONFLICT","같은 요청 식별자로 다른 작업을 보낼 수 없습니다.")
        return existing
    project=_project(db,user,body.project_id,body.base_revision)
    existing=db.scalar(select(Job).where(Job.tenant_id==user.tenant_id,Job.operation_key==operation))
    if existing:
        if existing.request_hash!=request_hash or existing.kind!="production_export":raise APIError(409,"IDEMPOTENCY_CONFLICT","같은 요청 식별자로 다른 작업을 보낼 수 없습니다.")
        return existing
    count=db.scalar(select(func.count()).select_from(Job).where(Job.tenant_id==user.tenant_id,Job.created_at>utcnow()-timedelta(hours=1)))
    if count>=30:raise APIError(429,"EXPORT_RATE_LIMIT","출력 요청이 많습니다. 잠시 후 다시 시도해 주세요.")
    revision=snapshot_revision(db,project,"production_export")
    snapshot,conditions,report=_preflight(db,project,revision,body.reviewed_face_ids,project_payload,request.app.state.storage,request.app.state.settings)
    _require_pass(report)
    if body.quote_id is None:raise APIError(422,"QUOTE_REQUIRED","최신 제작 출력 견적을 확인해 주세요.")
    quote=db.scalar(select(Quote).where(Quote.id==str(body.quote_id),Quote.tenant_id==user.tenant_id))
    if quote is None or quote.action not in {"export.production.first","export.production.repeat"}:raise APIError(404,"QUOTE_NOT_FOUND","제작 출력 견적을 찾을 수 없습니다.")
    data=production_input_data(db,project,body.reviewed_face_ids)
    job=Job(id=str(uuid4()),tenant_id=user.tenant_id,project_id=project.id,revision_id=revision.id,kind="production_export",operation_key=operation,request_hash=request_hash,snapshot={})
    db.add(job)
    try:db.flush()
    except IntegrityError:
        db.rollback();existing=db.scalar(select(Job).where(Job.tenant_id==user.tenant_id,Job.operation_key==operation))
        if existing and existing.request_hash==request_hash and existing.kind=="production_export":return existing
        raise APIError(409,"EXPORT_CONFLICT","같은 요청 식별자의 다른 작업이 이미 있습니다.") from None
    reservation=reserve(db,user.tenant_id,operation,quote.action,1,quote_id=quote.id,project_id=project.id,base_revision=project.base_revision,input_data=data,job_id=job.id,actor_id=user.id)
    job.snapshot={**snapshot,"approved_conditions":conditions,"conditions_hash":canonical_hash(conditions),"reviewed_face_ids":sorted(set(body.reviewed_face_ids)),"reservation_id":reservation.id,"unit_cost":reservation.unit_cost,"actor_id":user.id,"preflight_id":report["id"]}
    db.add(AuditEvent(tenant_id=user.tenant_id,actor_id=user.id,action="production_queued",entity_id=job.id,details={"revision_id":revision.id,"preflight_id":report["id"]}))
    try:db.commit()
    except IntegrityError:
        db.rollback();existing=db.scalar(select(Job).where(Job.tenant_id==user.tenant_id,Job.operation_key==operation))
        if existing and existing.request_hash==request_hash:return existing
        raise APIError(409,"EXPORT_CONFLICT","다른 출력 요청과 충돌했습니다. 최신 상태를 확인해 주세요.") from None
    return job


def install_production_routes(app,db_session,owned,project_payload,snapshot_revision):
    router=APIRouter(prefix="/v1",tags=["production"])
    def result(request,data):return {"data":data,"request_id":request.state.request_id}
    @router.post("/preflight")
    def preflight(body:PreflightBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True,authorize_write=False)
        project=_project(db,user,body.project_id,body.base_revision);revision=snapshot_revision(db,project,"preflight")
        _,_,report=_preflight(db,project,revision,body.reviewed_face_ids,project_payload,app.state.storage,app.state.settings)
        db.commit();return result(request,public_preflight(report,body.kind))
    @router.post("/production/quotes",status_code=201)
    def production_quote(body:PreflightBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True)
        values=prepare_production_quote(db,user,body.model_dump(mode="json"),app.state.settings,project_payload,snapshot_revision,app.state.storage)
        quote=create_quote(db,user.tenant_id,**values);db.commit();return result(request,quote_payload(quote))
    app.include_router(router)
