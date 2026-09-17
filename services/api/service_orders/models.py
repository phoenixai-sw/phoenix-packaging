from datetime import datetime
from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ..database import Base,new_id,utcnow


class ServiceOrder(Base):
    __tablename__='service_orders'
    __table_args__=(UniqueConstraint('tenant_id','operation_key',name='uq_service_order_operation'),CheckConstraint('revision > 0',name='ck_service_order_revision'),CheckConstraint("status IN ('requested','quoted','accepted','in_progress','delivered','completed','canceled','rejected')",name='ck_service_order_status'))
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=new_id)
    tenant_id:Mapped[str]=mapped_column(ForeignKey('tenants.id'),index=True)
    requested_by:Mapped[str]=mapped_column(ForeignKey('users.id'))
    service_code:Mapped[str]=mapped_column(String(40))
    project_id:Mapped[str|None]=mapped_column(ForeignKey('projects.id'),nullable=True)
    request_note:Mapped[str]=mapped_column(Text)
    catalog_snapshot:Mapped[dict]=mapped_column(JSON)
    catalog_policy_version:Mapped[str]=mapped_column(String(80))
    operation_key:Mapped[str]=mapped_column(String(160))
    request_hash:Mapped[str]=mapped_column(String(64))
    status:Mapped[str]=mapped_column(String(30),default='requested',index=True)
    revision:Mapped[int]=mapped_column(Integer,default=1)
    current_quote_id:Mapped[str|None]=mapped_column(String(36),nullable=True)
    accepted_quote_id:Mapped[str|None]=mapped_column(String(36),nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class ServiceQuote(Base):
    __tablename__='service_order_quotes'
    __table_args__=(UniqueConstraint('order_id','number',name='uq_service_quote_number'),CheckConstraint('amount_inc_vat >= 0 AND amount_inc_vat <= 10000000',name='ck_service_quote_amount'))
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=new_id)
    tenant_id:Mapped[str]=mapped_column(ForeignKey('tenants.id'),index=True)
    order_id:Mapped[str]=mapped_column(ForeignKey('service_orders.id'),index=True)
    number:Mapped[int]=mapped_column(Integer)
    amount_inc_vat:Mapped[int]=mapped_column(Integer)
    currency:Mapped[str]=mapped_column(String(3),default='KRW')
    scope:Mapped[str]=mapped_column(Text)
    exclusions:Mapped[str]=mapped_column(Text)
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    policy_version:Mapped[str]=mapped_column(String(80))
    quoted_by:Mapped[str]=mapped_column(ForeignKey('users.id'))
    reason:Mapped[str]=mapped_column(String(500))
    checkout_terms:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class ServiceOrderEvent(Base):
    __tablename__='service_order_events'
    __table_args__=(UniqueConstraint('order_id','order_revision',name='uq_service_order_event_revision'),)
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=new_id)
    tenant_id:Mapped[str]=mapped_column(ForeignKey('tenants.id'),index=True)
    order_id:Mapped[str]=mapped_column(ForeignKey('service_orders.id'),index=True)
    order_revision:Mapped[int]=mapped_column(Integer)
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    kind:Mapped[str]=mapped_column(String(40))
    note:Mapped[str]=mapped_column(Text)
    quote_id:Mapped[str|None]=mapped_column(ForeignKey('service_order_quotes.id'),nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class ServiceCheckout(Base):
    """Immutable link from one accepted quote to one reusable PG order."""
    __tablename__='service_checkouts'
    payment_order_id:Mapped[str]=mapped_column(ForeignKey('payment_orders.id'),primary_key=True)
    tenant_id:Mapped[str]=mapped_column(ForeignKey('tenants.id'),index=True)
    service_order_id:Mapped[str]=mapped_column(ForeignKey('service_orders.id'),unique=True)
    service_quote_id:Mapped[str]=mapped_column(ForeignKey('service_order_quotes.id'))
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    accepted_revision:Mapped[int]=mapped_column(Integer)
    snapshot:Mapped[dict]=mapped_column(JSON)
    consent:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
