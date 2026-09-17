from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from pydantic import Field,model_validator
from ..contracts.base import ContractModel


class Plan(ContractModel):
    id:Literal['starter','pro','partner']
    name:str=Field(min_length=1,max_length=40)
    monthly_ex_vat:int=Field(ge=1,le=10000000,strict=True)
    monthly_inc_vat:int=Field(ge=1,le=11000000,strict=True)
    credits:int=Field(ge=1,le=100000,strict=True)
    seats:int=Field(ge=1,le=100,strict=True)
    @model_validator(mode='after')
    def tax(self):
        if self.monthly_inc_vat != (self.monthly_ex_vat*11+5)//10:raise ValueError('VAT 포함 금액은 공급가의 110%여야 합니다.')
        return self


class Topup(ContractModel):
    credits:Literal[500,1000]
    ex_vat:int=Field(ge=1,le=10000000,strict=True)
    inc_vat:int=Field(ge=1,le=11000000,strict=True)
    expires_months:Literal[12]=12
    @model_validator(mode='after')
    def tax(self):
        if self.inc_vat != (self.ex_vat*11+5)//10:raise ValueError('VAT 포함 금액은 공급가의 110%여야 합니다.')
        return self


class Trial(ContractModel):
    credits:Literal[30]=30
    expires_days:Literal[14]=14
    production_export:Literal[False]=False
    auto_conversion:Literal[False]=False


class PricingPolicy(ContractModel):
    currency:Literal['KRW']='KRW'
    live_billing_enabled:Literal[False]=False
    plans:list[Plan]=Field(min_length=3,max_length=3)
    topups:list[Topup]=Field(min_length=2,max_length=2)
    trial:Trial
    actions:dict[str,Annotated[int,Field(strict=True,ge=0,le=10000)]]
    @model_validator(mode='after')
    def consistent(self):
        if {p.id for p in self.plans}!={'starter','pro','partner'} or {t.credits for t in self.topups}!={500,1000}:raise ValueError('기존 요금제·충전 종류를 모두 유지해야 합니다.')
        costs={'image.generate.standard','image.edit.standard','image.generate.high','image.edit.high','export.production.first'}
        free={'editor.manual','preview.all_faces','export.production.repeat','export.review'}
        if set(self.actions)!=costs|free or any(type(v) is not int or v<0 or v>10000 for v in self.actions.values()):raise ValueError('작업별 정수 크레딧 표를 확인해 주세요.')
        if any(self.actions[k]!=0 for k in free) or any(self.actions[k]<1 for k in costs):raise ValueError('무료 수동 편집·검토·같은 항목 재출력 정책을 유지해야 합니다.')
        ordered=sorted(self.plans,key=lambda p:['starter','pro','partner'].index(p.id))
        if not (ordered[0].monthly_inc_vat<ordered[1].monthly_inc_vat<ordered[2].monthly_inc_vat and ordered[0].credits<ordered[1].credits<ordered[2].credits):raise ValueError('요금제 금액과 크레딧은 Starter < Pro < Partner 순서여야 합니다.')
        if [p.seats for p in ordered]!=[1,3,5]:raise ValueError('현재 좌석 정책 1/3/5를 유지해 주세요.')
        if self.actions['image.generate.high']<self.actions['image.generate.standard'] or self.actions['image.edit.high']<self.actions['image.edit.standard']:raise ValueError('상위 품질 단가는 표준보다 낮을 수 없습니다.')
        return self


Model=Literal['gpt-image-2.5-sunburst','gpt-image-2.5-flare']
Quality=Literal['low','medium','high','xhigh','max','auto']
class ImagePolicy(ContractModel):
    default_model:Model
    allowed_models:list[Model]=Field(min_length=1,max_length=2)
    allowed_qualities:list[Quality]=Field(min_length=1,max_length=6)
    high_enabled:bool
    daily_limit_usd:float=Field(gt=0,le=100000,allow_inf_nan=False)
    unknown_request_allowance_usd:float=Field(gt=0,le=100000,allow_inf_nan=False)
    monthly_alert_usd:float=Field(gt=0,le=100000,allow_inf_nan=False)
    @model_validator(mode='after')
    def consistent(self):
        if len(set(self.allowed_models))!=len(self.allowed_models) or self.default_model not in self.allowed_models:raise ValueError('기본 모델은 중복 없는 허용 모델에 포함되어야 합니다.')
        if len(set(self.allowed_qualities))!=len(self.allowed_qualities):raise ValueError('품질을 중복 지정할 수 없습니다.')
        if 'high' not in self.allowed_qualities:raise ValueError('현재 UI의 기본 품질 high를 유지해야 합니다.')
        return self


class CreatePolicy(ContractModel):
    kind:Literal['pricing','image']
    reason:str=Field(min_length=5,max_length=1000)
    payload:PricingPolicy|ImagePolicy
    @model_validator(mode='after')
    def kind_matches(self):
        if (self.kind=='pricing') != isinstance(self.payload,PricingPolicy):raise ValueError('정책 종류와 입력 내용이 다릅니다.')
        return self


class PublishPolicy(ContractModel):
    expected_active_id:UUID|None
    reason:str=Field(min_length=5,max_length=1000)
    understands_existing_subscriptions_unchanged:Literal[True]


class CorrectionBody(ContractModel):
    tenant_id:UUID
    amount:int=Field(ge=-100000,le=100000,strict=True)
    scope:Literal['standard_only','paid']
    bucket_id:UUID|None=None
    expires_days:int=Field(ge=1,le=365,default=7)
    reason:str=Field(min_length=5,max_length=500)
    @model_validator(mode='after')
    def valid(self):
        if self.amount==0 or self.amount<0 and not self.bucket_id or self.amount>0 and self.bucket_id:raise ValueError('지급은 새 버킷, 회수는 지정 버킷의 미사용분으로 처리합니다.')
        return self


class CorrectionDTO(ContractModel):
    id:str
    tenant_id:str
    amount:int
    bucket_id:str
    scope:str
    reason:str
    actor_id:str
    created_at:datetime


class PolicyDTO(ContractModel):
    id:str
    kind:Literal['pricing','image']
    version:str
    payload:PricingPolicy|ImagePolicy
    payload_hash:str
    reason:str
    created_at:datetime
    published_at:datetime|None
    active:bool


class PolicyOverview(ContractModel):
    versions:list[PolicyDTO]
    pricing:PricingPolicy
    pricing_version:str
    image:ImagePolicy
    active_pricing_id:str|None
    active_image_id:str|None
    live_billing_enabled:Literal[False]=False
    notice:str


class CorrectionsDTO(ContractModel):
    items:list[CorrectionDTO]
