from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import time
from zipfile import ZipFile

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import select, func

from services.api import svg_import as svg
from services.api.errors import APIError
from services.api.models import Asset
from services.api.tests.test_api import app, client, register, project
from services.api.tests.test_uploads import direct, initiate, quarantine, complete

SAMPLE = b'''<svg xmlns="http://www.w3.org/2000/svg" width="240" height="180" viewBox="0 0 240 180">
<title>Private source title removed</title><defs><linearGradient id="g"><stop offset="0" stop-color="#673344"/><stop offset="1" stop-color="#ffbc77"/></linearGradient>
<clipPath id="clip"><circle cx="120" cy="90" r="75"/></clipPath></defs>
<rect width="240" height="180" fill="#fff2dc"/><g clip-path="url(#clip)"><rect x="30" y="15" width="180" height="150" fill="url(#g)"/></g>
<path d="M 65 112 Q 120 30 175 112 Z" fill="#fff2dc" stroke="#251536" stroke-width="4"/>
</svg>'''


def wrap(content, **attrs):
    at={"width":"200","height":"100",**attrs}
    return ('<svg xmlns="http://www.w3.org/2000/svg" '+" ".join(f'{k}="{v}"' for k,v in at.items())+'>'+content+'</svg>').encode()


def test_static_shapes_gradient_clip_render_and_sanitized_roundtrip():
    clean=svg.sanitize_svg(SAMPLE)
    assert clean.width==240 and clean.height==180
    assert b"Private source title" not in clean.raw and clean.removed==("metadata",)
    assert svg.sanitize_svg(clean.raw).raw==clean.raw
    result=svg.rasterize_svg(clean)
    with Image.open(BytesIO(result)) as im:
        assert im.size==(240,180) and im.format=="PNG"
        assert im.convert("RGB").getpixel((0,0))==(255,242,220)
        assert im.convert("RGB").getpixel((120,35)) != (255,242,220)


@pytest.mark.parametrize("payload",[
    wrap('<script>alert(1)</script>'), wrap('<g onclick="fetch(1)"><rect width="1" height="1"/></g>'),
    wrap('<image href="https://private.invalid/a.png"/>'),wrap('<image href="file:///etc/passwd"/>'),
    wrap('<image href="data:image/png;base64,AAAA"/>'),wrap('<use href="#a"/><g id="a"/>'),
    wrap('<foreignObject><html>content</html></foreignObject>'),wrap('<animate attributeName="width"/>'),
    wrap('<style>@import url(https://private.invalid/x);</style>'),wrap('<rect style="fill: url(https://private.invalid/x)"/>'),
    wrap('<rect style="fill:var(--x)"/>'), wrap('<rect filter="url(#f)"/>'),
    wrap('<defs><filter id="f"><feGaussianBlur stdDeviation="2000"/></filter></defs>'),
    wrap('<rect xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="file:///etc/passwd"/>'),
    wrap('<g xml:base="file:///etc/"><path d="M0 0L1 1"/></g>'),
    wrap('<clipPath id="a" clip-path="url(#a)"><rect width="100" height="100"/></clipPath>'),
    wrap('<defs><clipPath id="a"><g clip-path="url(#b)"/></clipPath><clipPath id="b"><g clip-path="url(#a)"/></clipPath></defs>'),
    b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg width="1" height="1">&x;</svg>',
    b'<!DOCTYPE svg [<!ENTITY a "AAAAAAAA"><!ENTITY b "&a;&a;">]><svg width="1" height="1">&b;</svg>',
    b'\x1f\x8bcompressed-svgz', b'<html/>', b'<svg width="nan" height="100"/>',
    b'<svg width="100" height="100"><path d="M1e300 0L2 2"/></svg>',
], ids=lambda value: sha256(value).hexdigest()[:10])
def test_active_external_recursive_and_xml_payloads_rejected_before_renderer(payload,monkeypatch):
    calls=[];monkeypatch.setattr(svg,"rasterize_svg",lambda *a:calls.append(a))
    with pytest.raises(APIError) as e: svg.prepare_image_upload(payload,svg.SVG_MIME)
    assert e.value.status==422 and not calls


@pytest.mark.parametrize("tag",["text","tspan","textPath","font","glyph"])
def test_text_or_font_is_not_silently_replaced_with_system_glyphs(tag):
    with pytest.raises(APIError) as e: svg.sanitize_svg(wrap(f'<{tag}>한글</{tag}>'))
    assert e.value.code=="SVG_TEXT_OUTLINE_REQUIRED"


@pytest.mark.parametrize("payload,code",[
    (b" "*(svg.MAX_SVG_BYTES+1),"SVG_SIZE_LIMIT"),
    (wrap('<rect/>',width="8193"),"SVG_SIZE_LIMIT"),
    (wrap('<rect/>',width="5000",height="5000"),"SVG_SIZE_LIMIT"),
    (wrap('<rect/>',width="100%"),"SVG_DIMENSIONS_REQUIRED"),
    (wrap('<rect/>'*svg.MAX_NODES),"SVG_COMPLEXITY_LIMIT"),
    (wrap('<g>'*(svg.MAX_DEPTH+1)+'<rect/>'+'</g>'*(svg.MAX_DEPTH+1)),"SVG_COMPLEXITY_LIMIT"),
    (wrap('<path d="'+'M0 0 '*10001+'"/>'),"SVG_COMPLEXITY_LIMIT"),
    (wrap('<path d="M'+'1 1 '*26000+'"/>'),"SVG_COMPLEXITY_LIMIT"),
    (wrap('<g transform="scale(1000000)"><g transform="scale(1000000)"><rect/></g></g>'),"SVG_COMPLEXITY_LIMIT"),
    (wrap('<g><g><g><g><rect/></g></g></g></g>',width="4000",height="4000"),"SVG_COMPLEXITY_LIMIT"),
], ids=lambda value: sha256(value).hexdigest()[:10] if isinstance(value,bytes) else value)
def test_source_canvas_node_path_and_cumulative_transform_budgets(payload,code):
    with pytest.raises(APIError) as e: svg.sanitize_svg(payload)
    assert e.value.code==code


def test_physical_units_and_viewbox_without_pixel_dimensions():
    clean=svg.sanitize_svg(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 160"><rect width="320" height="160"/></svg>')
    assert (clean.width,clean.height)==(320,160)
    assert svg.sanitize_svg(clean.raw).raw==clean.raw
    clean=svg.sanitize_svg(wrap('<rect width="100%" height="100%"/>',width="25.4mm",height="1in"))
    assert (clean.width,clean.height)==(96,96)
    assert svg.sanitize_svg(clean.raw).raw==clean.raw


def test_style_has_priority_independent_of_attribute_order():
    for content in ['<rect style="fill:#ff0000" fill="#0000ff" width="100%" height="100%"/>',
                    '<rect fill="#0000ff" width="100%" height="100%" style="fill:#ff0000"/>']:
        clean=svg.sanitize_svg(wrap(content))
        assert svg.sanitize_svg(clean.raw).raw==clean.raw
        image=Image.open(BytesIO(svg.rasterize_svg(clean)))
        assert image.convert('RGB').getpixel((50,50))==(255,0,0)


def test_actual_renderer_process_is_killed_on_deadline_without_environment_secrets(monkeypatch):
    monkeypatch.setattr(svg,"RENDER_TIMEOUT",.2)
    monkeypatch.setattr(svg,"_RENDER_CODE","import time;time.sleep(60)")
    started=time.monotonic()
    with pytest.raises(APIError) as e: svg.rasterize_svg(svg.sanitize_svg(SAMPLE))
    assert e.value.code=="SVG_RENDER_TIMEOUT" and time.monotonic()-started<3


def test_high_complexity_accepted_document_is_bounded_by_actual_process_deadline():
    # Near node budget, overlapping antialiased circles; no filters or recursion.
    content=''.join(f'<circle cx="{n%500}" cy="{n%480}" r="400" fill="#123456" opacity="0.01"/>' for n in range(1900))
    started=time.monotonic()
    try:
        data=svg.rasterize_svg(svg.sanitize_svg(wrap(content,width="512",height="512")))
        assert Image.open(BytesIO(data)).size==(512,512)
    except APIError as e:
        assert e.code=="SVG_RENDER_TIMEOUT"
    assert time.monotonic()-started<svg.RENDER_TIMEOUT+3


def test_direct_svg_completion_is_immutable_idempotent_and_source_not_in_library(direct):
    app,client,auth,storage=direct
    ticket=initiate(client,len(SAMPLE),svg.SVG_MIME,"mark.svg")
    key=quarantine(app,storage,ticket['id'],SAMPLE)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:complete(client,ticket),range(2)))
    assert [r.status_code for r in responses]==[200,200]
    asset=responses[0].json()['data']
    assert asset['id']==responses[1].json()['data']['id'] and asset['content_type']=='image/png' and asset['source']=='svg_import'
    with app.state.session_factory() as db:
        rows=list(db.scalars(select(Asset)));assert len(rows)==2
        png=db.get(Asset,asset['id']);source=db.get(Asset,png.metadata_json['svg_import']['source_asset_id'])
        assert source.source=='sanitized_svg' and source.content_type==svg.SVG_MIME
        assert source.workspace_id==png.workspace_id
        assert sha256(storage.get(source.storage_key)).hexdigest()==source.metadata_json['sha256']
        assert sha256(storage.get(png.storage_key)).hexdigest()==png.metadata_json['sha256']
        assert png.metadata_json['svg_import']['uploaded_sha256']==sha256(SAMPLE).hexdigest()
        assert b'Private source title' not in storage.get(source.storage_key)
    storage.objects[key]=b'changed after validation'
    assert complete(client,ticket).json()['data']['id']==asset['id'] and storage.put_count==2
    listed=client.get('/v1/assets').json()['data']['items']
    assert [r['id'] for r in listed]==[asset['id']]


def test_svg_bad_declared_type_and_active_content_have_no_durable_bytes(direct):
    app,client,auth,storage=direct
    for raw,mime in [(SAMPLE,'image/png'),(wrap('<script/>'),svg.SVG_MIME)]:
        ticket=initiate(client,len(raw),mime,'bad.svg');quarantine(app,storage,ticket['id'],raw)
        assert complete(client,ticket).status_code==422
        assert complete(client,ticket).status_code==409
    assert storage.put_count==0


def test_final_quota_counts_sanitized_source_plus_png(direct):
    app,client,auth,storage=direct
    prepared=svg.prepare_image_upload(SAMPLE,svg.SVG_MIME)
    ticket=initiate(client,len(SAMPLE),svg.SVG_MIME);quarantine(app,storage,ticket['id'],SAMPLE)
    with app.state.session_factory() as db:
        db.add(Asset(tenant_id=auth['tenant']['id'],storage_key='quota',original_name='fixture',content_type='image/png',width_px=1,height_px=1,
            byte_size=svg.QUOTA_BYTES-len(prepared.raw)-len(prepared.svg.raw)+1));db.commit()
    response=complete(client,ticket)
    assert response.status_code==422 and response.json()['code']=='ASSET_QUOTA_EXCEEDED' and storage.put_count==0


def test_multipart_svg_download_acl_and_editable_source_lineage(client,app,tmp_path,monkeypatch):
    auth=register(client);item=project(client)
    uploaded=client.post('/v1/assets',files={'file':('mark.svg',SAMPLE,svg.SVG_MIME)},data={'project_id':item['id']})
    assert uploaded.status_code==201,uploaded.text
    asset=uploaded.json()['data']
    assert asset['content_type']=='image/png'
    with app.state.session_factory() as db:
        row=db.get(Asset,asset['id']);source_id=row.metadata_json['svg_import']['source_asset_id'];source=db.get(Asset,source_id)
        source_raw=app.state.storage.get(source.storage_key)
    response=client.get(f'/v1/assets/{source_id}/content')
    assert response.status_code==200 and 'attachment' in response.headers['content-disposition']
    assert response.content==source_raw
    other=TestClient(app);register(other,'other-svg@example.com')
    assert other.get(f'/v1/assets/{source_id}/content').status_code==404
    assert other.get(asset['url']).status_code==404;other.close()
    scene=deepcopy(item['scene']);scene['faces'][0]['objects'].append({'id':'mark','type':'image','face_id':'front','asset_id':asset['id'],
        'x_mm':20,'y_mm':90,'width_mm':60,'height_mm':45,'z_index':0})
    saved=client.patch(f"/v1/projects/{item['id']}/draft",json={'base_revision':1,'scene':scene})
    assert saved.status_code==200,saved.text
    from services.api.tests.test_editable_exports import enqueue, run
    job=enqueue(client,saved.json()['data']);assert run(app)==1
    status=client.get(f"/v1/jobs/{job['id']}").json()['data'];assert status['status']=='succeeded',status
    with ZipFile(BytesIO(client.get(status['download_url']).content)) as archive:
        rows=json.loads(archive.read('assets.json'))['items']
        assert len(rows)==2
        source=next(r for r in rows if r['id']==source_id)
        assert source['role']=='source' and source['source']=='sanitized_svg'
        assert archive.read(source['path'])==source_raw
        assert sha256(source_raw).hexdigest()==source['sha256']
        assert '정화한 정적 벡터' in archive.read('README.ko.txt').decode()
    # The same PNG remains the output source; no hidden SVG execution in PDF.
    queued=client.post('/v1/exports',json={'project_id':item['id'],'base_revision':2})
    assert queued.status_code==202,queued.text
    from services.api.jobs import process_pending_jobs
    assert process_pending_jobs(app.state.session_factory,app.state.storage)==1
    from pypdf import PdfReader
    reviewed=client.get(f"/v1/jobs/{queued.json()['data']['id']}").json()['data']
    assert reviewed['status']=='succeeded',reviewed
    pdf=PdfReader(BytesIO(client.get(reviewed['download_url']).content))
    assert any(x.image.size==(240,180) for x in pdf.pages[0].images)
    # Both bytes survive encrypted backup, independent restore and app reopen.
    from services.api.tests.test_backup_restore import backup_script, verify_restored_app
    for key,value in {'APP_ENV':'test','DATABASE_URL':app.state.settings.database_url,'STORAGE_BACKEND':'local','STORAGE_DIR':str(app.state.storage.root)}.items():
        monkeypatch.setenv(key,value)
    output=tmp_path/'svg-backup';key=tmp_path/'outside-backup.key'
    backed=backup_script.backup(output,key)
    assert backed['verified'] and backed['objects_verified']==4
    restored=tmp_path/'svg-restored';backup_script.verify(output,key,restored)
    credentials=tmp_path/'explicit-qa.json';credentials.write_text(json.dumps({'email':'owner@example.com'}),encoding='utf-8')
    report=verify_restored_app(restored,credentials)
    assert report['application_reopen_verified'] and report['assets_reopened']==2 and report['export_files_reopened']==2


def test_unsafe_svg_cannot_be_placed_by_source_uuid(client,app):
    register(client);item=project(client)
    asset=client.post('/v1/assets',files={'file':('mark.svg',SAMPLE,svg.SVG_MIME)}).json()['data']
    with app.state.session_factory() as db: source_id=db.get(Asset,asset['id']).metadata_json['svg_import']['source_asset_id']
    scene=deepcopy(item['scene']);scene['faces'][0]['objects'].append({'id':'raw','type':'image','face_id':'front','asset_id':source_id,
        'x_mm':20,'y_mm':90,'width_mm':60,'height_mm':45,'z_index':0})
    response=client.patch(f"/v1/projects/{item['id']}/draft",json={'base_revision':1,'scene':scene})
    assert response.status_code==422
