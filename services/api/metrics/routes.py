from datetime import datetime, timedelta, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy import select
from ..auth import require_auth
from ..billing.models import Payment
from ..billing.policy import aware
from ..business import owned_record
from ..contracts.base import Envelope, ERROR_RESPONSES
from ..database import utcnow
from ..editor_sessions import enforce_edit_lease
from ..errors import APIError
from ..models import Project, ProjectEditLease, Job, Tenant
from ..retention.service import digest, audit
from ..retention.storage_lifecycle import gate
from .models import ActivitySlice, CostEntry
from .schemas import ActivityInput, ActivityDTO, CostInput, CostDTO, CostsPage, MetricsOverviewDTO
from .reporting import overview, cost_payload


def admin(request,db,mutate=False):
    user,_=require_auth(request,db,mutate=mutate,authorize_write=False,enforce_membership=False)
    if not user.is_admin:raise APIError(403,"ADMIN_REQUIRED","플랫폼 관리자 권한이 필요합니다.")
    return user


def install_metrics_routes(app,db_session):
    router=APIRouter(prefix="/v1",tags=["internal_metrics"],responses=ERROR_RESPONSES)
    def result(request,data):return {"data":data,"request_id":request.state.request_id}

    @router.post("/metrics/activity",response_model=Envelope[ActivityDTO],response_model_exclude_unset=True)
    def activity(body:ActivityInput,request:Request,db=Depends(db_session)):
        user,session=require_auth(request,db,mutate=True)
        project=owned_record(db,Project,body.project_id,user.tenant_id)
        enforce_edit_lease(db,project,request)
        now=utcnow();lease=db.get(ProjectEditLease,project.id)
        if not lease or aware(lease.expires_at)<=now or lease.user_id!=user.id or lease.login_session_id!=session.id:
            raise APIError(423,"ACTIVITY_LEASE_REQUIRED","활성 편집 창에서만 활동 시간을 기록합니다.")
        bucket=int(now.timestamp())//30
        existing=db.scalar(select(ActivitySlice).where(ActivitySlice.project_id==project.id,ActivitySlice.time_bucket==bucket))
        seconds=0
        if existing is None:
            previous=db.scalar(select(ActivitySlice).where(ActivitySlice.project_id==project.id,
                ActivitySlice.session_id==session.id,ActivitySlice.lease_id==lease.lease_token).order_by(ActivitySlice.created_at.desc()).limit(1))
            gap=(now-aware(previous.created_at)).total_seconds() if previous else 0
            seconds=min(30,max(0,int(gap))) if 0<gap<=60 else 0
            db.add(ActivitySlice(tenant_id=user.tenant_id,project_id=project.id,actor_id=user.id,session_id=session.id,
                lease_id=lease.lease_token,time_bucket=bucket,seconds=seconds,created_at=now))
        db.commit();return result(request,{"recorded":existing is None,"estimated_seconds_added":seconds,"measurement":"bounded_browser_activity_estimate"})

    @router.get("/admin/metrics",response_model=Envelope[MetricsOverviewDTO],response_model_exclude_unset=True)
    def report(request:Request,start:datetime|None=None,end:datetime|None=None,db=Depends(db_session)):
        admin(request,db)
        end=aware(end or utcnow());start=aware(start or end-timedelta(days=30))
        if end<=start or end-start>timedelta(days=366):raise APIError(422,"METRICS_RANGE_INVALID","1년 이내의 올바른 조회 기간을 선택해 주세요.")
        return result(request,overview(db,start,end))

    @router.post("/admin/metrics/costs",status_code=201,response_model=Envelope[CostDTO],response_model_exclude_unset=True)
    def cost(body:CostInput,request:Request,db=Depends(db_session)):
        user=admin(request,db,True);key=request.headers.get("idempotency-key","")
        if not 1<=len(key)<=160:raise APIError(422,"IDEMPOTENCY_KEY_REQUIRED","비용 기록 식별자가 필요합니다.")
        gate(db)  # Serializes corrections/idempotency across tenants.
        fingerprint=digest(body.model_dump(mode="json"))
        existing=db.scalar(select(CostEntry).where(CostEntry.operation_key==key))
        if existing:
            if existing.request_hash!=fingerprint:raise APIError(409,"IDEMPOTENCY_CONFLICT","같은 식별자로 다른 비용을 기록할 수 없습니다.")
            return result(request,cost_payload(existing))
        tenant_id=str(body.tenant_id) if body.tenant_id else None
        if tenant_id and db.get(Tenant,tenant_id) is None:raise APIError(404,"NOT_FOUND","조직을 찾을 수 없습니다.")
        for model,identity in ((Project,body.project_id),(Job,body.job_id),(Payment,body.payment_id)):
            if identity:
                row=db.get(model,str(identity))
                if row is None or row.tenant_id!=tenant_id:raise APIError(404,"NOT_FOUND","같은 조직의 연결 자료를 찾을 수 없습니다.")
                if model is Job and body.project_id and row.project_id!=str(body.project_id):raise APIError(422,"PROJECT_MISMATCH","작업의 프로젝트를 확인해 주세요.")
        if aware(body.occurred_at)>utcnow()+timedelta(minutes=5):raise APIError(422,"COST_TIME_INVALID","미래 비용을 실제 발생한 비용으로 기록할 수 없습니다.")
        if body.supersedes_id:
            original=db.get(CostEntry,str(body.supersedes_id))
            if original is None or (original.tenant_id,original.category,original.currency,original.payment_id)!=(tenant_id,body.category,body.currency,str(body.payment_id) if body.payment_id else None):
                raise APIError(422,"COST_CORRECTION_MISMATCH","같은 조직·비용 항목·통화·결제의 기록만 정정할 수 있습니다.")
            if db.scalar(select(CostEntry.id).where(CostEntry.supersedes_id==original.id)):raise APIError(409,"COST_ALREADY_CORRECTED","이미 정정된 기록입니다. 최신 기록을 확인해 주세요.")
        elif body.payment_id and db.scalar(select(CostEntry.id).where(CostEntry.payment_id==str(body.payment_id))):
            raise APIError(409,"PAYMENT_FEE_EXISTS","이 결제 수수료는 기존 기록을 사유와 함께 정정해 주세요.")
        values=body.model_dump(mode="python")
        for name in ("tenant_id","project_id","job_id","payment_id","supersedes_id"):
            if values[name] is not None:values[name]=str(values[name])
        row=CostEntry(**values,actor_id=user.id,operation_key=key,request_hash=fingerprint)
        db.add(row);db.flush();audit(db,tenant_id,user.id,"operating_cost_recorded",row.id,{"category":body.category,"basis":body.basis,"reason":body.reason,"supersedes_id":values["supersedes_id"]})
        db.commit();return result(request,cost_payload(row))

    @router.get("/admin/metrics/costs",response_model=Envelope[CostsPage],response_model_exclude_unset=True)
    def cost_history(request:Request,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),db=Depends(db_session)):
        admin(request,db)
        rows=list(db.scalars(select(CostEntry).order_by(CostEntry.created_at.desc(),CostEntry.id.desc()).offset(offset).limit(limit+1)))
        return result(request,{"items":[cost_payload(row) for row in rows[:limit]],"next_offset":offset+limit if len(rows)>limit else None})

    app.include_router(router)
