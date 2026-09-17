"""Durable review export consumer, callable from Celery or a secured job endpoint."""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .database import utcnow
from .models import Asset, Job


def process_pending_jobs(session_factory, storage, limit=1) -> int:
    from .exporters import export_review_pdf

    processed, attempts = 0, 0
    target = max(1, min(limit, 10))
    while processed < target and attempts < 40:
        attempts += 1
        lease_id = str(uuid4())
        with session_factory() as db:
            # Crash recovery: a result is immutable and may safely be recreated at
            # the same private key. Normal requests never reset running work.
            db.execute(update(Job).where(Job.kind == "review_export", Job.status == "running", Job.updated_at < utcnow() - timedelta(minutes=15)).values(status="queued", lease_id=None, updated_at=utcnow()))
            active_tenants = select(Job.tenant_id).where(Job.status == "running", Job.kind.in_(["review_export", "production_export"]))
            candidate = db.scalar(select(Job).where(Job.kind == "review_export", Job.status == "queued", Job.tenant_id.not_in(active_tenants)).order_by(Job.created_at).limit(1).with_for_update(skip_locked=True))
            if candidate is None:
                db.commit()
                break
            # Compare-and-swap permits only one worker to claim the durable row.
            try:
                claimed = db.execute(update(Job).where(Job.id == candidate.id, Job.status == "queued").values(status="running", lease_id=lease_id, updated_at=utcnow()).execution_options(synchronize_session=False))
                db.commit()
            except IntegrityError:
                # Another worker claimed a different export of this tenant.
                db.rollback()
                continue
            if claimed.rowcount != 1:
                continue
            job_id, tenant_id, snapshot = candidate.id, candidate.tenant_id, candidate.snapshot
        try:
            with TemporaryDirectory(prefix="phoenix-export-") as tmp:
                temp = Path(tmp)

                def asset_resolver(asset_id):
                    with session_factory() as asset_db:
                        asset = asset_db.scalar(select(Asset).where(Asset.id == str(asset_id), Asset.tenant_id == tenant_id))
                        if asset is None:
                            raise ValueError("Asset is not accessible in this tenant")
                        path = temp / f"asset-{asset.id}"
                        path.write_bytes(storage.get(asset.storage_key))
                        return path

                def asset_metadata(asset_id):
                    with session_factory() as asset_db:
                        asset = asset_db.scalar(select(Asset).where(Asset.id == str(asset_id), Asset.tenant_id == tenant_id))
                        if asset is None:
                            raise ValueError("Asset is not accessible in this tenant")
                        return asset.metadata_json
                asset_resolver.metadata = asset_metadata

                output = temp / "review.pdf"
                manifest = export_review_pdf(snapshot, output, asset_resolver=asset_resolver)
                # Each lease writes its own object: an expired worker cannot
                # overwrite the output of the worker that recovered its job.
                key = f"{tenant_id}/exports/{job_id}/{lease_id}.pdf"
                storage.put(key, output.read_bytes(), "application/pdf")
            with session_factory() as db:
                db.execute(update(Job).where(Job.id == job_id, Job.status == "running", Job.lease_id == lease_id).values(status="succeeded", result={"storage_key": key, "manifest": manifest, "review_only": True, "credits_charged": 0}, error=None, updated_at=utcnow()))
                db.commit()
        except Exception as exc:
            # Error descriptions are selected, never raw provider URLs or data.
            message = "검토 파일 생성에 실패했습니다. 글꼴과 이미지 상태를 확인해 주세요."
            if hasattr(exc, "message") and type(exc).__module__.startswith("services.api"):
                message = str(exc.message)[:500]
            with session_factory() as db:
                db.execute(update(Job).where(Job.id == job_id, Job.status == "running", Job.lease_id == lease_id).values(status="failed", error=message, updated_at=utcnow()))
                db.commit()
        processed += 1
    return processed
