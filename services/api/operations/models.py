from datetime import datetime
from sqlalchemy import JSON,CheckConstraint,DateTime,ForeignKey,Integer,String,Text,UniqueConstraint,event,inspect
from sqlalchemy.orm import Mapped,mapped_column
from ..database import Base,new_id,utcnow


class PolicyVersion(Base):
    __tablename__='operation_policy_versions'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=new_id)
    kind:Mapped[str]=mapped_column(String(20),index=True)
    version:Mapped[str]=mapped_column(String(100),unique=True)
    payload:Mapped[dict]=mapped_column(JSON)
    payload_hash:Mapped[str]=mapped_column(String(64))
    reason:Mapped[str]=mapped_column(Text)
    created_by:Mapped[str]=mapped_column(ForeignKey('users.id'))
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    published_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)


class ActivePolicy(Base):
    __tablename__='operation_active_policies'
    kind:Mapped[str]=mapped_column(String(20),primary_key=True)
    version_id:Mapped[str|None]=mapped_column(ForeignKey('operation_policy_versions.id'),nullable=True)
    revision:Mapped[int]=mapped_column(Integer,default=0)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class CreditCorrection(Base):
    __tablename__='credit_corrections'
    __table_args__=(UniqueConstraint('tenant_id','operation_key',name='uq_credit_correction_operation'),CheckConstraint('amount != 0',name='ck_credit_correction_amount'))
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=new_id)
    tenant_id:Mapped[str]=mapped_column(ForeignKey('tenants.id'),index=True)
    operation_key:Mapped[str]=mapped_column(String(160))
    request_hash:Mapped[str]=mapped_column(String(64))
    amount:Mapped[int]=mapped_column(Integer)
    bucket_id:Mapped[str]=mapped_column(ForeignKey('credit_buckets.id'))
    scope:Mapped[str]=mapped_column(String(30))
    reason:Mapped[str]=mapped_column(String(500))
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


@event.listens_for(PolicyVersion, 'before_update')
def immutable_policy(mapper, connection, target):
    state=inspect(target)
    if any(state.attrs[key].history.has_changes() for key in ('kind','version','payload','payload_hash','reason','created_by','created_at')):
        raise ValueError('Policy versions are immutable; create a new draft.')
    published=state.attrs.published_at.history
    if published.has_changes() and published.deleted and published.deleted[0] is not None:
        raise ValueError('A published version cannot be republished or unpublished.')


def immutable_record(mapper, connection, target):
    raise ValueError('Policy and credit correction history is append-only.')


event.listen(PolicyVersion,'before_delete',immutable_record)
event.listen(CreditCorrection,'before_update',immutable_record)
event.listen(CreditCorrection,'before_delete',immutable_record)
