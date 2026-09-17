from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import Field
from ..contracts.base import ContractModel
from ..contracts.billing import PaymentOrderData,SubscriptionData

ServiceCode=Literal['file_review','onboarding','pilot_pro_first_month']
ServiceStatus=Literal['requested','quoted','accepted','in_progress','delivered','completed','canceled','rejected']


class ServiceCatalogItem(ContractModel):
    code:ServiceCode
    name:str
    suggested_amount_krw:int
    price_note:str
    scope:str
    automatic_renewal:bool=False
    credit_grant:int=0
    checkout_enabled:bool=False


class CatalogPayload(ContractModel):
    policy_version:str
    currency:Literal['KRW']='KRW'
    items:list[ServiceCatalogItem]
    notice:str


class CreateServiceOrder(ContractModel):
    service_code:ServiceCode
    project_id:UUID|None=None
    request_note:str=Field(min_length=3,max_length=4000)


class QuoteServiceOrder(ContractModel):
    base_revision:int=Field(ge=1)
    amount_inc_vat:int=Field(ge=0,le=10000000,strict=True)
    scope:str=Field(min_length=5,max_length=4000)
    exclusions:str=Field(min_length=3,max_length=2000)
    valid_days:int=Field(ge=1,le=30,default=7)
    reason:str=Field(min_length=3,max_length=500)


class ServiceOrderAction(ContractModel):
    base_revision:int=Field(ge=1)
    note:str=Field(min_length=3,max_length=2000)


class AcceptServiceQuote(ServiceOrderAction):
    quote_id:UUID
    understands_no_payment:Literal[True]


class ServiceWorkTransition(ServiceOrderAction):
    status:Literal['in_progress','delivered','completed','rejected']


class ServiceQuotePayload(ContractModel):
    id:str
    number:int
    amount_inc_vat:int
    currency:Literal['KRW']='KRW'
    scope:str
    exclusions:str
    expires_at:datetime
    policy_version:str
    reason:str
    created_at:datetime
    checkout_terms:'CheckoutTerms | None'=None


class CheckoutTerms(ContractModel):
    terms_version:str
    service_code:ServiceCode
    amount_inc_vat:int
    currency:Literal['KRW']
    vat_included:Literal[True]
    checkout_kind:Literal['payment','billing_auth']
    automatic_renewal:bool
    credits:int
    seats:int|None
    plan_id:Literal['pro']|None
    renewal_amount_inc_vat:int|None
    renewal_interval:Literal['month']|None
    pricing_version:str
    policy_version:str
    proposal_only:Literal[True]
    first_period_discount_inc_vat:int
    upgrade_proration_basis:Literal['paid_first_period_amount']|None


ServiceQuotePayload.model_rebuild()


class ServiceEventPayload(ContractModel):
    id:str
    order_revision:int
    kind:str
    note:str
    quote_id:str|None
    created_at:datetime


class ServiceOrderPayload(ContractModel):
    id:str
    service_code:ServiceCode
    project_id:str|None
    request_note:str
    catalog_snapshot:ServiceCatalogItem
    catalog_policy_version:str
    status:ServiceStatus
    revision:int
    current_quote_id:str|None
    accepted_quote_id:str|None
    created_at:datetime
    updated_at:datetime
    quotes:list[ServiceQuotePayload]
    events:list[ServiceEventPayload]
    payment_status:str='not_collected'
    credits_granted:int=0
    checkout_enabled:bool=False
    checkout_blocked_reason:str|None=None
    checkout_terms:CheckoutTerms|None=None
    payment_order:PaymentOrderData|None=None
    payment_retry_allowed:bool=False
    payment_retry_blocked_reason:str|None=None
    refund_allowed:bool=False
    refund_blocked_reason:str|None=None
    subscription_active:bool=False
    subscription:SubscriptionData|None=None
    admin_notice:str|None=None


class ServiceOrderList(ContractModel):
    items:list[ServiceOrderPayload]
    next_cursor:str|None=None
