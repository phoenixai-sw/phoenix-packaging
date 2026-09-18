"""ICC registry and durable, visibly unapproved engine tests. No client approval flags."""
from copy import deepcopy
from datetime import timedelta
from typing import Literal
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import Field,ConfigDict
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from .auth import require_auth
from .business import owned_record
from .database import utcnow
from .editor_sessions import enforce_edit_lease
from .errors import APIError
from .models import Asset, Job, Project
from .feature_models import Evidence, RegistryVersion, AuditEvent
from .contracts.base import ContractModel, Envelope, ERROR_RESPONSES
from .exporters.print_profile import PrintProfile, parse_print_profile, ADAPTER_ID
from .exporters.print_color import inspect_icc, MAX_ICC_BYTES
from .exporters.print_pdf import inspect_print
from .billing.service import canonical_hash

BUILTIN_ID='synthetic-cmyk-engine-test-v1'
BUILTIN_ICC='builtin-synthetic-test-v1'
ICC_PATH=Path(__file__).resolve().parents[2]/'fixtures'/'icc'/'synthetic-cmyk-test.icc'


class EngineProfileDTO(ContractModel):
    id: str
    name: str
    manufacturer: str
    status: str
    is_demo: bool
    test_only: bool
    requirements: PrintProfile
    print_request_available: bool=False


class ProfilesDTO(ContractModel):
    items: list[EngineProfileDTO]


class ICCDTO(ContractModel):
    id: str
    sha256: str
    description: str
    byte_size: int
    source: str
    license: str


class ProfileBody(ContractModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name: str=Field(min_length=1,max_length=160)
    manufacturer: str=Field(min_length=1,max_length=160)
    material: str=Field(min_length=1,max_length=120)
    source: str=Field(min_length=1,max_length=2000)
    license: str=Field(min_length=1,max_length=2000)
    requirements: PrintProfile
    review_available: bool=False


class TestBody(ContractModel):
    project_id: UUID
    base_revision: int=Field(ge=1)
    profile_id: str=Field(min_length=1,max_length=100)
    mode: Literal['engine_test','print_request']='engine_test'


class TestJobDTO(ContractModel):
    id: str
    status: str
    review_only: bool=True
    credits_charged: int=0
    format: Literal['print_engine_zip','print_request_zip']='print_engine_zip'


def _builtin(layout='face_pages',finishing=False):
    return {'id':BUILTIN_ID,'name':'합성 CMYK 엔진 시험 — 제조 프로필 아님','manufacturer':'Phoenix internal test','print_request_available':False,
            'status':'draft','is_demo':True,'test_only':True,'requirements':parse_print_profile({'icc_id':BUILTIN_ICC,
            'icc_sha256':sha256(ICC_PATH.read_bytes()).hexdigest(),'layout':layout,
            **({'finishing_delivery':'separate_process_pdf_v1'} if finishing else {})})}


def _profile(db,identity,*,test_mode,print_request=False):
    if identity==BUILTIN_ID:
        if print_request:raise APIError(422,'TEST_ICC_PRODUCTION_FORBIDDEN','합성 시험 ICC로는 인쇄 의뢰본을 만들 수 없습니다. 실제 ICC 프로필을 선택해 주세요.')
        if not test_mode:raise APIError(422,'TEST_ICC_PRODUCTION_FORBIDDEN','합성 시험 ICC는 제작에 사용할 수 없습니다.')
        return _builtin()
    row=db.get(RegistryVersion,identity)
    if not row or row.kind!='profile' or row.status=='revoked' or row.details.get('requirements',{}).get('adapter_id')!=ADAPTER_ID:
        raise APIError(404,'PRINT_PROFILE_NOT_FOUND','ICC 출력 프로필을 찾을 수 없습니다.')
    if (test_mode or print_request) and row.details.get('review_available') is not True:raise APIError(404,'PRINT_PROFILE_NOT_FOUND','시험용으로 공개한 ICC 프로필이 아닙니다.')
    return {'id':row.id,'name':row.name,'manufacturer':row.manufacturer,'status':row.status,'is_demo':row.is_demo,
            'test_only':row.status!='approved' or row.is_demo,'requirements':parse_print_profile(row.details['requirements']),'print_request_available':True}


def _mode_flags(mode):
    return {'test_mode':mode=='test','print_request':mode=='print_request'}


def freeze_print_output(db,profile_id,*,test_mode,layout=None,finishing=False,print_request=False):
    """print_request: customer-visible real-ICC profile, no engine-test stamp, still no manufacturer approval."""
    if test_mode and print_request:raise APIError(422,'PRINT_MODE_CONFLICT','시험 출력과 인쇄 의뢰본은 동시에 만들 수 없습니다.')
    profile=_profile(db,profile_id,test_mode=test_mode,print_request=print_request)
    if profile_id==BUILTIN_ID and (layout or finishing):profile=_builtin(layout or 'face_pages',finishing=finishing)
    if print_request and (layout or finishing):
        # The registered profile fixes ICC and bleed; the project decides net/face pages and finishing delivery.
        requirements=dict(profile['requirements']);requirements['layout']=layout or requirements['layout']
        if finishing:requirements['finishing_delivery']='separate_process_pdf_v1'
        profile['requirements']=parse_print_profile(requirements)
    p=profile['requirements']
    if p['icc_id']==BUILTIN_ICC:
        if not test_mode:raise APIError(422,'TEST_ICC_PRODUCTION_FORBIDDEN','합성 시험 ICC는 제작에 사용할 수 없습니다.')
        icc={'id':BUILTIN_ICC,'sha256':p['icc_sha256'],'source':'Phoenix synthetic CI profile','license':'CC0-1.0','test_only':True}
    else:
        row=db.get(RegistryVersion,p['icc_id'])
        if not row or row.kind!='icc_profile' or row.status=='revoked':raise APIError(422,'ICC_UNAVAILABLE','등록 ICC가 없거나 철회되었습니다.')
        evidence=db.get(Evidence,row.details.get('evidence_id'))
        if not evidence or evidence.sha256!=p['icc_sha256'] or row.details.get('sha256')!=p['icc_sha256']:
            raise APIError(422,'ICC_HASH_MISMATCH','ICC 등록 버전과 파일 해시가 다릅니다.')
        if not test_mode and (p['icc_sha256']==sha256(ICC_PATH.read_bytes()).hexdigest() or row.details.get('description','').startswith('Phoenix SYNTHETIC')):
            raise APIError(422,'TEST_ICC_PRODUCTION_FORBIDDEN','합성 시험 ICC는 제작 프로필로 사용할 수 없습니다.')
        icc={'id':row.id,'evidence_id':evidence.id,'sha256':evidence.sha256,'source':row.details['source'],'license':row.details['license'],'test_only':False}
    return {'version':'1.0','mode':'test' if test_mode else 'print_request' if print_request else 'production','profile_id':profile_id,'requirements':p,'icc':icc}


def resolve_print_icc(db,storage,frozen):
    expected=freeze_print_output(db,frozen['profile_id'],**_mode_flags(frozen['mode']),layout=frozen['requirements']['layout'],finishing=frozen['requirements'].get('finishing_delivery')=='separate_process_pdf_v1')
    if canonical_hash(expected)!=canonical_hash(frozen):raise APIError(409,'PRINT_PROFILE_CHANGED','출력 프로필·ICC가 변경되거나 철회되었습니다.')
    info=frozen['icc']
    raw=ICC_PATH.read_bytes() if info['id']==BUILTIN_ICC else storage.get(db.get(Evidence,info['evidence_id']).storage_key)
    if sha256(raw).hexdigest()!=info['sha256']:raise APIError(422,'ICC_HASH_MISMATCH','보관 ICC 바이트의 해시가 다릅니다.')
    inspect_icc(raw);return raw


def check_test_access(db,tenant_id,snapshot,*,lock=False):
    from .editable_exports import _actor,check_archive_access
    actor=_actor(db,tenant_id,snapshot['actor_id'],lock=lock)
    check_archive_access(db,actor,{**snapshot,'editable_assets':[]},creating=True,lock=lock)
    statement=select(RegistryVersion).where(RegistryVersion.id.in_([snapshot['print_output']['profile_id'],snapshot['print_output']['icc']['id']])).order_by(RegistryVersion.id).execution_options(populate_existing=True)
    list(db.scalars(statement.with_for_update() if lock else statement))
    expected=freeze_print_output(db,snapshot['print_output']['profile_id'],**_mode_flags(snapshot['print_output']['mode']),layout=snapshot['print_output']['requirements']['layout'],finishing=snapshot['print_output']['requirements'].get('finishing_delivery')=='separate_process_pdf_v1')
    if canonical_hash(expected)!=canonical_hash(snapshot['print_output']):raise APIError(409,'PRINT_PROFILE_CHANGED','시험 출력 프로필·ICC가 변경되었습니다.')
    for face in snapshot['scene']['faces']:
        for obj in face['objects']:
            if obj['type']!='image' or not obj.get('visible',True) or not obj.get('print_enabled',True):continue
            statement=select(Asset).where(Asset.id==obj['asset_id'],Asset.tenant_id==tenant_id).execution_options(populate_existing=True)
            asset=db.scalar(statement.with_for_update() if lock else statement)
            if not asset or asset.workspace_id not in (None,snapshot.get('workspace_id')):
                raise APIError(404,'ASSET_UNAVAILABLE','시험 출력 이미지 접근 권한이 변경되었습니다.')
    return actor


def freeze_print_assets(db,storage,project):
    from io import BytesIO
    from PIL import Image
    identities={o['asset_id'] for f in project.scene['faces'] for o in f['objects'] if o['type']=='image' and o.get('visible',True) and o.get('print_enabled',True)}
    if len(identities)>20:raise APIError(413,'PRINT_ASSET_LIMIT','ICC 출력은 서로 다른 이미지 20개 이하를 지원합니다.')
    records=[];total=0;pixels=0
    for identity in sorted(identities):
        row=db.get(Asset,identity)
        if not row or row.tenant_id!=project.tenant_id or row.workspace_id not in (None,project.workspace_id):
            raise APIError(404,'ASSET_UNAVAILABLE','현재 프로젝트의 이미지 접근 권한을 확인해 주세요.')
        if row.byte_size>20*1024*1024 or total+row.byte_size>200*1024*1024:raise APIError(413,'PRINT_ASSET_LIMIT','출력 이미지 합계는 200MiB 이하입니다.')
        raw=storage.get(row.storage_key);digest=sha256(raw).hexdigest();total+=len(raw)
        if len(raw)!=row.byte_size or ((row.metadata_json or {}).get('sha256') and row.metadata_json['sha256']!=digest):
            raise APIError(422,'PRINT_SOURCE_HASH_MISMATCH','이미지 보관 해시가 다릅니다.')
        with Image.open(BytesIO(raw)) as image:pixels+=image.width*image.height
        if pixels>40_000_000:raise APIError(413,'PRINT_PIXEL_LIMIT','ICC 출력 이미지 합계는 4천만 픽셀 이하입니다.')
        records.append({'id':identity,'sha256':digest,'byte_size':len(raw),'workspace_id':row.workspace_id,
                        'quality_metadata':{'image_quality':deepcopy((row.metadata_json or {}).get('image_quality'))}})
    return records


def install_print_engine_routes(app,db_session,project_payload,snapshot_revision):
    router=APIRouter(prefix='/v1',tags=['print-engine'])
    def result(request,value):return {'data':value,'request_id':request.state.request_id}
    def test_job(job):return {'id':job.id,'status':job.status,'format':'print_request_zip' if (job.snapshot or {}).get('print_output',{}).get('mode')=='print_request' else 'print_engine_zip','review_only':True,'credits_charged':0}
    def admin(request,db):
        user,_=require_auth(request,db,mutate=True,authorize_write=False,enforce_membership=False)
        if not user.is_admin:raise APIError(403,'ADMIN_REQUIRED','플랫폼 관리자 권한이 필요합니다.')
        return user
    @router.get('/print-engine/profiles',response_model=Envelope[ProfilesDTO],response_model_exclude_unset=True,responses=ERROR_RESPONSES)
    def profiles(request:Request,db=Depends(db_session)):
        require_auth(request,db);items=[_builtin()]
        for row in db.scalars(select(RegistryVersion).where(RegistryVersion.kind=='profile',RegistryVersion.status!='revoked')):
            if row.details.get('review_available') is True and row.details.get('requirements',{}).get('adapter_id')==ADAPTER_ID:
                items.append(_profile(db,row.id,test_mode=True))
        return result(request,{'items':items})
    @router.post('/admin/print-engine/icc',status_code=201,response_model=Envelope[ICCDTO],response_model_exclude_unset=True,responses=ERROR_RESPONSES)
    def upload_icc(request:Request,file:UploadFile=File(...),source:str=Form(...,min_length=1,max_length=2000),license:str=Form(...,min_length=1,max_length=2000),db=Depends(db_session)):
        user=admin(request,db);source=source.strip();license=license.strip()
        if not source or not license:raise APIError(422,'ICC_LICENSE_REQUIRED','ICC 출처와 보관·PDF 임베드 사용권을 기록해 주세요.')
        raw=file.file.read(MAX_ICC_BYTES+1);info=inspect_icc(raw)
        identity=str(uuid4());evidence_id=str(uuid4());key=f'{user.tenant_id}/evidence/{evidence_id}'
        app.state.storage.put(key,raw,'application/vnd.iccprofile')
        if sha256(app.state.storage.get(key)).hexdigest()!=info['sha256']:raise APIError(503,'ICC_STORAGE_FAILED','ICC 보관을 확인하지 못했습니다.')
        db.add(Evidence(id=evidence_id,tenant_id=user.tenant_id,uploaded_by=user.id,storage_key=key,name=Path(file.filename or 'output.icc').name[:180],
                       sha256=info['sha256'],content_type='application/vnd.iccprofile',byte_size=len(raw)))
        db.add(RegistryVersion(id=identity,kind='icc_profile',name=info['description'][:160],manufacturer='User supplied',status='draft',is_demo=False,
            details={**info,'evidence_id':evidence_id,'source':source,'license':license},created_by=user.id))
        db.add(AuditEvent(tenant_id=user.tenant_id,actor_id=user.id,action='icc_registered',entity_id=identity,details={'sha256':info['sha256']}));db.commit()
        return result(request,{k:v for k,v in {'id':identity,**info,'source':source,'license':license}.items() if k in ICCDTO.model_fields})
    @router.post('/admin/print-engine/profiles',status_code=201,response_model=Envelope[EngineProfileDTO],response_model_exclude_unset=True,responses=ERROR_RESPONSES)
    def register_profile(body:ProfileBody,request:Request,db=Depends(db_session)):
        user=admin(request,db);p=parse_print_profile(body.requirements.model_dump())
        row=db.get(RegistryVersion,p['icc_id'])
        if p['icc_id']==BUILTIN_ICC or not row or row.kind!='icc_profile' or row.status=='revoked' or row.details.get('sha256')!=p['icc_sha256']:
            raise APIError(422,'ICC_UNAVAILABLE','등록한 실제 ICC 버전과 해시를 선택해 주세요.')
        identity=str(uuid4());version=RegistryVersion(id=identity,kind='profile',name=body.name,manufacturer=body.manufacturer,status='draft',is_demo=False,
            details=body.model_dump(exclude={'name','manufacturer'}),created_by=user.id);db.add(version)
        db.add(AuditEvent(tenant_id=user.tenant_id,actor_id=user.id,action='print_profile_registered',entity_id=identity,details={'icc_id':p['icc_id']}));db.commit()
        return result(request,{'id':identity,'name':version.name,'manufacturer':version.manufacturer,'status':'draft','is_demo':False,'test_only':True,'requirements':p})
    @router.post('/print-engine/tests',status_code=202,response_model=Envelope[TestJobDTO],response_model_exclude_unset=True,responses=ERROR_RESPONSES)
    def create_test(body:TestBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True);project=owned_record(db,Project,body.project_id,user.tenant_id)
        enforce_edit_lease(db,project,request)
        if project.base_revision!=body.base_revision:raise APIError(409,'REVISION_CONFLICT','최신 디자인을 저장한 뒤 시험 출력해 주세요.')
        from .geometry.snapshots import project_geometry
        geometry=project_geometry(project)
        request_mode=body.mode=='print_request'
        frozen=freeze_print_output(db,body.profile_id,test_mode=not request_mode,print_request=request_mode,layout='net' if project.template_id!='three-side-seal' else 'face_pages',finishing=bool(geometry.get('holes') or geometry.get('pouch_features') is not None))
        resolve_print_icc(db,app.state.storage,frozen)
        operation=request.headers.get('idempotency-key') or 'print-test:'+canonical_hash({'project':project.id,'revision':body.base_revision,'print_output':frozen})
        if not 1<=len(operation)<=160:raise APIError(422,'IDEMPOTENCY_KEY_INVALID','요청 식별자를 확인해 주세요.')
        digest=canonical_hash({'body':body.model_dump(mode='json'),'print_output':frozen})
        old=db.scalar(select(Job).where(Job.tenant_id==user.tenant_id,Job.operation_key==operation))
        if old:
            if old.kind!='review_export' or old.request_hash!=digest:raise APIError(409,'IDEMPOTENCY_CONFLICT','같은 식별자로 다른 출력 요청을 보낼 수 없습니다.')
            return result(request,test_job(old))
        count=db.scalar(select(func.count()).select_from(Job).where(Job.tenant_id==user.tenant_id,Job.kind.in_(['review_export','editable_export']),Job.created_at>utcnow()-timedelta(hours=1)))
        if count>=30:raise APIError(429,'EXPORT_RATE_LIMIT','출력 요청이 많습니다. 잠시 후 다시 시도해 주세요.')
        revision=snapshot_revision(db,project,'print_engine_test');snapshot={**project_payload(project),'revision_id':revision.id,'actor_id':user.id,'print_output':frozen,
            'print_assets':freeze_print_assets(db,app.state.storage,project)}
        def resolver(identity):
            asset=owned_record(db,Asset,identity,user.tenant_id)
            if asset.workspace_id not in (None,project.workspace_id):raise APIError(404,'ASSET_UNAVAILABLE','현재 작업공간 이미지가 아닙니다.')
            return app.state.storage.get(asset.storage_key)
        resolver.metadata=lambda identity:owned_record(db,Asset,identity,user.tenant_id).metadata_json
        from .font_assets.service import freeze_fonts,attach_font_resolver
        snapshot['font_assets']=freeze_fonts(db,user.tenant_id,snapshot['scene'])
        attach_font_resolver(resolver,db,app.state.storage,user.tenant_id,snapshot)
        inspect_print(snapshot,frozen['requirements'],resolver,test_mode=not request_mode,print_request=request_mode)
        job=Job(tenant_id=user.tenant_id,project_id=project.id,revision_id=revision.id,kind='review_export',operation_key=operation,request_hash=digest,snapshot=snapshot)
        db.add(job)
        try:db.commit()
        except IntegrityError:
            db.rollback();old=db.scalar(select(Job).where(Job.tenant_id==user.tenant_id,Job.operation_key==operation))
            if old and old.request_hash==digest:return result(request,test_job(old))
            raise APIError(409,'EXPORT_CONFLICT','다른 출력 요청과 충돌했습니다.') from None
        return result(request,test_job(job))
    app.include_router(router)
