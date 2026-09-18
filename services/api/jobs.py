"""Durable review export consumer, callable from Celery or a secured job endpoint."""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from .retention.storage_lifecycle import record_write_intent, mark_published

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
            active_tenants = select(Job.tenant_id).where(Job.status == "running", Job.kind.in_(["review_export", "production_export", "editable_export"]))
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
                        if asset is None or (snapshot.get('print_output') and asset.workspace_id not in (None,snapshot.get('workspace_id'))):
                            raise ValueError("Asset is not accessible in this tenant")
                        path = temp / f"asset-{asset.id}"
                        raw=storage.get(asset.storage_key)
                        if snapshot.get('print_output'):
                            from hashlib import sha256
                            expected=next((a for a in snapshot.get('print_assets',[]) if a['id']==asset.id),None)
                            if not expected or sha256(raw).hexdigest()!=expected['sha256']:
                                from .errors import APIError
                                raise APIError(422,'PRINT_SOURCE_HASH_MISMATCH','동결한 이미지와 원본 파일이 다릅니다.')
                        path.write_bytes(raw)
                        return path

                def asset_metadata(asset_id):
                    if snapshot.get('print_output'):
                        expected=next((a for a in snapshot.get('print_assets',[]) if a['id']==str(asset_id)),None)
                        if expected is None:raise ValueError('Asset quality snapshot missing')
                        return expected['quality_metadata']
                    with session_factory() as asset_db:
                        asset = asset_db.scalar(select(Asset).where(Asset.id == str(asset_id), Asset.tenant_id == tenant_id))
                        if asset is None:
                            raise ValueError("Asset is not accessible in this tenant")
                        return asset.metadata_json
                asset_resolver.metadata = asset_metadata
                from .font_assets.service import attach_font_resolver
                attach_font_resolver(asset_resolver,session_factory,storage,tenant_id,snapshot)

                print_mode = snapshot.get("print_output", {}).get("mode")
                engine_test = print_mode in ("test", "print_request")
                if engine_test:
                    from .print_engine import resolve_print_icc,check_test_access
                    from .exporters.print_pdf import render_print_artifacts
                    from zipfile import ZipFile,ZIP_DEFLATED
                    with session_factory() as db:
                        check_test_access(db,tenant_id,snapshot)
                        icc=resolve_print_icc(db,storage,snapshot["print_output"])
                    manifest=render_print_artifacts(snapshot,temp/"engine",snapshot["print_output"]["requirements"],icc,asset_resolver,test_mode=print_mode=="test",print_request=print_mode=="print_request")
                    output=temp/"engine-test.zip"
                    with ZipFile(output,"w",ZIP_DEFLATED) as archive:
                        for path in sorted((temp/"engine").iterdir()):archive.write(path,path.name)
                    if output.stat().st_size>200*1024*1024:
                        from .errors import APIError
                        raise APIError(413,"PRINT_BUNDLE_LIMIT","시험 출력 묶음이 200MiB를 넘습니다.")
                else:
                    output = temp / "review.pdf"
                    manifest = export_review_pdf(snapshot, output, asset_resolver=asset_resolver)
                # Each lease writes its own object: an expired worker cannot
                # overwrite the output of the worker that recovered its job.
                extension="zip" if engine_test else "pdf"
                key = f"{tenant_id}/exports/{job_id}/{lease_id}.{extension}"
                raw = output.read_bytes()
                record_write_intent(session_factory, tenant_id, job_id, lease_id, key, raw)
                storage.put(key, raw, "application/zip" if engine_test else "application/pdf")
                if engine_test:
                    from hashlib import sha256
                    if sha256(storage.get(key)).digest()!=sha256(raw).digest():
                        from .errors import APIError
                        raise APIError(503,"STORAGE_VERIFICATION_FAILED","시험 출력 묶음 보관을 확인하지 못했습니다.")
            with session_factory() as db:
                extra={}
                if engine_test:
                    check_test_access(db,tenant_id,snapshot,lock=True)
                    request_mode=print_mode=="print_request"
                    extra={"format":"print_request_zip" if request_mode else "print_engine_zip","media_type":"application/zip",
                           "filename":f"phoenix-print-request-{job_id}.zip" if request_mode else f"phoenix-print-engine-test-{job_id}.zip","sha256":sha256(raw).hexdigest()}
                won = db.execute(update(Job).where(Job.id == job_id, Job.status == "running", Job.lease_id == lease_id).values(status="succeeded", result={"storage_key": key, "manifest": manifest, "review_only": True, "credits_charged": 0,**extra}, error=None, updated_at=utcnow()))
                if won.rowcount == 1:
                    mark_published(db, key)
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
