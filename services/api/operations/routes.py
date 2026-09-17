from uuid import UUID
from fastapi import APIRouter,Depends,Query,Request
from sqlalchemy import select
from ..auth import require_auth
from ..billing.policy import pricing,aware
from ..billing.service import wallet_summary
from ..contracts.base import ContractModel,Envelope,ERROR_RESPONSES
from ..contracts.billing import WalletSummary
from ..errors import APIError
from ..models import Tenant
from .models import PolicyVersion,CreditCorrection
from .schemas import CreatePolicy,PublishPolicy,PolicyDTO,PolicyOverview,CorrectionBody,CorrectionDTO,CorrectionsDTO,PricingPolicy
from .service import active_version,image_defaults,image_settings,create_policy,publish_policy,correct_credits

NOTICE='게시한 요금은 새 견적과 새 주문에 적용됩니다. 기존 구독은 동의한 조건을 유지합니다. 정책 게시로 실제 결제·제작 게이트를 열 수 없습니다.'


class WalletDTO(ContractModel):
    tenant_id:str
    tenant_name:str
    summary:WalletSummary


class PublicPricing(ContractModel):
    policy:PricingPolicy
    version:str


def policy_payload(row,active_id=None):
    return {k:getattr(row,k) for k in ('id','kind','version','payload','payload_hash','reason')} | {'created_at':aware(row.created_at),'published_at':aware(row.published_at) if row.published_at else None,'active':row.id==active_id}


def correction_payload(row):
    return {k:getattr(row,k) for k in ('id','tenant_id','amount','bucket_id','scope','reason','actor_id')} | {'created_at':aware(row.created_at)}


def install_operation_routes(app,db_session):
    router=APIRouter(prefix='/v1',tags=['operation-policy'],responses=ERROR_RESPONSES)
    def admin(request,db,mutate=False):
        # Global operator authority is independent of the selected team's paid
        # membership. Avoid acquiring subscription/pricing read locks first.
        user,_=require_auth(request,db,mutate=mutate,authorize_write=False,enforce_membership=False)
        if not user.is_admin:raise APIError(403,'ADMIN_REQUIRED','운영 관리자만 정책과 크레딧 조정을 관리할 수 있습니다.')
        return user
    def result(request,data):return {'data':data,'request_id':request.state.request_id}

    @router.get('/pricing',response_model=Envelope[PublicPricing],response_model_exclude_unset=True)
    def public_pricing(request:Request,db=Depends(db_session)):
        value=pricing(db);version=value.pop('version');return result(request,{'policy':value,'version':version})

    @router.get('/admin/policies',response_model=Envelope[PolicyOverview],response_model_exclude_unset=True)
    def overview(request:Request,db=Depends(db_session)):
        admin(request,db);p=active_version(db,'pricing');i=active_version(db,'image');value=pricing(db);version=value.pop('version')
        rows=db.scalars(select(PolicyVersion).order_by(PolicyVersion.created_at.desc()).limit(100))
        return result(request,{'versions':[policy_payload(row,p.id if row.kind=='pricing' and p else i.id if row.kind=='image' and i else None) for row in rows],
            'pricing':value,'pricing_version':version,'image':image_defaults(image_settings(db,app.state.settings)),'active_pricing_id':p.id if p else None,'active_image_id':i.id if i else None,'live_billing_enabled':False,'notice':NOTICE})

    @router.post('/admin/policies',status_code=201,response_model=Envelope[PolicyDTO],response_model_exclude_unset=True)
    def create(body:CreatePolicy,request:Request,db=Depends(db_session)):
        user=admin(request,db,True);row=create_policy(db,user,body,app.state.settings);db.commit();return result(request,policy_payload(row))

    @router.post('/admin/policies/{identity}/publish',response_model=Envelope[PolicyDTO],response_model_exclude_unset=True)
    def publish(identity:UUID,body:PublishPolicy,request:Request,db=Depends(db_session)):
        user=admin(request,db,True);row=publish_policy(db,user,identity,body,app.state.settings);db.commit();return result(request,policy_payload(row,row.id))

    @router.get('/admin/credit-wallets/{tenant_id}',response_model=Envelope[WalletDTO],response_model_exclude_unset=True)
    def wallet(tenant_id:UUID,request:Request,db=Depends(db_session)):
        admin(request,db);tenant=db.get(Tenant,str(tenant_id))
        if not tenant:raise APIError(404,'TENANT_NOT_FOUND','작업 공간을 찾을 수 없습니다.')
        data=wallet_summary(db,tenant.id);db.commit();return result(request,{'tenant_id':tenant.id,'tenant_name':tenant.name,'summary':data})

    @router.get('/admin/credit-corrections',response_model=Envelope[CorrectionsDTO],response_model_exclude_unset=True)
    def corrections(request:Request,tenant_id:UUID,db=Depends(db_session)):
        admin(request,db);rows=db.scalars(select(CreditCorrection).where(CreditCorrection.tenant_id==str(tenant_id)).order_by(CreditCorrection.created_at.desc()).limit(100))
        return result(request,{'items':[correction_payload(r) for r in rows]})

    @router.post('/admin/credit-corrections',status_code=201,response_model=Envelope[CorrectionDTO],response_model_exclude_unset=True)
    def correction(body:CorrectionBody,request:Request,db=Depends(db_session)):
        user=admin(request,db,True);row=correct_credits(db,user,body,request.headers.get('Idempotency-Key',''));db.commit();return result(request,correction_payload(row))

    app.include_router(router)
