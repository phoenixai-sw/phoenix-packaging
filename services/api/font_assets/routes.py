from datetime import timedelta
import json
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select, update
from ..auth import require_auth
from ..billing.policy import aware
from ..contracts.base import Envelope, ERROR_RESPONSES, binary_responses
from ..contracts.core import UploadTicket
from ..database import utcnow
from ..errors import APIError
from ..feature_models import AuditEvent
from ..models import Tenant
from ..storage import SupabaseStorage
from .models import FontAsset, FontUploadSession
from .schemas import Declaration, FontUploadBody, FontData, FontList, GlyphBody, GlyphReport
from .service import payload, owned_font, read_font
from .validation import inspect_font, glyph_report, MAX_BYTES

QUOTA_BYTES = 100*1024*1024


def lock_quota(db, tenant_id, size, *, pending_id=None):
    db.execute(update(Tenant).where(Tenant.id==tenant_id).values(name=Tenant.name).execution_options(synchronize_session=False))
    now = utcnow()
    pending = list(db.scalars(select(FontUploadSession).where(FontUploadSession.tenant_id==tenant_id,
        FontUploadSession.status=='pending',FontUploadSession.expires_at>now,FontUploadSession.id!=pending_id)))
    used = db.scalar(select(func.coalesce(func.sum(FontAsset.byte_size),0)).where(FontAsset.tenant_id==tenant_id))
    count = db.scalar(select(func.count()).select_from(FontAsset).where(FontAsset.tenant_id==tenant_id))
    if len(pending)>=5: raise APIError(429,'FONT_UPLOAD_BUSY','진행 중인 글꼴 업로드를 완료해 주세요.')
    if count+len(pending)>=200:raise APIError(422,'FONT_COUNT_LIMIT','팀 글꼴 보관은 최대 200개입니다.')
    if used+sum(row.byte_size for row in pending)+size>QUOTA_BYTES:
        raise APIError(422,'FONT_QUOTA_EXCEEDED','팀의 글꼴 보관 한도 100MiB를 초과했습니다.')


def publish(db, storage, user, raw, name, declaration):
    if not declaration.web_use_confirmed or not declaration.print_use_confirmed:
        raise APIError(422,'FONT_RIGHTS_REQUIRED','웹 사용·문서 편집 및 인쇄 포함 권한을 확인해 주세요.')
    info = inspect_font(raw)
    key_id = str(uuid4()); key = f'{user.tenant_id}/fonts/{key_id}.ttf'
    storage.put(key,raw,'font/ttf')
    rights = declaration.model_dump(exclude={'web_use_confirmed','print_use_confirmed'})
    row = FontAsset(id=key_id,tenant_id=user.tenant_id,created_by=user.id,storage_key=key,
                    original_name=Path(name).name[:160],**info,**rights)
    db.add(row); db.flush()
    db.add(AuditEvent(tenant_id=user.tenant_id,actor_id=user.id,action='font_registered',entity_id=row.id,
                     details={'sha256':row.sha256,'license_name':row.license_name,'rights_verification':'user_attested'}))
    return row


def install_font_routes(app, db_session):
    router = APIRouter(prefix='/v1/fonts',tags=['fonts'])
    storage = app.state.storage
    def result(request,data): return {'data':data,'request_id':request.state.request_id}

    @router.get('',response_model=Envelope[FontList],response_model_exclude_unset=True,responses=ERROR_RESPONSES)
    def fonts(request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db)
        return result(request,{'items':[payload(row) for row in db.scalars(select(FontAsset).where(FontAsset.tenant_id==user.tenant_id).order_by(FontAsset.created_at.desc()))],
            'direct_upload':isinstance(storage,SupabaseStorage),'max_bytes':MAX_BYTES if isinstance(storage,SupabaseStorage) else min(MAX_BYTES,app.state.settings.upload_limit)})

    @router.post('',status_code=201,response_model=Envelope[FontData],response_model_exclude_unset=True,responses=ERROR_RESPONSES)
    async def upload(request:Request,file:UploadFile=File(...),declaration:str=Form(...),db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True)
        if isinstance(storage,SupabaseStorage):raise APIError(422,'FONT_DIRECT_UPLOAD_REQUIRED','대용량 글꼴 직접 업로드를 사용해 주세요.')
        try: rights=Declaration.model_validate_json(declaration)
        except ValidationError:raise APIError(422,'FONT_DECLARATION_INVALID','글꼴 출처·권리 확인 항목을 입력해 주세요.') from None
        raw=await file.read(min(MAX_BYTES,app.state.settings.upload_limit)+1)
        if len(raw)>min(MAX_BYTES,app.state.settings.upload_limit):raise APIError(413,'FONT_TOO_LARGE','글꼴 파일 업로드 한도를 초과했습니다.')
        lock_quota(db,user.tenant_id,len(raw));row=publish(db,storage,user,raw,file.filename or 'font.ttf',rights)
        db.commit();return result(request,payload(row))

    @router.post('/uploads',status_code=201,response_model=Envelope[UploadTicket],response_model_exclude_unset=True,responses=ERROR_RESPONSES)
    def initiate(body:FontUploadBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True)
        if not isinstance(storage,SupabaseStorage):raise APIError(422,'DIRECT_UPLOAD_UNAVAILABLE','로컬에서는 파일을 직접 업로드해 주세요.')
        if not body.web_use_confirmed or not body.print_use_confirmed:raise APIError(422,'FONT_RIGHTS_REQUIRED','글꼴 사용 권한을 확인해 주세요.')
        lock_quota(db,user.tenant_id,body.byte_size)
        count=db.scalar(select(func.count()).select_from(FontUploadSession).where(FontUploadSession.tenant_id==user.tenant_id,FontUploadSession.created_at>utcnow()-timedelta(hours=1)))
        if count>=20:raise APIError(429,'FONT_UPLOAD_RATE_LIMIT','글꼴 업로드 요청이 많습니다. 잠시 후 다시 시도해 주세요.')
        identity=str(uuid4());key=f'{user.tenant_id}/quarantine/{identity}'
        row=FontUploadSession(id=identity,tenant_id=user.tenant_id,user_id=user.id,storage_key=key,name=Path(body.name).name,
            byte_size=body.byte_size,declaration=body.model_dump(exclude={'name','byte_size'}),expires_at=utcnow()+timedelta(minutes=15))
        db.add(row);url=storage.signed_upload_url(key);db.commit()
        return result(request,{'id':row.id,'upload_url':url,'method':'PUT','headers':{'Content-Type':'font/ttf','x-upsert':'false'},'expires_at':row.expires_at.isoformat(),'max_bytes':MAX_BYTES})

    @router.post('/uploads/{identity}/complete',response_model=Envelope[FontData],response_model_exclude_unset=True,responses=ERROR_RESPONSES)
    def complete(identity:UUID,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True)
        row=db.scalar(select(FontUploadSession).where(FontUploadSession.id==str(identity),FontUploadSession.tenant_id==user.tenant_id,FontUploadSession.user_id==user.id))
        if not row:raise APIError(404,'NOT_FOUND','글꼴 업로드를 찾을 수 없습니다.')
        db.execute(update(Tenant).where(Tenant.id==user.tenant_id).values(name=Tenant.name).execution_options(synchronize_session=False));db.refresh(row)
        if row.font_asset_id:return result(request,payload(owned_font(db,user.tenant_id,row.font_asset_id)))
        if row.status!='pending' or aware(row.expires_at)<=utcnow():raise APIError(409,'UPLOAD_EXPIRED','업로드 시간이 지났습니다. 다시 시작해 주세요.')
        lock_quota(db,user.tenant_id,row.byte_size,pending_id=row.id)
        try:
            raw=storage.get_limited(row.storage_key,MAX_BYTES)
            if len(raw)!=row.byte_size:raise ValueError('size')
            font=publish(db,storage,user,raw,row.name,Declaration.model_validate(row.declaration))
        except APIError:
            row.status='rejected';db.commit();raise
        except Exception:
            raise APIError(422,'FONT_UPLOAD_INCOMPLETE','업로드한 파일을 읽지 못했습니다. 업로드 완료 후 다시 시도해 주세요.') from None
        row.status='completed';row.font_asset_id=font.id;db.commit();return result(request,payload(font))

    @router.get('/{identity}',response_model=Envelope[FontData],response_model_exclude_unset=True,responses=ERROR_RESPONSES)
    def detail(identity:UUID,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db);return result(request,payload(owned_font(db,user.tenant_id,identity)))

    @router.get('/{identity}/content',response_class=Response,responses=binary_responses('font/ttf'))
    def content(identity:UUID,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db);row=owned_font(db,user.tenant_id,identity)
        # Verify before issuing a signed browser URL: never silently render a
        # changed file. Private storage publication paths are immutable.
        source=read_font(db,storage,user.tenant_id,row.id)
        if isinstance(storage,SupabaseStorage):return RedirectResponse(storage.signed_url(row.storage_key),status_code=307)
        return Response(source.data,media_type='font/ttf',headers={'Cache-Control':'no-store'})

    @router.post('/{identity}/glyphs',response_model=Envelope[GlyphReport],response_model_exclude_unset=True,responses=ERROR_RESPONSES)
    def glyphs(identity:UUID,body:GlyphBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db)
        source=read_font(db,storage,user.tenant_id,str(identity))
        return result(request,glyph_report(source.data,body.text))

    app.include_router(router)


def cleanup_font_quarantine(sessions,storage):
    if not isinstance(storage,SupabaseStorage):return 0
    from ..retention.storage_lifecycle import gate,ACTIVE_BACKUPS
    from ..retention.models import BackupRun
    from ..retention.service import lock_tenant,effective_holds
    count=0
    with sessions() as db:
        gate(db)
        if db.scalar(select(BackupRun.id).where(BackupRun.state.in_(ACTIVE_BACKUPS)).limit(1)):return 0
        rows=list(db.scalars(select(FontUploadSession).where(FontUploadSession.created_at<utcnow()-timedelta(hours=3),FontUploadSession.status!='cleaned').limit(20)))
        for row in rows:
            lock_tenant(db,row.tenant_id)
            if effective_holds(db,row.tenant_id):continue
            storage.delete(row.storage_key);row.status='cleaned';count+=1
        db.commit()
    return count
