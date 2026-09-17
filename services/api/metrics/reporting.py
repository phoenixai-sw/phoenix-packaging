from collections import defaultdict
from decimal import Decimal, InvalidOperation
from sqlalchemy import select, func
from ..billing.models import Payment, PaymentOrder, PaymentEvent
from ..billing.policy import aware
from ..feature_models import AiUnit, ProviderAttempt, Evidence
from ..models import Asset, Job, Project
from ..retention.deletion import available_asset_clause
from ..retention.models import StorageIntent
from .models import MetricEvent, Acquisition, ActivitySlice, CostEntry
from .schemas import CostDTO
from ..errors import APIError

REPORT_ROWS = 10000


def bounded(db, query):
    rows = list(db.execute(query.limit(REPORT_ROWS + 1)))
    if len(rows) > REPORT_ROWS:
        raise APIError(422, "METRICS_REPORT_TOO_LARGE", "집계 자료가 많습니다. 조회 기간을 줄이거나 운영 보고서 추출을 요청해 주세요.")
    return rows


def money(value):
    try:
        result=Decimal(str(value))
        return result if result.is_finite() and result>=0 else None
    except (InvalidOperation,TypeError,ValueError): return None


def cost_payload(row):
    return {name:getattr(row,name) for name in CostDTO.model_fields}


def overview(db,start,end):
    event_provider=MetricEvent.properties["provider"].as_string()
    event_source=MetricEvent.properties["record_source"].as_string()
    counts=list(db.execute(select(MetricEvent.name,event_provider,event_source,func.count()).where(MetricEvent.occurred_at>=start,
        MetricEvent.occurred_at<end).group_by(MetricEvent.name,event_provider,event_source)))
    acquisitions=[row[0] for row in bounded(db,select(Acquisition).where(Acquisition.created_at>=start,Acquisition.created_at<end))]
    acquisition_ids=[row.tenant_id for row in acquisitions]
    trial_tenants=set(db.scalars(select(MetricEvent.tenant_id).where(MetricEvent.name=="trial_granted",MetricEvent.occurred_at<end,MetricEvent.tenant_id.in_(acquisition_ids))))
    # Earliest verified subscription payment across ALL history. A renewal,
    # upgrade or top-up can never create a second acquired customer.
    payment_rows=bounded(db,select(Payment,PaymentOrder.kind).join(PaymentOrder,PaymentOrder.id==Payment.order_id).where(Payment.approved_at>=start,Payment.approved_at<end))
    approved_states=["DONE","CANCELED","PARTIAL_CANCELED"]
    first_query=select(Payment.tenant_id.label("tenant_id"),func.min(Payment.approved_at).label("at")).join(PaymentOrder,PaymentOrder.id==Payment.order_id).where(Payment.provider=="toss_live",Payment.status.in_(approved_states),PaymentOrder.kind=="subscription").group_by(Payment.tenant_id).subquery()
    first_paid={tenant:aware(at) for tenant,at in db.execute(select(first_query.c.tenant_id,first_query.c.at).where(first_query.c.tenant_id.in_(acquisition_ids)))}
    payment_counts={"subscription_payments":0,"renewals":0,"topups":0,"upgrades":0,"first_subscription_customers":db.scalar(select(func.count()).select_from(first_query).where(first_query.c.at>=start,first_query.c.at<end)),"mock_payments_excluded":0,"test_payments_excluded":0,"unrecognized_providers_excluded":0}
    period_payments=[]
    for payment,kind in payment_rows:
        at=aware(payment.approved_at)
        if payment.provider!="toss_live":
            key={"mock":"mock_payments_excluded","toss_test":"test_payments_excluded"}.get(payment.provider,"unrecognized_providers_excluded")
            payment_counts[key]+=1
            continue
        if payment.status not in approved_states: continue
        if start<=at<end:
            period_payments.append(payment)
            name={"subscription":"subscription_payments","renewal":"renewals","topup":"topups","upgrade":"upgrades"}.get(kind)
            if name: payment_counts[name]+=1
    payment_counts["refund_events"]=db.scalar(select(func.count()).select_from(PaymentEvent).join(Payment,Payment.id==PaymentEvent.payment_id).where(Payment.provider=="toss_live",PaymentEvent.kind=="REFUND",PaymentEvent.created_at>=start,PaymentEvent.created_at<end))
    cohorts=defaultdict(list)
    for row in acquisitions: cohorts[(row.channel,row.utm_source,row.utm_medium,row.utm_campaign)].append(row.tenant_id)
    cohort_rows=[]
    for (channel,source,medium,campaign),tenants in sorted(cohorts.items(),key=lambda entry:str(entry[0])):
        paid=sum(t in first_paid and first_paid[t]<end for t in tenants)
        cohort_rows.append({"channel":channel,"utm_source":source,"utm_medium":medium,"campaign_hash":campaign,
            "signups":len(tenants),"trial_tenants":sum(t in trial_tenants for t in tenants),
            "first_subscription_customers":paid,"conversion_rate":paid/len(tenants) if tenants else None})
    projects=list(db.scalars(select(Project).where(Project.created_at>=start,Project.created_at<end).order_by(Project.created_at.desc()).limit(100)))
    timings=[]
    for project in projects:
        first=db.scalar(select(func.min(Job.updated_at)).where(Job.project_id==project.id,Job.kind=="production_export",Job.status=="succeeded",Job.updated_at<end,Job.result["review_only"].as_boolean().is_(False)))
        first=aware(first) if first else None
        until=min(first,end) if first else end
        seconds,count=db.execute(select(func.coalesce(func.sum(ActivitySlice.seconds),0),func.count()).where(
            ActivitySlice.project_id==project.id,ActivitySlice.created_at<until)).one()
        timings.append({"project_id":project.id,"created_at":project.created_at,"first_production_at":first,
            "elapsed_seconds":max(0,int((first-aware(project.created_at)).total_seconds())) if first else None,
            "estimated_active_edit_seconds":seconds,"activity_observed":count>0})
    attempts=bounded(db,select(ProviderAttempt,AiUnit.job_id).join(AiUnit,AiUnit.id==ProviderAttempt.unit_id)
        .where(ProviderAttempt.created_at>=start,ProviderAttempt.created_at<end).order_by(ProviderAttempt.created_at.desc()))
    attempt_rows=[]; totals=defaultdict(lambda:{"sum":Decimal(0),"count":0})
    def total(category,currency,basis,value):
        item=totals[(category,currency,basis)];item["count"]+=1
        if value is not None:item["sum"]+=value
    for attempt,job_id in attempts:
        image_settings=(attempt.usage or {}).get("image_settings") or {}
        value=money(attempt.cost_usd)
        basis="fixture" if attempt.provider=="fixture" else "not_called" if attempt.status=="canceled_before_provider" else "unknown" if value is None else "estimate" if attempt.cost_is_estimate else "actual"
        if basis=="not_called": value=Decimal(0)
        if basis not in {"fixture","not_called"}:total("provider","USD",basis,value)
        attempt_rows.append({"attempt_id":attempt.id,"tenant_id":attempt.tenant_id,"job_id":job_id,"model":attempt.model,
            "requested_quality":image_settings.get("quality"),"actual_quality":image_settings.get("actual_quality"),"output_size":image_settings.get("output_size"),
            "provider":attempt.provider,"status":attempt.status,"basis":basis,"cost_usd":value,"error_code":attempt.error_code,"created_at":attempt.created_at})
    superseded=select(CostEntry.supersedes_id).where(CostEntry.supersedes_id.is_not(None))
    costs=[row[0] for row in bounded(db,select(CostEntry).where(CostEntry.id.not_in(superseded),CostEntry.occurred_at>=start,CostEntry.occurred_at<end).order_by(CostEntry.created_at.desc()))]
    for cost in costs:total(cost.category,cost.currency,cost.basis,cost.amount)
    fee_ids=set(db.scalars(select(CostEntry.payment_id).where(CostEntry.id.not_in(superseded),CostEntry.category=="payment_fee",CostEntry.basis.in_(["actual","estimate"]),CostEntry.payment_id.in_([p.id for p in period_payments]))))
    asset_rows=bounded(db,select(Asset.storage_key,Asset.byte_size).where(available_asset_clause(include_deleting=True)))
    objects=dict(asset_rows)
    objects.update(dict(bounded(db,select(Evidence.storage_key,Evidence.byte_size))))
    from ..database import Base
    font_table=Base.metadata.tables.get("font_assets")
    if font_table is not None:objects.update(dict(bounded(db,select(font_table.c.storage_key,font_table.c.byte_size))))
    for (job_result,) in bounded(db,select(Job.result).where(Job.status=="succeeded",Job.kind.in_(["review_export","production_export","editable_export"]))):
        result=job_result or {}
        # A deletion seal precedes storage I/O. Holds, backup pins or transient
        # failures can leave a sealed file physically retained for a long time.
        if result.get("storage_key") and (result.get("_retention") or {}).get("state") != "deleted":
            size=result.get("byte_size")
            if size is None:size=db.scalar(select(StorageIntent.byte_size).where(StorageIntent.storage_key==result["storage_key"]))
            objects[result["storage_key"]]=size
    output_count=db.scalar(select(func.count()).select_from(Job).where(Job.created_at>=start,Job.created_at<end,Job.kind.in_(["review_export","production_export","editable_export"])))
    return {"start":start,"end":end,"timezone":"UTC","event_counts":[{"name":name,"provider":provider,"record_source":source,"count":count} for name,provider,source,count in counts],
        "cohorts":cohort_rows,"payments":payment_counts,"timings":timings,"provider_attempts":attempt_rows[:100],
        "provider_attempt_count":len(attempts),"costs":[cost_payload(row) for row in costs[:100]],
        "totals":[{"category":category,"currency":currency,"basis":basis,"amount":None if basis=="unknown" else item["sum"],"entries":item["count"]} for (category,currency,basis),item in sorted(totals.items())],
        "storage_observed_bytes":sum(value for value in objects.values() if value is not None),"storage_observed_objects":len(objects),
        "output_jobs":output_count,"reported_support_minutes":sum(row.support_minutes or 0 for row in costs),
        "payment_fees_unknown":sum(p.id not in fee_ids for p in period_payments),
        "missing_cost_categories":[kind for kind in ("storage","output","support","payment_fee") if not any(row.category==kind and row.basis!="unknown" for row in costs)],
        "limits":{"display_projects":100,"display_attempts":100,"display_costs":100,"max_rows_per_source":REPORT_ROWS,"storage_objects_without_size":sum(size is None for size in objects.values())},
        "limitations":["활동 시간은 활성 편집창의 입력 신호를 서버 시각으로 제한한 추정치이며 실제 노동시간이 아닙니다.",
            "실제/추정/불명 비용과 통화를 합산하지 않습니다. 제공자 비용은 요청별 기록이며 환불 크레딧과 별개입니다.",
            "저장 크기는 현재 확인 가능한 객체 합계이며 저장 서비스 청구액이 아닙니다. 수수료·출력·지원 비용은 운영 기록이 없으면 불명입니다.",
            "기존 가입의 기록되지 않은 UTM과 편집 활동은 소급 복원하지 않습니다. 캠페인은 원문 대신 해시로 그룹화합니다.",
            "유입 전환은 Toss 실결제 최초 구독 승인 기준입니다. 모의·테스트 결제는 제외하며, 후속 환불은 별도 건수로 표시하고 최초 전환을 취소하지 않습니다."]}
