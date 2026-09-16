"""Bounded post-publication AI integrity checks; never regenerate paid results."""
from datetime import datetime, timedelta
from hashlib import sha256
import re

import httpx
from sqlalchemy import select, or_

from .ai_jobs import lock_tenant_work, summarize_job
from .billing.policy import aware
from .billing.service import compensate_unit
from .database import utcnow
from .errors import APIError
from .feature_models import AiUnit, AuditEvent
from .models import Asset, Job

PROBE_INTERVAL = timedelta(minutes=5)
HEALTHY_INTERVAL = timedelta(hours=24)
MAX_IMAGE_BYTES = 25 * 1024 * 1024


def ensure_asset_available(asset):
    if asset.metadata_json.get("integrity_status") == "unavailable_compensated":
        raise APIError(410, "ASSET_UNAVAILABLE_COMPENSATED", "이미지 파일의 소실 또는 손상이 확인되어 크레딧을 복원했습니다. 새 이미지는 자동 생성되지 않습니다.")


def probe_asset(storage, key, expected_hash, expected_size):
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        return "uncertain", "missing_integrity_baseline"
    try:
        content = storage.get_limited(key, MAX_IMAGE_BYTES) if hasattr(storage, "get_limited") else storage.get(key)
    except FileNotFoundError:
        return "missing", "object_absent"
    except httpx.HTTPStatusError as exc:
        # Supabase documents that even NoSuchKey can mask permission failures.
        # Require a separate privileged healthy-bucket/object-list confirmation.
        try:
            body = exc.response.json()
            missing = isinstance(body, dict) and (body.get("code") == "NoSuchKey" or str(body.get("message", "")).lower() == "object not found")
            if exc.response.status_code in {400, 404} and missing and hasattr(storage, "confirm_missing") and storage.confirm_missing(key):
                return "missing", "object_absent"
        except Exception:
            pass
        return "uncertain", "storage_http_error"
    except ValueError as exc:
        return ("corrupt", "oversize") if str(exc) == "Asset exceeds limit" else ("uncertain", "storage_validation_error")
    except (httpx.HTTPError, OSError):
        return "uncertain", "storage_connection_error"
    digest = sha256(content).hexdigest()
    if len(content) != expected_size or digest != expected_hash:
        return "corrupt", digest
    return "healthy", digest


def reconcile_ai_assets(session_factory, storage, *, now=None, limit=2):
    """Rotate oldest checked successful units, at most 2 by default per cron.

    A first definitive failure only records suspicion. Compensation requires a
    second matching definitive observation at least five minutes later. A good
    read or any transient/ambiguous failure clears the confirmation sequence.
    """
    now = aware(now or utcnow())
    cutoff = now - PROBE_INTERVAL
    with session_factory() as db:
        next_check = AiUnit.result_metadata["integrity"]["next_check_at"].as_string()
        candidates = list(db.execute(select(AiUnit.id, AiUnit.tenant_id).where(AiUnit.status == "succeeded", AiUnit.updated_at <= cutoff, or_(next_check.is_(None), next_check <= now.isoformat())).order_by(AiUnit.updated_at, AiUnit.id).limit(max(1, min(limit, 10)))))
    checked = 0
    for unit_id, tenant_id in candidates:
        with session_factory() as db:
            unit = db.get(AiUnit, unit_id)
            asset = db.get(Asset, unit.asset_id)
            if unit.status != "succeeded" or asset is None or asset.tenant_id != tenant_id:
                continue
            observed_at = aware(unit.updated_at)
            identity = (asset.id, asset.storage_key, asset.metadata_json.get("sha256"), asset.byte_size)
        state, signature = probe_asset(storage, identity[1], identity[2], identity[3])
        with session_factory() as db:
            lock_tenant_work(db, tenant_id)
            unit = db.scalar(select(AiUnit).where(AiUnit.id == unit_id).with_for_update())
            asset = db.scalar(select(Asset).where(Asset.id == identity[0], Asset.tenant_id == tenant_id).with_for_update())
            # Concurrent check/revocation cannot reuse an earlier observation or
            # compensate a different object that replaced the checked identity.
            if unit.status != "succeeded" or aware(unit.updated_at) != observed_at or asset is None or (asset.id, asset.storage_key, asset.metadata_json.get("sha256"), asset.byte_size) != identity:
                continue
            old = unit.result_metadata.get("integrity", {})
            current = {"state": state, "signature": signature, "checked_at": now.isoformat(), "next_check_at": (now + (HEALTHY_INTERVAL if state == "healthy" else PROBE_INTERVAL)).isoformat()}
            if state in {"missing", "corrupt"}:
                prior = old.get("first_observed_at")
                previous = aware(datetime.fromisoformat(prior)) if prior else now
                matches = old.get("state") == state and old.get("signature") == signature
                current["first_observed_at"] = previous.isoformat() if matches else now.isoformat()
                if matches and previous <= cutoff:
                    reason = "완료된 AI 이미지 소실 확인" if state == "missing" else "완료된 AI 이미지 무결성 손상 확인"
                    compensate_unit(db, tenant_id, unit.reservation_id, unit.unit_index, reason=reason, now=now)
                    unit.status, unit.error_code = "compensated", "ASSET_MISSING" if state == "missing" else "ASSET_CHECKSUM_MISMATCH"
                    current["state"] = "compensated"
                    asset.metadata_json = {**asset.metadata_json, "integrity_status": "unavailable_compensated", "integrity_confirmed_at": now.isoformat()}
                    db.add(AuditEvent(tenant_id=tenant_id, action="asset_loss_compensated", entity_id=asset.id, details={"job_id": unit.job_id, "unit_index": unit.unit_index, "reservation_id": unit.reservation_id, "reason": state}, created_at=now))
                elif not matches:
                    db.add(AuditEvent(tenant_id=tenant_id, action="asset_integrity_suspect", entity_id=asset.id, details={"job_id": unit.job_id, "reason": state}, created_at=now))
            elif old.get("state") in {"missing", "corrupt"}:
                db.add(AuditEvent(tenant_id=tenant_id, action="asset_integrity_recovered" if state == "healthy" else "asset_integrity_uncertain", entity_id=asset.id, details={"job_id": unit.job_id}, created_at=now))
            unit.result_metadata = {**unit.result_metadata, "integrity": current}
            unit.updated_at = now
            if unit.status == "compensated":
                summarize_job(db, db.get(Job, unit.job_id))
            db.commit()
            checked += 1
    return checked
