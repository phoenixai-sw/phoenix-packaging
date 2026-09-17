from calendar import monthrange
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from functools import lru_cache
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from ..errors import APIError
from copy import deepcopy
from ..operations.models import PolicyVersion,ActivePolicy

SEOUL = ZoneInfo("Asia/Seoul")


@lru_cache(maxsize=1)
def seed_pricing():
    path = Path(__file__).resolve().parents[3] / "config" / "pricing.seed.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["actions"] = {"editor.manual": 0, "preview.all_faces": 0, **data["actions"]}
    return data


def pricing(db=None):
    if db is not None:
        from ..operations.service import active_version
        row=active_version(db,'pricing')
        if row:return {**deepcopy(row.payload),'version':row.version}
    return deepcopy(seed_pricing())


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def add_months(value: datetime, months=1, anchor_day=None):
    local = aware(value).astimezone(SEOUL)
    index = local.year * 12 + local.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    day = min(anchor_day or local.day, monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day).astimezone(timezone.utc)


def plan(plan_id, *, db=None, snapshot=None):
    value = next((item for item in (snapshot or pricing(db))["plans"] if item["id"] == plan_id), None)
    if value is None:
        raise APIError(422, "UNKNOWN_PLAN", "요금제를 확인해 주세요.")
    return value


def prorate(old_plan, new_plan, start, end, now):
    seconds = Decimal(str(max(0, (aware(end) - aware(now)).total_seconds())))
    total = Decimal(str((aware(end) - aware(start)).total_seconds()))
    if total <= 0 or seconds <= 0:
        raise APIError(409, "PERIOD_ENDED", "새 결제 주기를 확인한 뒤 변경해 주세요.")
    fraction = min(Decimal(1), seconds / total)
    amount = (Decimal(new_plan["monthly_inc_vat"] - old_plan["monthly_inc_vat"]) * fraction).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    credits = (Decimal(new_plan["credits"] - old_plan["credits"]) * fraction).quantize(Decimal(1), rounding=ROUND_FLOOR)
    return int(amount), int(credits)
