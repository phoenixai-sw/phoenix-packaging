"""Post-publication export integrity, without regeneration or another charge.

Two matching definitive failures, spaced five minutes apart, are required for
compensation. Network/auth uncertainty never revokes a paid entitlement.
"""
from datetime import datetime,timedelta
from hashlib import sha256
import re
import httpx
from sqlalchemy import select,or_,update
from .billing.policy import aware
from .billing.service import canonical_hash,compensate_unit,lock_wallet
from .billing.models import ProductionEntitlement,Reservation,Quote
from .database import utcnow
from .errors import APIError
from .feature_models import AuditEvent
from .models import Job
from .retention.models import StorageIntent
from .retention.deletion import object_state,ensure_object_available
from .retention.service import lock_tenant

KINDS={'review_export','production_export','editable_export'}
PROBE_INTERVAL=timedelta(minutes=5)
HEALTHY_INTERVAL=timedelta(hours=24)
MAX_BYTES=256*1024*1024


def export_availability(job):
    if job.kind not in KINDS:return None
    metadata=(job.result or {}).get('_integrity') or {}
    state=metadata.get('state');deleted=object_state(job).get('state') in {'deleting','deleted'}
    status='deleted' if deleted else {'healthy':'available','uncertain':'temporarily_unverified','missing':'suspect','corrupt':'suspect','unavailable':'compensated' if metadata.get('credit_restored',0)>0 else 'unavailable'}.get(state,'unchecked')
    messages={'deleted':'소유자의 요청에 따라 파일 접근이 종료되었습니다.',
        'available':'저장된 출력 파일의 무결성을 확인했습니다.','unchecked':'다운로드할 때 원본 파일의 무결성을 확인합니다.',
        'temporarily_unverified':'저장소 응답을 확인하지 못했습니다. 과금·완료 이력은 유지되며 다시 확인할 수 있습니다.',
        'suspect':'출력 파일의 소실 또는 손상이 관측되어 다운로드를 중지하고 재확인합니다.',
        'unavailable':'출력 파일의 소실 또는 손상이 확인되었습니다. 무료 출력은 같은 저장본으로 재시도할 수 있습니다.',
        'compensated':'출력 파일의 소실 또는 손상이 확인되어 해당 차감을 복원했습니다. 새 제작은 현재 승인 조건과 새 견적이 필요합니다.'}
    return {'status':status,'reason':metadata.get('reason'),'checked_at':metadata.get('checked_at'),
        'next_check_at':metadata.get('next_check_at'),'confirmed_at':metadata.get('confirmed_at'),
        'retry_allowed':not deleted and job.status=='failed' and job.kind in {'review_export','editable_export'},
        'credit_restored':metadata.get('credit_restored',0),'message':messages[status]}


def download_is_available(job):
    state=export_availability(job)
    return job.status=='succeeded' and bool(job.result and job.result.get('storage_key')) and state is not None and state['status'] not in {'suspect','unavailable','compensated','deleted'}


def _identity(db,job):
    result=job.result or {};key=result.get('storage_key');digest=result.get('sha256')
    from .storage import validate_key
    try:
        validate_key(key)
        if not key.startswith(job.tenant_id+'/'):raise ValueError()
    except (ValueError,TypeError):return (None,None,None)
    if not digest and job.kind=='review_export' and result.get('format')!='print_engine_zip':digest=(result.get('manifest') or {}).get('sha256')
    size=result.get('byte_size')
    intent=db.scalar(select(StorageIntent).where(StorageIntent.tenant_id==job.tenant_id,StorageIntent.job_id==job.id,StorageIntent.storage_key==key,StorageIntent.status=='published')) if key else None
    if intent:
        if digest and digest!=intent.sha256 or size is not None and size!=intent.byte_size:return (key,None,None)
        digest,size=intent.sha256,intent.byte_size
    return key,digest,size


def _probe(storage,identity):
    key,expected,size=identity
    if not key or not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected) or size is not None and (type(size) is not int or not 0<size<=MAX_BYTES):
        return 'uncertain','missing_integrity_baseline',None
    try:
        raw=storage.get_limited(key,MAX_BYTES)
    except FileNotFoundError:return 'missing','object_absent',None
    except httpx.HTTPStatusError as exc:
        try:
            body=exc.response.json()
            absent=isinstance(body,dict) and (body.get('code')=='NoSuchKey' or str(body.get('message','')).lower()=='object not found')
            if exc.response.status_code in {400,404} and absent and hasattr(storage,'confirm_missing') and storage.confirm_missing(key):return 'missing','object_absent',None
        except Exception:pass
        return 'uncertain','storage_http_error',None
    except ValueError as exc:
        return ('corrupt','oversize',None) if str(exc)=='Asset exceeds limit' and size is not None else ('uncertain','storage_validation_error',None)
    except (httpx.HTTPError,OSError):return 'uncertain','storage_connection_error',None
    digest=sha256(raw).hexdigest()
    if digest!=expected or size is not None and len(raw)!=size:return 'corrupt',digest,None
    return 'healthy',digest,raw


def _compensate_production(db,job,now):
    reservation=db.scalar(select(Reservation).where(Reservation.id==job.snapshot.get('reservation_id'),Reservation.tenant_id==job.tenant_id,Reservation.job_id==job.id))
    if not reservation or reservation.unit_status.get('0')!='captured':
        raise APIError(409,'EXPORT_COMPENSATION_UNCERTAIN','출력 차감 내역을 확인하지 못해 운영 확인이 필요합니다.')
    if reservation.unit_cost==0:return 0
    compensate_unit(db,job.tenant_id,reservation.id,0,reason='완료된 제작 출력 파일 소실·손상 확인',now=now)
    entitlement=db.scalar(select(ProductionEntitlement).where(ProductionEntitlement.tenant_id==job.tenant_id,ProductionEntitlement.reservation_id==reservation.id).with_for_update())
    if entitlement and entitlement.status=='entitled':
        entitlement.status='compensated'
        db.add(AuditEvent(tenant_id=job.tenant_id,action='production_entitlement_compensated',entity_id=entitlement.id,
            details={'job_id':job.id,'reservation_id':reservation.id,'fingerprint':entitlement.fingerprint,'next_export_requires_new_paid_quote':True},created_at=now))
    return reservation.unit_cost


def assert_repeat_entitlement(db,tenant_id,snapshot):
    """A free job reserved before compensation cannot publish on stale rights."""
    if snapshot.get('unit_cost')!=0:return
    reservation=db.scalar(select(Reservation).where(Reservation.id==snapshot.get('reservation_id'),Reservation.tenant_id==tenant_id))
    quote=db.get(Quote,reservation.quote_id) if reservation and reservation.quote_id else None
    entitlement=db.scalar(select(ProductionEntitlement).where(ProductionEntitlement.tenant_id==tenant_id,ProductionEntitlement.fingerprint==quote.fingerprint).with_for_update()) if quote else None
    if not entitlement or entitlement.status!='entitled':raise APIError(409,'PRODUCTION_ENTITLEMENT_CHANGED','기존 출력의 보상 등으로 무료 재출력 권한이 변경되었습니다. 최신 제작 견적을 확인해 주세요.')


def _observe(db,job_id,tenant_id,identity,prior_token,state,signature,now):
    lock_tenant(db,tenant_id)
    # Same wallet serialization as quotes, reservations and publication.
    lock_wallet(db,tenant_id,now)
    db.execute(update(Job).where(Job.id==job_id,Job.tenant_id==tenant_id).values(updated_at=Job.updated_at))
    job=db.scalar(select(Job).where(Job.id==job_id,Job.tenant_id==tenant_id).execution_options(populate_existing=True))
    if not job or job.status!='succeeded' or object_state(job).get('state') in {'deleting','deleted'} or _identity(db,job)!=identity or canonical_hash((job.result or {}).get('_integrity') or {})!=prior_token:
        return job,False
    old=job.result.get('_integrity') or {}
    current={'state':state,'reason':{'missing':'EXPORT_FILE_MISSING','corrupt':'EXPORT_CHECKSUM_MISMATCH','uncertain':'EXPORT_STORAGE_UNCERTAIN','healthy':None}[state],
        'signature':signature,'checked_at':now.isoformat(),'next_check_at':(now+(HEALTHY_INTERVAL if state=='healthy' else PROBE_INTERVAL)).isoformat()}
    if state in {'missing','corrupt'}:
        same=old.get('state')==state and old.get('signature')==signature
        first=aware(datetime.fromisoformat(old['first_observed_at'])) if same and old.get('first_observed_at') else now
        current['first_observed_at']=first.isoformat()
        if same and first<=now-PROBE_INTERVAL:
            restored=_compensate_production(db,job,now) if job.kind=='production_export' else 0
            current.update(state='unavailable',confirmed_at=now.isoformat(),credit_restored=restored)
            job.status='unavailable' if job.kind=='production_export' else 'failed'
            job.updated_at=now
            job.error='출력 파일 소실·손상이 확인되었습니다. '+('차감 크레딧을 복원했습니다. 새 제작 견적이 필요합니다.' if restored else '무료 작업은 동일 저장본으로 다시 시도할 수 있습니다.' if job.kind!='production_export' else '새 견적으로 승인 조건을 다시 확인해 주세요.')
            db.add(AuditEvent(tenant_id=tenant_id,action='export_loss_confirmed',entity_id=job.id,
                details={'reason':state,'expected_sha256':identity[1],'reservation_id':job.snapshot.get('reservation_id'),'credits_restored':restored,'automatic_regeneration':False},created_at=now))
        elif not same:
            db.add(AuditEvent(tenant_id=tenant_id,action='export_integrity_suspect',entity_id=job.id,details={'reason':state,'expected_sha256':identity[1]},created_at=now))
    elif old.get('state') in {'missing','corrupt'}:
        db.add(AuditEvent(tenant_id=tenant_id,action='export_integrity_recovered' if state=='healthy' else 'export_integrity_uncertain',entity_id=job.id,details={},created_at=now))
    # Preserve completion time used by reporting; observation times are separate.
    job.result={**job.result,'_integrity':current};db.flush()
    return job,True


def ensure_export_available(job):
    ensure_object_available(job)
    if (job.result or {}).get('_integrity',{}).get('state')=='unavailable':
        raise APIError(410,'EXPORT_FILE_UNAVAILABLE',export_availability(job)['message'])


def check_export_download(db,job,storage,*,now=None):
    """Caller must complete tenant/workspace and archive/font/ICC ACL checks first."""
    ensure_export_available(job)
    if job.kind not in KINDS or job.status!='succeeded':
        raise APIError(410 if job.status=='unavailable' else 409,'EXPORT_UNAVAILABLE','파일 상태를 확인해 주세요.',retryable=job.status not in {'unavailable','failed'})
    now=aware(now or utcnow());identity=_identity(db,job);token=canonical_hash((job.result or {}).get('_integrity') or {})
    # Authentication may have locked the member wallet. End that read/auth
    # transaction before network I/O and the tenant-first observation locks.
    # _observe checks the immutable identity, status and prior observation again.
    job_id,tenant_id=job.id,job.tenant_id
    db.commit()
    state,signature,raw=_probe(storage,identity)
    current,accepted=_observe(db,job_id,tenant_id,identity,token,state,signature,now)
    db.commit()
    if current is not None:ensure_object_available(current)
    if not accepted or current.status!='succeeded':raise APIError(409,'EXPORT_STATE_CHANGED','출력 파일 상태가 변경되었습니다. 최신 상태를 확인해 주세요.',retryable=True)
    if state=='uncertain':raise APIError(503,'EXPORT_STORAGE_UNCERTAIN','저장소에서 파일 상태를 확인하지 못했습니다. 차감·완료 이력은 유지되며 다시 시도할 수 있습니다.',retryable=True)
    if state!='healthy':raise APIError(409,'EXPORT_INTEGRITY_PENDING','출력 파일 소실 또는 손상이 관측되어 다운로드를 중지하고 재확인합니다.',retryable=True)
    return raw


def reconcile_exports(sessions,storage,*,now=None,limit=1):
    now=aware(now or utcnow());checked=0
    with sessions() as db:
        next_check=Job.result['_integrity']['next_check_at'].as_string();retention=Job.result['_retention']['state'].as_string()
        candidates=list(db.execute(select(Job.id,Job.tenant_id).where(Job.kind.in_(KINDS),Job.status=='succeeded',Job.updated_at<=now-PROBE_INTERVAL,
            or_(retention.is_(None),retention.not_in(['deleting','deleted'])),or_(next_check.is_(None),next_check<=now.isoformat()))
            .order_by(Job.updated_at,Job.id).limit(max(1,min(limit,5)))))
    for identity,tenant_id in candidates:
        with sessions() as db:
            job=db.get(Job,identity)
            if not job or job.status!='succeeded' or object_state(job).get('state') in {'deleting','deleted'}:continue
            next_check=(job.result or {}).get('_integrity',{}).get('next_check_at')
            if aware(job.updated_at)>now-PROBE_INTERVAL or next_check and aware(datetime.fromisoformat(next_check))>now:continue
            baseline=_identity(db,job);token=canonical_hash((job.result or {}).get('_integrity') or {})
        state,signature,_=_probe(storage,baseline)
        with sessions() as db:
            _,accepted=_observe(db,identity,tenant_id,baseline,token,state,signature,now)
            db.commit();checked+=int(accepted)
    return checked
