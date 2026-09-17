"""Serialized estimated USD guard, separate from user credit accounting.

Unknown/uncertain attempts retain a conservative operator-configured allowance.
This is a pre-call estimate, not a provider-side guarantee of a final invoice cap.
"""
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .database import utcnow
from .feature_models import ProviderAttempt, ProviderBudget


def _money(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _cost(row, settings):
    if row.status == "canceled_before_provider":
        return Decimal(0), False
    actual = _money(row.cost_usd)
    if actual is not None:
        return actual, False
    allowance = _money((row.usage or {}).get("budget", {}).get("allowance_usd"))
    return allowance if allowance is not None else Decimal(str(settings.ai_request_allowance_usd)), True


def _utc(now):
    # SQLite returns naive datetimes for the same UTC values stored by Postgres.
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def _period(now):
    now = _utc(now)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start.replace(day=1), start + timedelta(days=1)


def _totals(db, settings, now):
    day, month, until = _period(now)
    day_cost, month_cost, known = Decimal(0), Decimal(0), Decimal(0)
    unknown, daily_calls = 0, 0
    for attempt in db.scalars(select(ProviderAttempt).where(ProviderAttempt.provider == "openai",
                                                           ProviderAttempt.created_at >= month,
                                                           ProviderAttempt.created_at < until)):
        value, pending = _cost(attempt, settings)
        month_cost += value
        created = attempt.created_at.replace(tzinfo=timezone.utc) if attempt.created_at.tzinfo is None else attempt.created_at
        if created >= day:
            day_cost += value
            daily_calls += 1
            if pending:
                unknown += 1
            else:
                known += value
    return day_cost, month_cost, known, unknown, daily_calls


def reserve_allowance(db, settings, now):
    """Return denial code or None; caller inserts the attempt before commit.

    A global row lock precedes reading attempts. The UPDATE provides the same
    serialization on SQLite as on PostgreSQL, including different tenants.
    """
    if settings.ai_provider != "openai":
        return None
    now = _utc(now)
    day = now.strftime("%Y-%m-%d")
    if db.get(ProviderBudget, day) is None:
        try:
            with db.begin_nested():
                db.add(ProviderBudget(day=day, requested_units=0))
                db.flush()
        except IntegrityError:
            pass
    db.execute(update(ProviderBudget).where(ProviderBudget.day == day).values(requested_units=ProviderBudget.requested_units))
    used = db.scalar(select(ProviderBudget.requested_units).where(ProviderBudget.day == day))
    if used >= settings.ai_daily_units:
        return "AI_DAILY_LIMIT"
    spent, _, _, _, _ = _totals(db, settings, now)
    if spent + Decimal(str(settings.ai_request_allowance_usd)) > Decimal(str(settings.ai_daily_cost_limit_usd)):
        return "AI_DAILY_COST_LIMIT"
    db.execute(update(ProviderBudget).where(ProviderBudget.day == day).values(requested_units=ProviderBudget.requested_units + 1))
    return None


def allowance_metadata(settings, now):
    return {"allowance_usd": settings.ai_request_allowance_usd, "utc_day": _utc(now).strftime("%Y-%m-%d"),
            "policy": "estimated-usd-guard-v1"}


def budget_summary(db, settings, now=None):
    now = _utc(now or utcnow())
    daily, monthly, known, unknown, calls = _totals(db, settings, now)
    limit = Decimal(str(settings.ai_daily_cost_limit_usd))
    monthly_alert = Decimal(str(settings.ai_monthly_budget_alert_usd))
    allowance = Decimal(str(settings.ai_request_allowance_usd))
    alerts = []
    if daily + allowance > limit:
        alerts.append({"code": "AI_DAILY_COST_LIMIT", "severity": "error", "message": "추정 일일 비용 한도로 새 AI 요청이 차단됩니다."})
    elif daily >= limit * Decimal("0.8"):
        alerts.append({"code": "AI_DAILY_COST_WARNING", "severity": "warning", "message": "추정 일일 API 비용이 한도의 80%에 도달했습니다."})
    if monthly >= monthly_alert:
        alerts.append({"code": "AI_MONTHLY_BUDGET_ALERT", "severity": "warning", "message": "이번 달 전체 API 비용이 운영 알림 기준에 도달했습니다."})
    return {"provider": settings.ai_provider, "enforced": settings.ai_provider == "openai", "timezone": "UTC",
            "date": now.strftime("%Y-%m-%d"), "day_limit_usd": float(limit), "per_request_allowance_usd": float(allowance),
            "day_guard_usd": float(daily), "day_recorded_cost_usd": float(known), "day_unknown_attempts": unknown,
            "day_attempts": calls, "month_guard_usd": float(monthly), "month_alert_usd": float(monthly_alert),
            "estimate_only": True, "alerts": alerts}
