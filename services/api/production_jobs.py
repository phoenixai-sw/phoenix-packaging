"""Durable lease-protected production bundle worker with atomic entitlement capture."""
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile,ZIP_DEFLATED
from sqlalchemy import select,update
from sqlalchemy.exc import IntegrityError
from .database import utcnow
from .models import Asset,Job,Project,User
from .feature_models import RegistryVersion,AuditEvent,Membership
from .business import workspace_access
from .errors import APIError
from .registry import approved_conditions
from .exporters import export_production_bundle,preflight_project
from .billing.service import canonical_hash,capture_unit,release
from .billing.payments import enforce_membership_entitlement


def _current_conditions(db,snapshot,tenant_id,*,lock=False):
    project=db.scalar(select(Project).where(Project.id==snapshot["id"],Project.tenant_id==tenant_id).with_for_update())
    if not project or project.base_revision!=snapshot["base_revision"]:
        raise APIError(409,"REVISION_CHANGED","출력 대기 중 프로젝트가 바뀌었습니다. 최신 리비전으로 다시 요청해 주세요.")
    actor=db.get(User,snapshot["actor_id"])
    if actor is None or not actor.is_active:raise APIError(403,"ACTOR_UNAVAILABLE","요청한 계정의 접근 권한이 변경되었습니다.")
    if actor.tenant_id!=tenant_id:
        membership=db.scalar(select(Membership).where(Membership.user_id==actor.id,Membership.tenant_id==tenant_id,Membership.is_active.is_(True)))
        if membership is None:raise APIError(403,"MEMBERSHIP_UNAVAILABLE","요청한 팀 접근 권한이 변경되었습니다.")
        actor=SimpleNamespace(id=actor.id,role=membership.role,tenant_id=tenant_id)
    if actor.role not in {"owner","editor"} or not workspace_access(db,actor,project.workspace_id):
        raise APIError(403,"ROLE_FORBIDDEN","제작 출력 권한이 변경되었습니다.")
    enforce_membership_entitlement(db,actor)
    if lock:
        identities=[snapshot.get("template_version_id"),snapshot.get("print_profile_version_id")]
        list(db.scalars(select(RegistryVersion).where(RegistryVersion.id.in_([i for i in identities if i])).order_by(RegistryVersion.id).with_for_update().execution_options(populate_existing=True)))
    # Version/material fields come from the frozen job, never a later scene.
    frozen=SimpleNamespace(**snapshot)
    conditions=approved_conditions(db,frozen,snapshot["revision_id"],snapshot["reviewed_face_ids"])
    if canonical_hash(conditions)!=snapshot["conditions_hash"]:
        raise APIError(409,"APPROVAL_CHANGED","제조사 승인 조건이 변경되거나 철회되었습니다.")
    return conditions


def process_production_jobs(session_factory,storage,settings,limit=1):
    processed=0
    for _ in range(max(1,min(limit,3))):
        lease=str(uuid4());now=utcnow();claimed=None
        with session_factory() as db:
            overdue=list(db.scalars(select(Job).where(Job.kind=="production_export",Job.status.in_(["queued","running"]),Job.created_at<now-timedelta(minutes=35)).with_for_update(skip_locked=True)))
            for job in overdue:
                release(db,job.tenant_id,job.snapshot["reservation_id"],reason="production_queue_timeout")
                job.status="failed";job.error="출력 대기 시간이 지나 예약을 복원했습니다. 새 견적으로 요청해 주세요.";job.lease_id=None;job.updated_at=now
            db.execute(update(Job).where(Job.kind=="production_export",Job.status=="running",Job.updated_at<now-timedelta(minutes=15)).values(status="queued",lease_id=None,updated_at=now))
            db.commit()
        for attempt in range(20):
            with session_factory() as db:
                active=select(Job.tenant_id).where(Job.status=="running",Job.kind.in_(["review_export","production_export"]))
                candidate=db.scalar(select(Job).where(Job.kind=="production_export",Job.status=="queued",Job.tenant_id.not_in(active)).order_by(Job.created_at).limit(1).with_for_update(skip_locked=True))
                if candidate is None:break
                try:
                    won=db.execute(update(Job).where(Job.id==candidate.id,Job.status=="queued").values(status="running",lease_id=lease,updated_at=now).execution_options(synchronize_session=False))
                    if won.rowcount!=1:db.rollback();continue
                    claimed=(candidate.id,candidate.tenant_id,dict(candidate.snapshot));db.commit();break
                except IntegrityError:db.rollback()
        if claimed is None:break
        job_id,tenant_id,snapshot=claimed
        try:
            if not settings.enable_production_export:raise APIError(503,"PRODUCTION_DISABLED","제작 출력이 비활성화되어 예약을 복원했습니다.")
            with session_factory() as db:conditions=_current_conditions(db,snapshot,tenant_id)
            with TemporaryDirectory(prefix="phoenix-production-") as folder:
                temporary=Path(folder);assets={}
                def resolver(identity):
                    if identity in assets:return assets[identity]
                    with session_factory() as db:
                        asset=db.scalar(select(Asset).where(Asset.id==str(identity),Asset.tenant_id==tenant_id))
                        if not asset or asset.workspace_id not in (None,snapshot.get("workspace_id")):
                            raise APIError(404,"ASSET_UNAVAILABLE","출력 자산의 접근 권한을 확인해 주세요.")
                        path=temporary/f"asset-{asset.id}";path.write_bytes(storage.get(asset.storage_key));assets[identity]=path;return path
                def asset_metadata(identity):
                    with session_factory() as db:
                        asset=db.scalar(select(Asset).where(Asset.id==str(identity),Asset.tenant_id==tenant_id))
                        if not asset or asset.workspace_id not in (None,snapshot.get("workspace_id")):
                            raise APIError(404,"ASSET_UNAVAILABLE","출력 자산의 접근 권한을 확인해 주세요.")
                        return asset.metadata_json
                resolver.metadata=asset_metadata
                def recheck():
                    with session_factory() as db:return _current_conditions(db,snapshot,tenant_id)
                bundle=temporary/"bundle";manifest=export_production_bundle(snapshot,bundle,conditions,resolver,approval_recheck=recheck)
                archive=temporary/"production.zip"
                with ZipFile(archive,"w",ZIP_DEFLATED) as zipfile:
                    for path in sorted(bundle.iterdir()):zipfile.write(path,path.name)
                raw=archive.read_bytes();digest=sha256(raw).hexdigest();key=f"{tenant_id}/exports/{job_id}/{lease}.zip"
                storage.put(key,raw,"application/zip")
                if sha256(storage.get(key)).hexdigest()!=digest:raise APIError(503,"STORAGE_VERIFICATION_FAILED","제작 묶음 보관을 확인하지 못했습니다.")
                with session_factory() as db:
                    # Hold registry row locks through publication and credit capture.
                    latest=_current_conditions(db,snapshot,tenant_id,lock=True)
                    if not preflight_project(snapshot,latest,resolver)["production_allowed"]:
                        raise APIError(422,"PRODUCTION_PREFLIGHT_CHANGED","최종 제작 검수가 변경되었습니다.")
                    won=db.execute(update(Job).where(Job.id==job_id,Job.status=="running",Job.lease_id==lease).values(updated_at=utcnow()).execution_options(synchronize_session=False))
                    if won.rowcount!=1:db.rollback();continue
                    job=db.get(Job,job_id)
                    if not capture_unit(db,tenant_id,snapshot["reservation_id"],0,job_id=job_id):
                        job.status="failed";job.error="예약 유효시간이 지나 결과를 공개하지 않았습니다. 새 견적으로 요청해 주세요."
                    else:
                        job.status="succeeded";job.error=None;job.result={"storage_key":key,"media_type":"application/zip","filename":f"phoenix-production-{job_id}.zip","sha256":digest,"manifest":manifest,"review_only":False,"credits_charged":snapshot["unit_cost"],"manufacturer_intake_status":"not_submitted"}
                        db.add(AuditEvent(tenant_id=tenant_id,actor_id=snapshot["actor_id"],action="production_succeeded",entity_id=job_id,details={"revision_id":snapshot["revision_id"],"sha256":digest}))
                    job.updated_at=utcnow();db.commit()
        except Exception as exc:
            message="제작 묶음을 준비하지 못해 예약을 복원했습니다. 검수 후 새 견적으로 다시 요청해 주세요."
            if hasattr(exc,"message") and type(exc).__module__.startswith("services.api"):message=str(exc.message)[:500]
            with session_factory() as db:
                won=db.execute(update(Job).where(Job.id==job_id,Job.status=="running",Job.lease_id==lease).values(status="failed",error=message,updated_at=utcnow()).execution_options(synchronize_session=False))
                if won.rowcount==1:
                    release(db,tenant_id,snapshot["reservation_id"],reason=getattr(exc,"code","production_failed"))
                    db.add(AuditEvent(tenant_id=tenant_id,actor_id=snapshot["actor_id"],action="production_failed",entity_id=job_id,details={"code":getattr(exc,"code","production_failed")}))
                db.commit()
        processed+=1
    return processed
