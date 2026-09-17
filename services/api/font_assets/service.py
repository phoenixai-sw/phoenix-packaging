from hashlib import sha256
from io import BytesIO
from sqlalchemy import select
from ..errors import APIError
from ..feature_models import Brand
from .models import FontAsset
from .types import FontSource
from .validation import MAX_BYTES, glyph_report


def owned_font(db, tenant_id, identity):
    row = db.scalar(select(FontAsset).where(FontAsset.id==str(identity), FontAsset.tenant_id==tenant_id))
    if row is None: raise APIError(404, 'FONT_NOT_FOUND', '이 팀의 글꼴을 찾을 수 없습니다.')
    return row


def payload(row):
    return {**{key:getattr(row,key) for key in ('id','family','subfamily','weight','sha256','byte_size','glyph_count','fs_type','ascent_ratio','descent_ratio','license_name','source_url','rights_holder','redistribution_allowed')},
            'name':row.original_name,'rights_verification':'user_attested','created_at':row.created_at.isoformat(),
            'url':f'/api/v1/fonts/{row.id}/content'}


def identity(row):
    return {key:getattr(row,key) for key in ('id','sha256','weight','family','storage_key','byte_size','license_name','license_text','source_url','rights_holder','redistribution_allowed')}


def font_ids(scene):
    return {str(obj['font_asset_id']) for face in scene.get('faces',[]) for obj in face.get('objects',[]) if obj.get('font_asset_id')}


def freeze_fonts(db, tenant_id, scene):
    return [identity(owned_font(db, tenant_id, value)) for value in sorted(font_ids(scene))]


def read_font(db, storage, tenant_id, asset_id, frozen=None):
    row = owned_font(db, tenant_id, asset_id)
    if frozen is not None and identity(row) != frozen:
        raise APIError(409, 'FONT_SNAPSHOT_CHANGED', '저장된 글꼴 파일·권리 기록이 달라 출력할 수 없습니다.')
    if row.storage_key != f'{tenant_id}/fonts/{row.id}.ttf':
        raise APIError(422, 'FONT_STORAGE_INVALID', '글꼴 저장 위치를 확인할 수 없습니다.')
    try: raw = storage.get_limited(row.storage_key, MAX_BYTES)
    except Exception: raise APIError(422, 'FONT_UNAVAILABLE', '원본 글꼴 파일을 읽지 못했습니다.') from None
    if len(raw) != row.byte_size or sha256(raw).hexdigest() != row.sha256:
        raise APIError(422, 'FONT_CHECKSUM_MISMATCH', '원본 글꼴 검사값이 달라 다른 글꼴로 대체하지 않았습니다.')
    return FontSource(row.id, row.sha256, row.family, row.weight, raw, row.license_name, row.redistribution_allowed)


def attach_font_resolver(resolver, db_or_factory, storage, tenant_id, snapshot=None):
    frozen = {item['id']:item for item in (snapshot or {}).get('font_assets',[])}
    cache = {}
    def resolve(asset_id):
        key = str(asset_id)
        if snapshot is not None and key not in frozen:
            raise APIError(422, 'FONT_SNAPSHOT_REQUIRED', '출력 작업에 고정된 글꼴 기록이 없습니다.')
        if key in cache:return cache[key]
        if callable(db_or_factory):
            with db_or_factory() as db: cache[key]=read_font(db, storage, tenant_id, key, frozen.get(key))
        else:cache[key]=read_font(db_or_factory, storage, tenant_id, key, frozen.get(key))
        return cache[key]
    resolver.font = resolve
    return resolver


def validate_scene_fonts(db, user, scene, previous=None, *, enforce_brand=True):
    previous = previous or {}
    # Existing selections remain usable after the brand allowlist is narrowed.
    old = {(face['id'],obj['id']):str(obj.get('font_asset_id') or '')
           for face in previous.get('faces',[]) for obj in face.get('objects',[])}
    brand = db.get(Brand, str(scene.get('brand_id'))) if scene.get('brand_id') else None
    allowed = set(brand.font_asset_ids or []) if brand and brand.tenant_id==user.tenant_id else set()
    for face in scene.get('faces',[]):
        for obj in face.get('objects',[]):
            asset_id = obj.get('font_asset_id')
            if asset_id:
                row = owned_font(db, user.tenant_id, asset_id)
                if obj.get('type') != 'text' or obj.get('font_weight',400) != row.weight:
                    raise APIError(422, 'FONT_WEIGHT_MISMATCH', '글꼴 파일의 실제 두께와 문구 설정을 맞춰 주세요.', {'object_id':obj['id']})
                if enforce_brand and str(asset_id) != old.get((face['id'],obj['id'])) and str(asset_id) not in allowed:
                    raise APIError(422, 'FONT_BRAND_REQUIRED', '먼저 연결한 브랜드의 허용 글꼴로 등록해 주세요.', {'object_id':obj['id']})
            elif obj.get('type') == 'text' and obj.get('font_weight',400) not in (400,700):
                raise APIError(422, 'UNSUPPORTED_FONT_WEIGHT', '기본 글꼴은 400과 700 두께를 지원합니다.', {'object_id':obj['id']})
