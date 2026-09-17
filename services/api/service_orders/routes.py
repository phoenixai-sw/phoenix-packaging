from copy import deepcopy
from datetime import timedelta
from uuid import UUID
from fastapi import APIRouter,Depends,Query,Request
from sqlalchemy import select,update,func
from ..auth import require_auth
from ..business import owned_record
from ..billing.policy import aware
from ..billing.service import canonical_hash,lock_wallet
from ..contracts.base import Envelope,ERROR_RESPONSES
from ..database import utcnow
from ..errors import APIError
from ..feature_models import AuditEvent
from ..models import Project
from .catalog import CATALOG,NOTICE,POLICY_VERSION
from .models import ServiceOrder,ServiceOrderEvent,ServiceQuote
from .checkout import checkout_payload,linked_order,make_quote_terms
from .schemas import CatalogPayload,CreateServiceOrder,QuoteServiceOrder,ServiceOrderAction,AcceptServiceQuote,ServiceWorkTransition,ServiceOrderPayload,ServiceOrderList


def service_payload(db,row,settings):
    quotes=list(db.scalars(select(ServiceQuote).where(ServiceQuote.order_id==row.id).order_by(ServiceQuote.number.desc())))
    events=list(db.scalars(select(ServiceOrderEvent).where(ServiceOrderEvent.order_id==row.id).order_by(ServiceOrderEvent.order_revision.desc())))
    keys=('id','service_code','project_id','request_note','catalog_snapshot','catalog_policy_version','status','revision','current_quote_id','accepted_quote_id','created_at','updated_at')
    def value(record,key):
        raw=getattr(record,key)
        return aware(raw) if key.endswith('_at') and raw is not None else raw
    return {**{k:value(row,k) for k in keys},'quotes':[{**{k:value(q,k) for k in ('id','number','amount_inc_vat','currency','scope','exclusions','expires_at','policy_version','reason','created_at')},'checkout_terms':q.checkout_terms['public'] if q.checkout_terms else None} for q in quotes],
        'events':[{k:value(e,k) for k in ('id','order_revision','kind','note','quote_id','created_at')} for e in events],
        **checkout_payload(db,row,settings),'admin_notice':NOTICE}


def _event(db,row,user,kind,note,quote_id=None):
    db.add(ServiceOrderEvent(tenant_id=row.tenant_id,order_id=row.id,order_revision=row.revision,actor_id=user.id,kind=kind,note=note,quote_id=quote_id))
    db.add(AuditEvent(tenant_id=row.tenant_id,actor_id=user.id,action='service_order.'+kind,entity_id=row.id,details={'revision':row.revision,'quote_id':quote_id}))


def _change(db,row,user,revision,kind,note,**values):
    changed=db.execute(update(ServiceOrder).where(ServiceOrder.id==row.id,ServiceOrder.revision==revision).values(revision=revision+1,updated_at=utcnow(),**values).execution_options(synchronize_session=False))
    if changed.rowcount!=1:raise APIError(409,'SERVICE_ORDER_CONFLICT','신청 내용이 변경됐습니다. 최신 내역을 확인해 주세요.')
    db.refresh(row);_event(db,row,user,kind,note,row.current_quote_id)


def install_service_order_routes(app,db_session):
    router=APIRouter(prefix='/v1',tags=['service-orders'],responses=ERROR_RESPONSES)
    def result(request,data):return {'data':data,'request_id':request.state.request_id}
    def payload(db,row):return service_payload(db,row,app.state.billing_settings)
    def owner(request,db,mutate=False):
        user,_=require_auth(request,db,mutate=mutate)
        if user.role!='owner':raise APIError(403,'OWNER_REQUIRED','별도 서비스 신청과 견적 수락은 소유자만 할 수 있습니다.')
        return user
    def admin(request,db,mutate=False):
        user,_=require_auth(request,db,mutate=mutate,authorize_write=False,enforce_membership=False)
        if not user.is_admin:raise APIError(403,'ADMIN_REQUIRED','운영 관리자 권한이 필요합니다.')
        return user
    def load(db,identity,user=None,revision=None):
        row=db.get(ServiceOrder,str(identity))
        if row is None or user and row.tenant_id!=user.tenant_id:raise APIError(404,'SERVICE_ORDER_NOT_FOUND','신청을 찾을 수 없습니다.')
        if revision is not None:
            lock_wallet(db,row.tenant_id)
            db.refresh(row)
            if row.revision!=revision:raise APIError(409,'SERVICE_ORDER_CONFLICT','최신 신청 내역을 확인해 주세요.')
        return row

    @router.get('/service-catalog',response_model=Envelope[CatalogPayload],response_model_exclude_unset=True)
    def catalog(request:Request,db=Depends(db_session)):
        require_auth(request,db)
        enabled=app.state.billing_settings.capabilities()['checkout_available']
        return result(request,{'policy_version':POLICY_VERSION,'currency':'KRW','items':[{**item,'checkout_enabled':enabled} for item in CATALOG.values()],'notice':NOTICE})

    @router.post('/service-orders',status_code=201,response_model=Envelope[ServiceOrderPayload],response_model_exclude_unset=True)
    def create(body:CreateServiceOrder,request:Request,db=Depends(db_session)):
        user=owner(request,db,True)
        key=request.headers.get('Idempotency-Key','')
        if not key or len(key)>160:raise APIError(422,'IDEMPOTENCY_KEY_REQUIRED','신청 요청 식별자가 필요합니다.')
        digest=canonical_hash(body.model_dump(mode='json'))
        lock_wallet(db,user.tenant_id)
        old=db.scalar(select(ServiceOrder).where(ServiceOrder.tenant_id==user.tenant_id,ServiceOrder.operation_key==key))
        if old:
            if old.request_hash!=digest:raise APIError(409,'IDEMPOTENCY_CONFLICT','동일 식별자로 다른 신청을 만들 수 없습니다.')
            data=payload(db,old);db.commit();return result(request,data)
        if body.project_id:owned_record(db,Project,body.project_id,user.tenant_id)
        if body.service_code=='file_review' and not body.project_id:raise APIError(422,'SERVICE_PROJECT_REQUIRED','파일 검토를 요청할 프로젝트를 선택해 주세요.')
        row=ServiceOrder(tenant_id=user.tenant_id,requested_by=user.id,service_code=body.service_code,project_id=str(body.project_id) if body.project_id else None,
            request_note=body.request_note,catalog_snapshot=deepcopy(CATALOG[body.service_code]),catalog_policy_version=POLICY_VERSION,operation_key=key,request_hash=digest)
        db.add(row);db.flush();_event(db,row,user,'requested',body.request_note);db.commit();return result(request,payload(db,row))

    @router.get('/service-orders',response_model=Envelope[ServiceOrderList],response_model_exclude_unset=True)
    def listing(request:Request,cursor:UUID|None=None,limit:int=Query(default=20,ge=1,le=50),db=Depends(db_session)):
        user=owner(request,db);query=select(ServiceOrder).where(ServiceOrder.tenant_id==user.tenant_id)
        if cursor:
            previous=load(db,cursor,user)
            query=query.where((ServiceOrder.created_at<previous.created_at)|((ServiceOrder.created_at==previous.created_at)&(ServiceOrder.id<previous.id)))
        rows=list(db.scalars(query.order_by(ServiceOrder.created_at.desc(),ServiceOrder.id.desc()).limit(limit+1)))
        return result(request,{'items':[payload(db,r) for r in rows[:limit]],'next_cursor':rows[limit-1].id if len(rows)>limit else None})

    @router.get('/service-orders/{identity}',response_model=Envelope[ServiceOrderPayload],response_model_exclude_unset=True)
    def detail(identity:UUID,request:Request,db=Depends(db_session)):
        user=owner(request,db);return result(request,payload(db,load(db,identity,user)))

    @router.post('/service-orders/{identity}/accept',response_model=Envelope[ServiceOrderPayload],response_model_exclude_unset=True)
    def accept(identity:UUID,body:AcceptServiceQuote,request:Request,db=Depends(db_session)):
        user=owner(request,db,True);row=load(db,identity,user,body.base_revision)
        q=db.get(ServiceQuote,str(body.quote_id))
        if row.status!='quoted' or not q or q.order_id!=row.id or q.id!=row.current_quote_id:raise APIError(409,'SERVICE_QUOTE_CHANGED','현재 견적을 확인한 뒤 수락해 주세요.')
        if aware(q.expires_at)<=aware(utcnow()):raise APIError(409,'SERVICE_QUOTE_EXPIRED','견적 기한이 지났습니다. 새 견적을 요청해 주세요.')
        _change(db,row,user,body.base_revision,'accepted',body.note,status='accepted',accepted_quote_id=q.id)
        db.commit();return result(request,payload(db,row))

    @router.post('/service-orders/{identity}/cancel',response_model=Envelope[ServiceOrderPayload],response_model_exclude_unset=True)
    def cancel(identity:UUID,body:ServiceOrderAction,request:Request,db=Depends(db_session)):
        user=owner(request,db,True);row=load(db,identity,user,body.base_revision)
        if row.status not in {'requested','quoted','accepted'}:raise APIError(409,'SERVICE_CANCEL_NOT_ALLOWED','진행 중인 업무는 담당자와 범위를 확인해 주세요.')
        payment=linked_order(db,row.id)
        if payment and payment.status!='refunded':raise APIError(409,'SERVICE_PAYMENT_PENDING','연결된 결제의 승인·취소 결과를 확인해야 신청을 취소할 수 있습니다.')
        _change(db,row,user,body.base_revision,'canceled',body.note,status='canceled');db.commit();return result(request,payload(db,row))

    @router.get('/admin/service-orders',response_model=Envelope[ServiceOrderList],response_model_exclude_unset=True)
    def admin_list(request:Request,cursor:UUID|None=None,limit:int=Query(default=20,ge=1,le=50),db=Depends(db_session)):
        user=admin(request,db);query=select(ServiceOrder)
        if cursor:
            prev=load(db,cursor);query=query.where((ServiceOrder.created_at<prev.created_at)|((ServiceOrder.created_at==prev.created_at)&(ServiceOrder.id<prev.id)))
        rows=list(db.scalars(query.order_by(ServiceOrder.created_at.desc(),ServiceOrder.id.desc()).limit(limit+1)))
        # Admin only sees submitted request text/quotes; this route grants no project or asset reads.
        db.add(AuditEvent(tenant_id=user.tenant_id,actor_id=user.id,action='service_order.queue_viewed',details={'count':min(len(rows),limit)}));db.commit()
        return result(request,{'items':[payload(db,r) for r in rows[:limit]],'next_cursor':rows[limit-1].id if len(rows)>limit else None})

    @router.post('/admin/service-orders/{identity}/quotes',response_model=Envelope[ServiceOrderPayload],response_model_exclude_unset=True)
    def quote(identity:UUID,body:QuoteServiceOrder,request:Request,db=Depends(db_session)):
        user=admin(request,db,True);row=load(db,identity,revision=body.base_revision)
        old_quote=db.get(ServiceQuote,row.accepted_quote_id) if row.accepted_quote_id else None
        legacy_requote=row.status=='accepted' and old_quote and not old_quote.checkout_terms and not linked_order(db,row.id)
        if row.status not in {'requested','quoted'} and not legacy_requote:raise APIError(409,'SERVICE_QUOTE_LOCKED','수락한 견적을 변경할 수 없습니다. 새 신청에서 협의해 주세요.')
        number=(db.scalar(select(func.max(ServiceQuote.number)).where(ServiceQuote.order_id==row.id)) or 0)+1
        q=ServiceQuote(tenant_id=row.tenant_id,order_id=row.id,number=number,amount_inc_vat=body.amount_inc_vat,scope=body.scope,exclusions=body.exclusions,
            expires_at=utcnow()+timedelta(days=body.valid_days),policy_version=POLICY_VERSION,quoted_by=user.id,reason=body.reason,
            checkout_terms=make_quote_terms(db,row.service_code,body.amount_inc_vat,body.scope,body.exclusions))
        db.add(q);db.flush();_change(db,row,user,body.base_revision,'quoted',body.reason,status='quoted',current_quote_id=q.id,accepted_quote_id=None)
        db.commit();return result(request,payload(db,row))

    @router.post('/admin/service-orders/{identity}/transition',response_model=Envelope[ServiceOrderPayload],response_model_exclude_unset=True)
    def transition(identity:UUID,body:ServiceWorkTransition,request:Request,db=Depends(db_session)):
        user=admin(request,db,True);row=load(db,identity,revision=body.base_revision)
        allowed={'requested':{'rejected'},'quoted':{'rejected'},'accepted':{'in_progress'},'in_progress':{'delivered'},'delivered':{'completed'}}
        if body.status not in allowed.get(row.status,set()):raise APIError(409,'SERVICE_TRANSITION_INVALID','현재 단계에서 가능한 업무 상태가 아닙니다.')
        payment=linked_order(db,row.id)
        accepted=db.get(ServiceQuote,row.accepted_quote_id) if row.accepted_quote_id else None
        if row.service_code=='pilot_pro_first_month' and body.status!='rejected' and (not payment or payment.status!='paid'):raise APIError(409,'SUBSCRIPTION_FULFILLMENT_REQUIRED','모집 문의는 실제 구독 결제 없이 개통·완료로 처리할 수 없습니다.')
        if body.status!='rejected' and accepted and accepted.checkout_terms and accepted.amount_inc_vat>0 and (not payment or payment.status!='paid'):
            raise APIError(409,'SERVICE_PAYMENT_REQUIRED','수납 상태를 확인한 뒤 업무를 진행해 주세요.')
        _change(db,row,user,body.base_revision,body.status,body.note,status=body.status);db.commit();return result(request,payload(db,row))

    app.include_router(router)
