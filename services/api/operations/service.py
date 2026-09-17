from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select,update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from ..database import utcnow
from ..errors import APIError
from ..feature_models import AuditEvent
from ..models import Tenant
from ..billing.models import CreditBucket
from ..billing.policy import aware
from ..billing.service import canonical_hash,lock_wallet,_event
from .models import ActivePolicy,PolicyVersion,CreditCorrection
from .schemas import PricingPolicy,ImagePolicy


def active_version(db,kind):
    pointer=db.scalar(select(ActivePolicy).where(ActivePolicy.kind==kind).with_for_update(read=True).execution_options(populate_existing=True))
    if pointer is None:
        # A seed policy needs the same synchronization row as a published one.
        # This fallback also covers isolated Base.create_all test databases.
        insert=pg_insert if db.get_bind().dialect.name=='postgresql' else sqlite_insert
        db.execute(insert(ActivePolicy).values(kind=kind,version_id=None,revision=0,updated_at=utcnow()).on_conflict_do_nothing(index_elements=['kind']))
        pointer=db.scalar(select(ActivePolicy).where(ActivePolicy.kind==kind).with_for_update(read=True).execution_options(populate_existing=True))
    row=db.get(PolicyVersion,pointer.version_id) if pointer and pointer.version_id else None
    if pointer and pointer.version_id and row is None or row and (row.kind!=kind or not row.published_at or canonical_hash(row.payload)!=row.payload_hash):
        raise APIError(503,'POLICY_INTEGRITY_FAILED','운영 정책을 확인하고 있습니다. 잠시 후 다시 시도해 주세요.')
    return row


def image_defaults(settings):
    return {'default_model':settings.image_model,'allowed_models':list(settings.ai_image_models),'allowed_qualities':list(settings.ai_image_qualities),
        'high_enabled':settings.ai_high_enabled,'daily_limit_usd':settings.ai_daily_cost_limit_usd,'unknown_request_allowance_usd':settings.ai_request_allowance_usd,'monthly_alert_usd':settings.ai_monthly_budget_alert_usd}


def image_settings(db,settings):
    row=active_version(db,'image')
    if not row:return settings
    value=ImagePolicy.model_validate(row.payload)
    allowed=tuple(m for m in value.allowed_models if m in settings.ai_image_models)
    if value.default_model not in allowed:raise APIError(503,'MODEL_POLICY_UNAVAILABLE','게시한 모델과 서버 허용 설정을 확인해 주세요.')
    return replace(settings,image_model=value.default_model,ai_image_models=allowed,ai_image_qualities=tuple(q for q in value.allowed_qualities if q in settings.ai_image_qualities),
        ai_high_enabled=settings.ai_high_enabled and value.high_enabled,
        ai_daily_cost_limit_usd=min(settings.ai_daily_cost_limit_usd,value.daily_limit_usd),
        ai_request_allowance_usd=max(settings.ai_request_allowance_usd,value.unknown_request_allowance_usd),
        ai_monthly_budget_alert_usd=min(settings.ai_monthly_budget_alert_usd,value.monthly_alert_usd))


def create_policy(db,user,body,settings):
    data=body.payload.model_dump(mode='json')
    if body.kind=='image':
        if not set(data['allowed_models'])<=set(settings.ai_image_models) or not set(data['allowed_qualities'])<=set(settings.ai_image_qualities):raise APIError(422,'MODEL_POLICY_NOT_DEPLOYED','서버에서 허용한 모델과 품질만 선택할 수 있습니다.')
        if data['high_enabled'] and not settings.ai_high_enabled:raise APIError(422,'HIGH_POLICY_NOT_DEPLOYED','서버의 고품질 사용 설정을 먼저 확인해 주세요.')
        if data['daily_limit_usd']>settings.ai_daily_cost_limit_usd or data['monthly_alert_usd']>settings.ai_monthly_budget_alert_usd or data['unknown_request_allowance_usd']<settings.ai_request_allowance_usd:raise APIError(422,'BUDGET_POLICY_LIMIT','화면 정책은 배포한 서버 예산 보호보다 완화할 수 없습니다.')
    row=PolicyVersion(kind=body.kind,version=f'{body.kind}-{utcnow():%Y%m%d}-{uuid4().hex[:12]}',payload=data,payload_hash=canonical_hash(data),reason=body.reason,created_by=user.id)
    db.add(row);db.flush()
    db.add(AuditEvent(tenant_id=user.tenant_id,actor_id=user.id,action='policy.draft_created',entity_id=row.id,details={'kind':row.kind,'version':row.version,'reason':body.reason,'payload_hash':row.payload_hash}))
    return row


def publish_policy(db,user,identity,body,settings):
    row=db.get(PolicyVersion,str(identity))
    if not row:raise APIError(404,'POLICY_NOT_FOUND','정책 버전을 찾을 수 없습니다.')
    if canonical_hash(row.payload)!=row.payload_hash:raise APIError(409,'POLICY_HASH_MISMATCH','정책 내용이 변경되어 게시할 수 없습니다.')
    (PricingPolicy if row.kind=='pricing' else ImagePolicy).model_validate(row.payload)
    dialect=db.get_bind().dialect.name;insert=pg_insert if dialect=='postgresql' else sqlite_insert
    db.execute(insert(ActivePolicy).values(kind=row.kind,version_id=None,revision=0,updated_at=utcnow()).on_conflict_do_nothing(index_elements=['kind']))
    db.execute(update(ActivePolicy).where(ActivePolicy.kind==row.kind).values(revision=ActivePolicy.revision+1))
    state=db.scalar(select(ActivePolicy).where(ActivePolicy.kind==row.kind).execution_options(populate_existing=True))
    # Another publisher may have committed this exact draft while we waited
    # for the global pointer; do not trust an earlier identity-map copy.
    db.refresh(row)
    if canonical_hash(row.payload)!=row.payload_hash:raise APIError(409,'POLICY_HASH_MISMATCH','정책 내용이 변경되어 게시할 수 없습니다.')
    if state.version_id != (str(body.expected_active_id) if body.expected_active_id else None):raise APIError(409,'POLICY_CHANGED','다른 관리자가 정책을 게시했습니다. 최신 내용을 확인해 주세요.')
    if row.published_at:raise APIError(409,'POLICY_ALREADY_PUBLISHED','이미 게시한 버전입니다. 이전 내용으로 복원하려면 새 초안을 만들어 주세요.')
    row.published_at=utcnow();state.version_id=row.id;state.updated_at=row.published_at
    db.flush()
    if row.kind=='image':image_settings(db,settings)
    db.add(AuditEvent(tenant_id=user.tenant_id,actor_id=user.id,action='policy.published',entity_id=row.id,details={'kind':row.kind,'version':row.version,'previous_id':str(body.expected_active_id) if body.expected_active_id else None,'reason':body.reason,'payload_hash':row.payload_hash}))
    return row


def correct_credits(db,user,body,key):
    if not key or len(key)>160:raise APIError(422,'IDEMPOTENCY_KEY_REQUIRED','조정 요청 식별자가 필요합니다.')
    tenant_id=str(body.tenant_id)
    if not db.get(Tenant,tenant_id):raise APIError(404,'TENANT_NOT_FOUND','작업 공간을 찾을 수 없습니다.')
    now=utcnow();lock_wallet(db,tenant_id,now)
    digest=canonical_hash(body.model_dump(mode='json'))
    old=db.scalar(select(CreditCorrection).where(CreditCorrection.tenant_id==tenant_id,CreditCorrection.operation_key==key))
    if old:
        if old.request_hash!=digest:raise APIError(409,'IDEMPOTENCY_CONFLICT','같은 조정 식별자로 다른 내용을 보낼 수 없습니다.')
        return old
    identity=str(uuid4())
    if body.amount>0:
        bucket=CreditBucket(tenant_id=tenant_id,kind='adjustment',scope=body.scope,grant_key='admin-adjustment:'+identity,granted=body.amount,available=body.amount,expires_at=now+timedelta(days=body.expires_days))
        db.add(bucket);db.flush()
    else:
        bucket=db.get(CreditBucket,str(body.bucket_id))
        if not bucket or bucket.tenant_id!=tenant_id:raise APIError(404,'BUCKET_NOT_FOUND','이 작업 공간의 크레딧 지급분이 아닙니다.')
        if bucket.scope!=body.scope:raise APIError(422,'CREDIT_SCOPE_MISMATCH','원래 지급분의 사용 범위를 유지해야 합니다.')
        if aware(bucket.expires_at)<=aware(now) or bucket.available < -body.amount:raise APIError(409,'ADJUSTMENT_UNAVAILABLE','유효한 미사용분만 회수할 수 있습니다. 예약·소비·만료 내역은 변경하지 않습니다.')
        bucket.available+=body.amount;bucket.expired-=body.amount
    row=CreditCorrection(id=identity,tenant_id=tenant_id,operation_key=key,request_hash=digest,amount=body.amount,bucket_id=bucket.id,scope=body.scope,reason=body.reason,actor_id=user.id)
    db.add(row)
    _event(db,tenant_id,bucket.id,'ADJUSTMENT',body.amount,'admin-adjustment:'+identity,body.reason,now,actor_id=user.id,operation_key=key)
    db.add(AuditEvent(tenant_id=tenant_id,actor_id=user.id,action='credits.adjusted',entity_id=identity,details={'amount':body.amount,'bucket_id':bucket.id,'scope':body.scope,'reason':body.reason}))
    db.flush();return row
