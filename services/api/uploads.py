"""Quarantined direct uploads keep 20 MiB images out of the web proxy body."""
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,Request
from pydantic import BaseModel,ConfigDict,Field
from PIL import Image
from sqlalchemy import func,select,update
from .auth import require_auth
from .business import owned_record
from .database import utcnow
from .errors import APIError
from .models import Asset,Project,Tenant
from .feature_models import UploadSession
from .billing.policy import aware
from .storage import SupabaseStorage

MAX_BYTES=20*1024*1024
QUOTA_BYTES=200*1024*1024


class UploadBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    name:str=Field(min_length=1,max_length=160)
    content_type:Literal["image/png","image/jpeg","image/webp"]
    byte_size:int=Field(gt=0,le=MAX_BYTES)
    project_id:UUID | None=None


def install_upload_routes(app,db_session,asset_payload):
    router=APIRouter(prefix="/v1",tags=["uploads"])
    storage=app.state.storage
    def result(request,data): return {"data":data,"request_id":request.state.request_id}
    @router.post("/assets/uploads",status_code=201)
    def initiate(body:UploadBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True)
        if not isinstance(storage,SupabaseStorage): raise APIError(422,"DIRECT_UPLOAD_UNAVAILABLE","로컬에서는 파일을 직접 업로드해 주세요.")
        if body.project_id: owned_record(db,Project,body.project_id,user.tenant_id)
        # A conditional write serializes quota/session counts on SQLite too,
        # whose SELECT FOR UPDATE is intentionally ignored.
        db.execute(update(Tenant).where(Tenant.id==user.tenant_id).values(name=Tenant.name).execution_options(synchronize_session=False))
        now=utcnow()
        pending=list(db.scalars(select(UploadSession).where(UploadSession.tenant_id==user.tenant_id,UploadSession.status=="pending",UploadSession.expires_at>now)))
        count=db.scalar(select(func.count()).select_from(UploadSession).where(UploadSession.tenant_id==user.tenant_id,UploadSession.created_at>now-timedelta(hours=1)))
        if len(pending)>=5 or count>=30: raise APIError(429,"UPLOAD_RATE_LIMIT","진행 중인 업로드를 완료한 뒤 다시 시도해 주세요.")
        used=db.scalar(select(func.coalesce(func.sum(Asset.byte_size),0)).where(Asset.tenant_id==user.tenant_id))
        if used+sum(p.byte_size for p in pending)+body.byte_size>QUOTA_BYTES: raise APIError(422,"ASSET_QUOTA_EXCEEDED","작업 공간의 200MiB 저장 한도를 초과했습니다.")
        identity=str(uuid4());key=f"{user.tenant_id}/quarantine/{identity}"
        row=UploadSession(id=identity,tenant_id=user.tenant_id,user_id=user.id,project_id=str(body.project_id) if body.project_id else None,storage_key=key,name=Path(body.name).name,content_type=body.content_type,byte_size=body.byte_size,expires_at=now+timedelta(minutes=15));db.add(row)
        url=storage.signed_upload_url(key);db.commit()
        return result(request,{"id":identity,"upload_url":url,"method":"PUT","headers":{"Content-Type":body.content_type,"x-upsert":"false"},"expires_at":row.expires_at.isoformat(),"max_bytes":MAX_BYTES})

    @router.post("/assets/uploads/{identity}/complete")
    def complete(identity:UUID,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True)
        row=owned_record(db,UploadSession,identity,user.tenant_id)
        if row.user_id!=user.id: raise APIError(404,"NOT_FOUND","요청한 업로드를 찾을 수 없습니다.")
        project=owned_record(db,Project,row.project_id,user.tenant_id) if row.project_id else None
        db.execute(update(Tenant).where(Tenant.id==user.tenant_id).values(name=Tenant.name).execution_options(synchronize_session=False))
        db.refresh(row)
        if row.asset_id: return result(request,asset_payload(owned_record(db,Asset,row.asset_id,user.tenant_id)))
        if row.status!="pending" or aware(row.expires_at)<=utcnow(): raise APIError(409,"UPLOAD_EXPIRED","업로드 시간이 지났습니다. 다시 시작해 주세요.")
        try:
            raw=storage.get_limited(row.storage_key,MAX_BYTES)
            if len(raw)!=row.byte_size: raise ValueError()
            with Image.open(BytesIO(raw)) as image:
                width,height=image.size
                if Image.MIME.get(image.format)!=row.content_type or width*height>40000000 or width<1 or height<1: raise ValueError()
                image.verify()
            # Container CRC/headers can be valid while raster data is truncated.
            # Decode once before the quarantined bytes become a durable asset.
            with Image.open(BytesIO(raw)) as image:image.load()
        except Exception:
            row.status="rejected";db.commit()
            raise APIError(422,"ASSET_INVALID","파일 형식·크기 또는 업로드 완료 상태를 확인해 주세요.") from None
        used=db.scalar(select(func.coalesce(func.sum(Asset.byte_size),0)).where(Asset.tenant_id==user.tenant_id))
        if used+len(raw)>QUOTA_BYTES: raise APIError(422,"ASSET_QUOTA_EXCEEDED","작업 공간의 저장 한도를 초과했습니다.")
        asset_id=str(uuid4());key=f"{user.tenant_id}/assets/{asset_id}"
        storage.put(key,raw,row.content_type)
        asset=Asset(id=asset_id,tenant_id=user.tenant_id,workspace_id=project.workspace_id if project else None,storage_key=key,original_name=row.name,content_type=row.content_type,byte_size=len(raw),width_px=width,height_px=height,metadata_json={"sha256":sha256(raw).hexdigest()});db.add(asset);db.flush()
        row.status="completed";row.asset_id=asset_id;db.commit()
        return result(request,asset_payload(asset))
    app.include_router(router)


def cleanup_quarantine(sessions,storage):
    if not isinstance(storage,SupabaseStorage): return 0
    # Signed upload URLs remain usable for two hours. Cleanup only after that
    # provider lifetime, so a still-valid token cannot recreate an orphan.
    count=0
    with sessions() as db:
        rows=list(db.scalars(select(UploadSession).where(UploadSession.created_at<utcnow()-timedelta(hours=3),UploadSession.status!="cleaned").limit(20)))
        for row in rows:
            storage.delete(row.storage_key);row.status="cleaned";count+=1
        db.commit()
    return count
