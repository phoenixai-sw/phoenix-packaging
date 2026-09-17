"""Server-authoritative generation quotes and durable, idempotent image jobs."""
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from .auth import require_auth
from .business import owned_record
from .errors import APIError
from .feature_models import AiUnit, AuditEvent
from .models import Project, Asset, Job
from .billing.models import Quote, Reservation
from .billing.service import reserve, release_unit, canonical_hash
from .ai_jobs import summarize_job, lock_tenant_work
from .image_provider import (get_capabilities, ProviderError, EDIT_VERSION, MAX_REFERENCE_BYTES,
                             normalize_edit_region, edit_pixel_box, decode_edit_reference)
from .image_sizing import ImageSizeError, select_image_output


def ensure_ai_access(settings,user):
    if settings.ai_provider=="disabled": raise APIError(503,"AI_NOT_CONFIGURED","이미지 생성 서비스 연결을 준비하고 있습니다.")
    if settings.ai_provider=="openai":
        allowed=user.email.lower() in settings.ai_allowed_emails
        if settings.ai_require_verified_email and user.email_verified_at is None and not allowed:
            raise APIError(403,"EMAIL_VERIFICATION_REQUIRED","실제 AI 생성을 사용하려면 Google 계정으로 다시 로그인해 주세요.")


def prepare_ai_quote(db,user,body,settings):
    ensure_ai_access(settings,user)
    action=body["action"]
    if action not in {"image.generate.standard","image.generate.high","image.edit.standard"}: raise APIError(422,"AI_ACTION_INVALID","지원하지 않는 이미지 작업입니다.")
    units=body.get("requested_units") or body.get("units") or 1
    if not 1<=units<=3 or (action=="image.edit.standard" and units!=1): raise APIError(422,"AI_UNIT_LIMIT","생성은 1~3장, 수정은 한 장씩 요청해 주세요.")
    if action=="image.generate.high" and not settings.ai_high_enabled: raise APIError(422,"HIGH_RESOLUTION_DISABLED","고해상도 생성은 원가 검증 후 제공됩니다.")
    if not body.get("project_id"): raise APIError(422,"PROJECT_REQUIRED","디자인 프로젝트를 먼저 선택해 주세요.")
    project=owned_record(db,Project,body["project_id"],user.tenant_id)
    if project.base_revision!=body.get("base_revision"): raise APIError(409,"REVISION_CONFLICT","디자인이 변경되었습니다. 저장 후 새 견적을 요청해 주세요.")
    submitted=body.get("input_data") or {}
    mode=submitted.get("edit_mode", "full")
    if not isinstance(mode,str) or mode not in {"full", "remove_text"}:
        raise APIError(422,"AI_EDIT_MODE_INVALID","지원하지 않는 이미지 수정 방식입니다.")
    edit_fields={"edit_mode", "edit_region", "confirmed_source_text"}
    if action!="image.edit.standard" and edit_fields.intersection(submitted):
        raise APIError(422,"EDIT_ACTION_MISMATCH","글자 제거는 원본 이미지 수정 작업에서만 사용할 수 있습니다.")
    if mode=="full" and {"edit_region","confirmed_source_text"}.intersection(submitted):
        raise APIError(422,"EDIT_MODE_MISMATCH","영역을 선택한 글자 제거 방식을 지정해 주세요.")
    prompt=body.get("prompt") if body.get("prompt") is not None else submitted.get("prompt","")
    if not isinstance(prompt,str) or not 5<=len(prompt.strip())<=4000: raise APIError(422,"PROMPT_INVALID","디자인 설명을 5~4,000자로 입력해 주세요.")
    face_id=body.get("face_id") or submitted.get("face_id","front")
    face=next((face for face in project.scene["faces"] if face["id"]==face_id),None)
    if not face: raise APIError(422,"FACE_INVALID","디자인할 면을 선택해 주세요.")
    reference=body.get("reference_asset_id") or submitted.get("reference_asset_id")
    if action=="image.edit.standard":
        if not reference: raise APIError(422,"REFERENCE_REQUIRED","수정할 원본 이미지를 선택해 주세요.")
        from .asset_reconciliation import ensure_asset_available
        reference_asset=owned_record(db,Asset,reference,user.tenant_id)
        ensure_asset_available(reference_asset)
    elif reference: raise APIError(422,"REFERENCE_ACTION_MISMATCH","원본 이미지를 수정하려면 이미지 수정 작업을 선택해 주세요.")
    data={"action":action,"prompt":prompt.strip(),"face_id":face_id,"width_mm":face["width_mm"],"height_mm":face["height_mm"],"reference_asset_id":str(reference) if reference else None,"workspace_id":project.workspace_id,"provider_mode":settings.ai_provider,"model":settings.image_model,"quality":"high"}
    sizing_width,sizing_height=face["width_mm"],face["height_mm"]
    if action=="image.edit.standard":
        data["edit_mode"]=mode
    if mode=="remove_text":
        confirmed=submitted.get("confirmed_source_text","")
        if not isinstance(confirmed,str) or len(confirmed)>4000:
            raise APIError(422,"SOURCE_TEXT_INVALID","확인한 원문은 4,000자 이하로 입력해 주세요.")
        try:
            region=normalize_edit_region(submitted.get("edit_region"))
            # Bind old uploads too: early upload rows may not have a SHA. A
            # bounded private read freezes actual bytes without mutating them.
            from hashlib import sha256
            from .storage import build_storage
            storage=build_storage(settings)
            content=storage.get_limited(reference_asset.storage_key,MAX_REFERENCE_BYTES)
            original=decode_edit_reference(content)
            if not 1/3<=original.width/original.height<=3:
                raise ProviderError("EDIT_REFERENCE_ASPECT_UNSUPPORTED","글자 제거 원본의 가로세로 비율은 1:3~3:1이어야 합니다.")
            box=edit_pixel_box(region,*original.size)
            saved_digest=reference_asset.metadata_json.get("sha256")
            digest=sha256(content).hexdigest()
            if saved_digest and saved_digest!=digest:
                raise ProviderError("AI_REFERENCE_CHANGED","원본 이미지의 저장 무결성을 확인하지 못했습니다.")
            data.update({"edit_version":EDIT_VERSION,"edit_region":region,"edit_pixel_box":box,
                         "confirmed_source_text":confirmed,"reference_sha256":digest,
                         "reference_width_px":original.width,"reference_height_px":original.height,
                         "reference_byte_size":len(content),"preservation_scope":"outside_edit_region"})
            if isinstance(reference_asset.metadata_json.get("image_quality"),dict):
                from copy import deepcopy
                # Partial retouching cannot turn interpolated source pixels
                # into native detail or erase synthesized-bleed provenance.
                data["reference_image_quality"]=deepcopy(reference_asset.metadata_json["image_quality"])
            sizing_width,sizing_height=original.size
        except ProviderError as error:
            raise APIError(422,error.code,error.message) from None
        except Exception:
            raise APIError(503,"AI_REFERENCE_UNAVAILABLE","원본 이미지를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.") from None
    try:
        data.update(select_image_output(settings.image_model, sizing_width, sizing_height))
    except ImageSizeError as error:
        raise APIError(422,"AI_SIZE_INVALID",str(error)) from None
    return {"action":action,"units":units,"project_id":project.id,"base_revision":project.base_revision,"input_data":data}


class JobBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    quote_id: UUID


def install_ai_routes(app,db_session,job_payload,snapshot_revision):
    router=APIRouter(prefix="/v1",tags=["images"])
    settings=app.state.settings
    def result(request,data): return {"data":data,"request_id":request.state.request_id}

    @router.get("/ai/capabilities")
    def capabilities(request:Request): return result(request,get_capabilities(settings))

    @router.post("/jobs",status_code=202)
    def create_job(body:JobBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True);ensure_ai_access(settings,user)
        operation=request.headers.get("idempotency-key","")
        if not 1<=len(operation)<=160: raise APIError(422,"IDEMPOTENCY_KEY_REQUIRED","요청 식별자가 필요합니다.")
        quote=owned_record(db,Quote,body.quote_id,user.tenant_id)
        if not quote.action.startswith("image."): raise APIError(422,"QUOTE_ACTION_MISMATCH","이미지 작업 견적을 선택해 주세요.")
        project=owned_record(db,Project,quote.project_id,user.tenant_id)
        existing=db.scalar(select(Job).where(Job.tenant_id==user.tenant_id,Job.operation_key==operation))
        request_hash=canonical_hash({"quote_id":quote.id})
        if existing:
            if existing.request_hash!=request_hash: raise APIError(409,"IDEMPOTENCY_CONFLICT","같은 요청 식별자로 다른 작업을 실행할 수 없습니다.")
            return result(request,job_payload(existing))
        if quote.input_data.get("provider_mode")!=settings.ai_provider or quote.input_data.get("model")!=settings.image_model:
            raise APIError(409,"QUOTE_CHANGED","이미지 서비스 설정이 변경되었습니다. 견적을 다시 확인해 주세요.")
        if quote.input_data.get("reference_asset_id"): owned_record(db,Asset,quote.input_data["reference_asset_id"],user.tenant_id)
        try:
            lock_tenant_work(db,user.tenant_id)
            existing=db.scalar(select(Job).where(Job.tenant_id==user.tenant_id,Job.operation_key==operation))
            if existing:
                if existing.request_hash!=request_hash: raise APIError(409,"IDEMPOTENCY_CONFLICT","같은 요청 식별자로 다른 작업을 실행할 수 없습니다.")
                return result(request,job_payload(existing))
            # Portable write lock binds the quote to the exact snapshot through
            # the reservation+job commit, even during a concurrent editor save.
            unchanged=db.execute(update(Project).where(Project.id==project.id,Project.tenant_id==user.tenant_id,Project.base_revision==quote.base_revision).values(base_revision=Project.base_revision).execution_options(synchronize_session=False))
            if unchanged.rowcount!=1:
                raise APIError(409,"REVISION_CONFLICT","디자인이 변경되었습니다. 새 견적을 요청해 주세요.")
            db.refresh(project)
            reservation=reserve(db,user.tenant_id,operation,quote.action,quote.units,quote_id=quote.id,project_id=project.id,base_revision=quote.base_revision,input_data=quote.input_data,actor_id=user.id)
            revision=snapshot_revision(db,project,"ai_generation")
            job=Job(id=str(uuid4()),tenant_id=user.tenant_id,project_id=project.id,revision_id=revision.id,kind="ai_generation",operation_key=operation,request_hash=request_hash,snapshot={**quote.input_data,"unit_cost":quote.unit_cost,"reservation_id":reservation.id,"requested_units":quote.units,"actor_id":user.id})
            db.add(job);db.flush();reservation.job_id=job.id
            for index in range(quote.units): db.add(AiUnit(tenant_id=user.tenant_id,job_id=job.id,reservation_id=reservation.id,unit_index=index))
            db.flush();summarize_job(db,job);db.add(AuditEvent(tenant_id=user.tenant_id,actor_id=user.id,action="generation_requested",entity_id=job.id,details={"units":quote.units,"action":quote.action}));db.commit()
        except IntegrityError:
            db.rollback();existing=db.scalar(select(Job).where(Job.tenant_id==user.tenant_id,Job.operation_key==operation))
            if existing and existing.request_hash==request_hash: return result(request,job_payload(existing))
            raise APIError(409,"JOB_CONFLICT","작업이 이미 시작되었는지 확인해 주세요.") from None
        return result(request,job_payload(job))

    @router.get("/projects/{identity}/generations")
    def generations(identity:UUID,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db);owned_record(db,Project,identity,user.tenant_id)
        rows=db.scalars(select(Job).where(Job.tenant_id==user.tenant_id,Job.project_id==str(identity),Job.kind=="ai_generation").order_by(Job.created_at.desc()).limit(100))
        return result(request,{"items":[job_payload(row) for row in rows]})

    @router.post("/jobs/{identity}/cancel")
    def cancel(identity:UUID,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True);job=owned_record(db,Job,identity,user.tenant_id)
        if job.kind!="ai_generation": raise APIError(422,"JOB_NOT_CANCELABLE","이미지 작업만 취소할 수 있습니다.")
        # Worker claim uses the same tenant mutex, so a claimed provider call is
        # never canceled as if it had not started.
        lock_tenant_work(db,user.tenant_id)
        units=list(db.scalars(select(AiUnit).where(AiUnit.job_id==job.id,AiUnit.status=="queued").with_for_update()))
        for unit in units:
            release_unit(db,user.tenant_id,unit.reservation_id,unit.unit_index,reason="사용자 대기 작업 취소");unit.status="canceled"
        db.flush();summarize_job(db,job);db.commit();return result(request,job_payload(job))
    app.include_router(router)
