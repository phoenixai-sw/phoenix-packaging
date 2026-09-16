"""Durable per-image work. A lost lease never reissues an uncertain paid call."""
from datetime import timedelta, timezone
from hashlib import sha256
from io import BytesIO
from uuid import uuid4
from types import SimpleNamespace
from PIL import Image
from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError
from .database import utcnow
from .models import Asset, Job, Tenant, User, Project
from .feature_models import AiUnit, ProviderAttempt, ProviderBudget, AuditEvent, Membership, WorkspaceMember
from .image_provider import generate_image, ProviderError
from .billing.service import lock_wallet
from .errors import APIError


def lock_tenant_work(db, tenant_id):
    # All AI paths take tenant/wallet before unit rows. The write lock is also
    # effective on SQLite, where SELECT FOR UPDATE is intentionally ignored.
    db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    lock_wallet(db, tenant_id)


def recheck_actor(db, job, settings):
    """Reauthorize before the paid call AND in the asset/capture transaction.

    Lock permission records until commit, so a concurrent revocation cannot
    race publication. A revocation during the external call is caught later.
    """
    from .billing.payments import enforce_membership_entitlement
    from .ai_routes import ensure_ai_access
    data = job.snapshot
    actor = db.scalar(select(User).where(User.id == data.get("actor_id")).with_for_update())
    if actor is None or not actor.is_active:
        raise ProviderError("AI_ACTOR_REVOKED", "요청자의 사용 권한이 변경되어 예약을 복원합니다.")
    role = actor.role
    if actor.tenant_id != job.tenant_id:
        member = db.scalar(select(Membership).where(Membership.user_id == actor.id, Membership.tenant_id == job.tenant_id).with_for_update())
        if member is None or not member.is_active:
            raise ProviderError("AI_MEMBERSHIP_REVOKED", "팀 접근 권한이 변경되어 예약을 복원합니다.")
        role = member.role
    if role not in {"owner", "editor"}:
        raise ProviderError("AI_WRITE_REVOKED", "편집 권한이 변경되어 예약을 복원합니다.")
    principal = SimpleNamespace(id=actor.id, email=actor.email, email_verified_at=actor.email_verified_at, tenant_id=job.tenant_id, role=role)
    try:
        enforce_membership_entitlement(db, principal)
        ensure_ai_access(settings, principal)
    except APIError as exc:
        raise ProviderError(exc.code, exc.message) from None
    if data.get("provider_mode") != settings.ai_provider or data.get("model") != settings.image_model:
        raise ProviderError("AI_CONFIGURATION_CHANGED", "이미지 서비스 설정이 변경되어 예약을 복원합니다.")
    if data["action"] == "image.generate.high" and not settings.ai_high_enabled:
        raise ProviderError("HIGH_RESOLUTION_DISABLED", "고해상도 생성 설정이 변경되어 예약을 복원합니다.")
    project = db.scalar(select(Project).where(Project.id == job.project_id, Project.tenant_id == job.tenant_id).with_for_update())
    if project is None or project.workspace_id != data.get("workspace_id"):
        raise ProviderError("AI_WORKSPACE_CHANGED", "작업 공간이 변경되어 예약을 복원합니다.")
    workspace_ids = {project.workspace_id}
    if data.get("reference_asset_id"):
        asset = db.scalar(select(Asset).where(Asset.id == data["reference_asset_id"], Asset.tenant_id == job.tenant_id).with_for_update())
        if asset is None:
            raise ProviderError("ASSET_UNAVAILABLE", "원본 이미지에 접근할 수 없습니다.")
        if asset.metadata_json.get("integrity_status") == "unavailable_compensated":
            raise ProviderError("ASSET_UNAVAILABLE", "원본 이미지의 소실 또는 손상이 확인되어 예약을 복원합니다.")
        workspace_ids.add(asset.workspace_id)
    if role != "owner":
        for workspace_id in sorted(value for value in workspace_ids if value):
            membership = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == actor.id, WorkspaceMember.tenant_id == job.tenant_id).with_for_update())
            if membership is None:
                raise ProviderError("AI_WORKSPACE_REVOKED", "작업 공간 접근 권한이 변경되어 예약을 복원합니다.")
    return principal


def summarize_job(db, job):
    units=list(db.scalars(select(AiUnit).where(AiUnit.job_id==job.id).order_by(AiUnit.unit_index)))
    pending=any(u.status in {"queued","running"} for u in units)
    succeeded=[u for u in units if u.status=="succeeded"]
    if pending: job.status="running" if any(u.status=="running" for u in units) else "queued"
    elif all(u.status=="canceled" for u in units): job.status="canceled"
    elif len(succeeded)==len(units): job.status="succeeded"
    elif succeeded: job.status="partially_succeeded"
    elif any(u.status in {"reconciliation_required","compensated"} for u in units): job.status="reconciliation_required"
    else: job.status="failed"
    assets=[]
    for unit in succeeded:
        asset=db.get(Asset,unit.asset_id)
        if asset: assets.append({"id":asset.id,"name":asset.original_name,"source":asset.source,"width_px":asset.width_px,"height_px":asset.height_px,"url":f"/v1/assets/{asset.id}/content"})
    cost=job.snapshot["unit_cost"]
    reserved=sum(u.status in {"queued","running"} for u in units)*cost
    charged=len(succeeded)*cost
    job.result={"assets":assets,"units":[{"index":u.unit_index,"status":u.status,"asset_id":u.asset_id,"error_code":u.error_code} for u in units],
        "credit_reserved":reserved,"credit_charged":charged,"credit_returned":len(units)*cost-reserved-charged,
        "demo":job.snapshot.get("provider_mode")=="fixture"}
    job.error=None if pending or len(succeeded)==len(units) else "일부 또는 모든 이미지가 생성되지 않았습니다. 제공되지 않은 결과의 예약 크레딧은 복원됐습니다."
    job.updated_at=utcnow()


def reserve_daily_call(db, settings, now):
    if settings.ai_provider!="openai": return True
    day=now.strftime("%Y-%m-%d")
    if db.get(ProviderBudget,day) is None:
        try:
            with db.begin_nested(): db.add(ProviderBudget(day=day,requested_units=0)); db.flush()
        except IntegrityError: pass
    return db.execute(update(ProviderBudget).where(ProviderBudget.day==day,ProviderBudget.requested_units<settings.ai_daily_units).values(requested_units=ProviderBudget.requested_units+1)).rowcount==1


def process_ai_jobs(session_factory, storage, settings, limit=1, provider=None):
    from .billing.service import capture_unit, release_unit
    provider=provider or generate_image
    processed=0
    for _ in range(max(1,min(limit,3))):
        now=utcnow()
        with session_factory() as db:
            stale=list(db.execute(select(AiUnit.id,AiUnit.tenant_id).where(
                ((AiUnit.status=="running") & (AiUnit.lease_until<now)) |
                ((AiUnit.status=="queued") & (AiUnit.created_at<now-timedelta(minutes=10)))
            ).limit(50)))
        for stale_id, stale_tenant in stale:
            with session_factory() as db:
                lock_tenant_work(db,stale_tenant)
                unit=db.scalar(select(AiUnit).where(AiUnit.id==stale_id).with_for_update())
                from .billing.policy import aware
                if unit.status not in {"queued","running"} or (unit.status=="running" and (not unit.lease_until or aware(unit.lease_until)>=now)) or (unit.status=="queued" and aware(unit.created_at)>=now-timedelta(minutes=10)):
                    continue
                release_unit(db,unit.tenant_id,unit.reservation_id,unit.unit_index,reason="worker_timeout",now=now)
                unit.status="reconciliation_required" if unit.status=="running" else "failed"
                unit.error_code="WORKER_LEASE_EXPIRED"
                unit.lease_id=None
                summarize_job(db,db.get(Job,unit.job_id))
                db.commit()
        with session_factory() as db:
            candidates=list(db.scalars(select(AiUnit).where(AiUnit.status=="queued").order_by(AiUnit.created_at).limit(20)))
            claimed=None
            for candidate in candidates:
                # Tenant lock makes the two-concurrent-provider-call limit atomic.
                lock_tenant_work(db,candidate.tenant_id)
                running=db.scalar(select(func.count()).select_from(AiUnit).where(AiUnit.tenant_id==candidate.tenant_id,AiUnit.status=="running"))
                if running>=2: db.rollback(); continue
                lease=str(uuid4())
                won=db.execute(update(AiUnit).where(AiUnit.id==candidate.id,AiUnit.status=="queued").values(status="running",lease_id=lease,lease_until=now+timedelta(minutes=6),updated_at=now,attempt_count=AiUnit.attempt_count+1).execution_options(synchronize_session=False))
                if won.rowcount!=1: db.rollback(); continue
                job=db.get(Job,candidate.job_id)
                try:
                    recheck_actor(db,job,settings)
                except ProviderError as error:
                    release_unit(db,candidate.tenant_id,candidate.reservation_id,candidate.unit_index,reason=error.code,now=now)
                    db.refresh(candidate);candidate.status="failed";candidate.error_code=error.code;candidate.lease_id=None
                    summarize_job(db,job);db.commit();continue
                if not reserve_daily_call(db,settings,now):
                    release_unit(db,candidate.tenant_id,candidate.reservation_id,candidate.unit_index,reason="provider_daily_limit",now=now)
                    db.refresh(candidate);candidate.status="failed";candidate.error_code="AI_DAILY_LIMIT"
                    summarize_job(db,job);db.commit();continue
                attempt=ProviderAttempt(tenant_id=candidate.tenant_id,unit_id=candidate.id,provider=settings.ai_provider,model=settings.image_model,status="started")
                db.add(attempt);db.flush();db.refresh(candidate)
                job.status="running";job.updated_at=now
                claimed=(candidate.id,job.id,candidate.tenant_id,candidate.reservation_id,candidate.unit_index,lease,attempt.id,dict(job.snapshot),candidate.attempt_count)
                db.commit();break
            if not claimed: break
        unit_id,job_id,tenant_id,reservation_id,index,lease,attempt_id,data,attempt_count=claimed
        try:
            reference=None
            if data.get("reference_asset_id"):
                with session_factory() as db:
                    original=db.scalar(select(Asset).where(Asset.id==data["reference_asset_id"],Asset.tenant_id==tenant_id))
                    if not original: raise ProviderError("ASSET_UNAVAILABLE","원본 이미지에 접근할 수 없습니다.")
                    content=storage.get(original.storage_key)
                    with Image.open(BytesIO(content)) as image:
                        buffer=BytesIO();image.convert("RGBA").save(buffer,format="PNG");reference=buffer.getvalue()
            # Reference download can take time. Verify the lease and permission
            # again immediately before issuing an external paid request.
            with session_factory() as db:
                lock_tenant_work(db,tenant_id)
                current=db.scalar(select(AiUnit).where(AiUnit.id==unit_id).with_for_update())
                if current.status!="running" or current.lease_id!=lease:
                    attempt=db.get(ProviderAttempt,attempt_id);attempt.status="canceled_before_provider"
                    db.commit();processed+=1;continue
                recheck_actor(db,db.get(Job,job_id),settings)
                db.commit()
            result=provider(settings,data,reference)
            # Provider billing exists even if the later private upload fails.
            with session_factory() as db:
                attempt=db.get(ProviderAttempt,attempt_id)
                attempt.request_id=result.metadata.get("provider_request_id")
                attempt.usage=result.metadata.get("usage",{})
                attempt.cost_usd=result.metadata.get("cost_usd")
                attempt.cost_is_estimate=result.metadata.get("cost_is_estimate",True)
                attempt.status="provider_succeeded"
                db.commit()
            asset_id=str(uuid4());key=f"{tenant_id}/ai/{job_id}/{unit_id}/{lease}.png"
            storage.put(key,result.content,"image/png")
            digest=sha256(result.content).hexdigest()
            if sha256(storage.get(key)).hexdigest()!=digest:
                raise ProviderError("ASSET_STORAGE_FAILED","이미지 보관을 확인하지 못했습니다.")
            with session_factory() as db:
                lock_tenant_work(db,tenant_id)
                unit=db.scalar(select(AiUnit).where(AiUnit.id==unit_id).with_for_update())
                attempt=db.get(ProviderAttempt,attempt_id)
                attempt.request_id=result.metadata.get("provider_request_id")
                attempt.usage=result.metadata.get("usage",{})
                attempt.cost_usd=result.metadata.get("cost_usd")
                attempt.status="provider_succeeded"
                if unit.status!="running" or unit.lease_id!=lease:
                    # Preserve provider cost even if the result arrived after refund.
                    attempt.status="late_result_not_charged";db.commit();processed+=1;continue
                recheck_actor(db,db.get(Job,job_id),settings)
                if not capture_unit(db,tenant_id,reservation_id,index,job_id=job_id):
                    unit.status="reconciliation_required";unit.error_code="RESERVATION_ALREADY_RELEASED"
                else:
                    job=db.get(Job,job_id)
                    asset=Asset(id=asset_id,tenant_id=tenant_id,workspace_id=data.get("workspace_id"),storage_key=key,original_name=f"디자인 시안 {index+1}.png",content_type="image/png",byte_size=len(result.content),width_px=result.width,height_px=result.height,
                        source=settings.ai_provider,metadata_json={**result.metadata,"sha256":digest,"job_id":job_id,"prompt":data["prompt"],"reference_asset_id":data.get("reference_asset_id")})
                    db.add(asset);db.flush()
                    unit.asset_id=asset_id;unit.status="succeeded";unit.result_metadata=result.metadata;unit.provider_request_id=result.metadata.get("provider_request_id")
                    db.add(AuditEvent(tenant_id=tenant_id,action="generation_succeeded",entity_id=job_id,details={"unit":index,"provider":settings.ai_provider}))
                unit.updated_at=utcnow();summarize_job(db,db.get(Job,job_id));db.commit()
        except Exception as exc:
            error=exc if isinstance(exc,ProviderError) else ProviderError("AI_STORAGE_OR_COMMIT_FAILED","결과 저장이 완료되지 않아 예약을 복원합니다.",uncertain=True)
            with session_factory() as db:
                lock_tenant_work(db,tenant_id)
                unit=db.scalar(select(AiUnit).where(AiUnit.id==unit_id).with_for_update())
                attempt=db.get(ProviderAttempt,attempt_id)
                attempt.status="uncertain" if error.uncertain else "failed";attempt.error_code=error.code
                if error.request_id: attempt.request_id=error.request_id
                if unit.status=="running" and unit.lease_id==lease:
                    if error.retryable and not error.uncertain and attempt_count<3:
                        unit.status="queued"
                    else:
                        release_unit(db,tenant_id,reservation_id,index,reason=error.code)
                        unit.status="reconciliation_required" if error.uncertain else "failed"
                    unit.error_code=error.code;unit.lease_id=None;unit.updated_at=utcnow()
                    summarize_job(db,db.get(Job,job_id))
                    db.add(AuditEvent(tenant_id=tenant_id,action="generation_failed",entity_id=job_id,details={"unit":index,"code":error.code}))
                db.commit()
        processed+=1
    return processed
