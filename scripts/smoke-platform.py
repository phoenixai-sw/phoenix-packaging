"""Hosted integration checks. --ai explicitly spends 20 trial credits on two real calls.

Reuses the existing QA account and persisted job/quote IDs. Never logs credentials,
signed URLs or provider payloads. Do not delete state to retry a paid operation.
"""
import argparse
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import time
import uuid

import httpx
from PIL import Image
from pypdf import PdfReader
from smoke_session import attach_session

ROOT=Path(__file__).resolve().parents[1]
LOCAL=ROOT/'.local'
ORIGIN='https://phoenix-packaging.vercel.app'
STATE=LOCAL/'cloud-platform-smoke-state.json'
REPORT=LOCAL/'cloud-platform-smoke-result.json'
parser=argparse.ArgumentParser();parser.add_argument('--ai',action='store_true');parser.add_argument('--session-file',type=Path,default=LOCAL/'google-smoke-session.json');args=parser.parse_args()
state=json.loads(STATE.read_text()) if STATE.exists() else {'run':uuid.uuid4().hex,'projects':{},'jobs':{}}
report=json.loads(REPORT.read_text()) if REPORT.exists() else {'origin':ORIGIN,'checks':[]}
client=httpx.Client(base_url=ORIGIN+'/api/v1',headers={'Origin':ORIGIN},timeout=60,follow_redirects=True)


def persist():
    STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


def request(method,path,**options):
    response=client.request(method,path,**options)
    if response.status_code>=400:
        try:code=response.json().get('code','HTTP_ERROR')
        except Exception:code='NON_JSON'
        raise RuntimeError(f'{method} {path}: HTTP{response.status_code} {code}')
    return response.json()['data']


def passed(name):
    if name not in report['checks']:report['checks'].append(name)
    persist();print('PASS '+name,flush=True)


def wait_job(identity):
    until=time.monotonic()+780
    prior=None
    while time.monotonic()<until:
        item=request('GET','/jobs/'+identity)
        if item['status']!=prior:
            print('Job '+identity[:8]+': '+item['status'],flush=True);prior=item['status']
        if item['status'] in {'succeeded','failed','partially_succeeded','canceled'}:
            if item['status']!='succeeded':raise RuntimeError('Job ended '+item['status']+' '+str((item.get('error') or {}).get('code','')))
            return item
        time.sleep(5)
    raise RuntimeError('Job still pending; rerun with persisted state, never recreate it')


def project(kind,**extra):
    if kind in state['projects']:return request('GET','/projects/'+state['projects'][kind])
    data=request('POST','/projects',json={'name':'Cloud QA '+kind,'product_name':'제주 말차 그래놀라','brand_name':'피닉스 QA','template_id':kind,'width_mm':160,'height_mm':230,**extra})
    state['projects'][kind]=data['id'];persist();return data


def core():
    config=request('GET','/config')
    assert config['direct_upload'] and config['upload_max_bytes']==20*1024*1024
    assert config['ai_provider']=='openai' and config['billing_provider']=='disabled' and not config['production_export_enabled']
    billing=request('GET','/billing');assert not billing['payment_capabilities']['checkout_available']
    assert client.get('/admin/overview').status_code==403
    passed('hosted capabilities and paid/admin gates')
    for kind,extra,pages in [('three-side-seal',{},2),('stand-up-pouch',{'bottom_mm':80},4),('folding-box',{'depth_mm':60},7)]:
        item=project(kind,**extra)
        scene=item['scene'];front=scene['faces'][0]
        front['objects'][1]['text']='높은 단백질 함량\n한글 · English 123'
        front['objects'][1]['font_size_pt']=18;front['objects'][1]['height_mm']=40
        payload={'base_revision':item['base_revision'],'scene':scene}
        saved=request('PATCH','/projects/'+item['id']+'/draft',json=payload)
        assert client.patch('/projects/'+item['id']+'/draft',json=payload).status_code==409
        reopened=request('GET','/projects/'+item['id'])
        assert reopened['scene']['faces'][0]['objects'][1]['text']==front['objects'][1]['text']
        estimate=request('POST','/preflight',json={'project_id':item['id'],'base_revision':saved['base_revision'],'kind':'review'})
        assert estimate['status']=='pass'
        production=request('POST','/preflight',json={'project_id':item['id'],'base_revision':saved['base_revision'],'kind':'production','reviewed_face_ids':[f['id'] for f in scene['faces']]})
        assert not production['production_allowed'] and production['status']=='blocked'
        job=request('POST','/exports',headers={'Idempotency-Key':state['run']+'-review-'+kind+'-'+str(saved['base_revision'])},json={'project_id':item['id'],'base_revision':saved['base_revision'],'kind':'review'})
        final=wait_job(job['id'])
        pdf=client.get('/exports/'+job['id']+'/download');assert pdf.status_code==200 and pdf.content.startswith(b'%PDF-')
        reader=PdfReader(BytesIO(pdf.content));assert len(reader.pages)==pages
        assert abs(float(reader.pages[0].trimbox.width)*25.4/72-160)<.01
        assert abs(float(reader.pages[0].trimbox.height)*25.4/72-230)<.01
        assert '높은 단백질 함량' in reader.pages[0].extract_text()
        (LOCAL/('cloud-'+kind+'-review.pdf')).write_bytes(pdf.content)
        report[kind]={'project_id':item['id'],'job_id':job['id'],'pages':pages,'pdf_bytes':len(pdf.content),'sha256':sha256(pdf.content).hexdigest()}
        passed(kind+' save/reopen/409/preflight/private PDF')
    item=project('three-side-seal')
    if 'upload_asset' not in state:
        raw_path=LOCAL/'cloud-direct-upload.png'
        if not raw_path.exists():
            stream=BytesIO();Image.frombytes('RGB',(1400,1100),os.urandom(1400*1100*3)).save(stream,format='PNG');raw_path.write_bytes(stream.getvalue())
        raw=raw_path.read_bytes();assert 4*1024*1024<len(raw)<20*1024*1024
        upload=request('POST','/assets/uploads',json={'name':'Cloud direct upload QA.png','content_type':'image/png','byte_size':len(raw),'project_id':item['id']})
        put=httpx.put(upload['upload_url'],content=raw,headers=upload['headers'],timeout=90)
        assert put.status_code in {200,201}
        asset=request('POST','/assets/uploads/'+upload['id']+'/complete')
        assert request('POST','/assets/uploads/'+upload['id']+'/complete')['id']==asset['id']
        fetched=client.get('/assets/'+asset['id']+'/content');assert fetched.status_code==200 and fetched.content==raw
        assert httpx.get(ORIGIN+'/api/v1/assets/'+asset['id']+'/content').status_code==401
        # Reusing the quarantine URL must never mutate the finalized asset.
        httpx.put(upload['upload_url'],content=raw,headers={**upload['headers'],'x-upsert':'true'},timeout=90)
        assert sha256(client.get('/assets/'+asset['id']+'/content').content).hexdigest()==sha256(raw).hexdigest()
        state['upload_asset']=asset['id'];report['direct_upload']={'bytes':len(raw),'dimensions':[1400,1100],'sha256':sha256(raw).hexdigest(),'private':True,'repeat_complete_idempotent':True,'final_isolated_from_upload_token':True}
    passed('over-4MiB direct upload validated/private/idempotent/immutable final')
    if 'brand_id' not in state:
        brand=request('POST','/brands',json={'name':'배포 검수 브랜드','colors':['#234537']});state['brand_id']=brand['id'];persist()
    if 'product_id' not in state:
        product_data=request('POST','/products',json={'name':'배포 검수 말차','brand_id':state['brand_id'],'variants':[{'name':'100g','sku':'QA-MATCHA-100','net_quantity':100,'net_unit':'g','barcode':'8801234567893'}]})
        state['product_id']=product_data['id'];state['variant_id']=product_data['variants'][0]['id'];persist()
    binding=request('POST','/projects/'+item['id']+'/bindings/preview',json={'product_variant_id':state['variant_id'],'base_revision':item['base_revision']})
    request('GET','/team');passed('brand/product/variant/binding/team API')


def ai():
    item=project('three-side-seal')
    if 'ai_balance_before' not in report:
        report['ai_balance_before']=request('GET','/credits')['available'];persist()
    previous_asset=None
    for action,prompt in [
        ('image.generate.standard','Create an exquisite premium Korean tea packaging artwork, flat print-ready illustration only, no pouch mockup. Deep forest green and warm ivory, delicate tea leaves with fine botanical ink lines, beautiful tactile paper texture, elegant blank central area for editable typography, refined luxury food brand art direction. No text, no logos, no watermark.'),
        ('image.edit.standard','Keep the elegant botanical composition and empty central typography area. Refine the leaves into richer emerald shades, add subtle restrained gold ink details, warm ivory paper and softer editorial lighting. Flat packaging artwork, no text, no logos, no watermark.')]:
        key=action.replace('.','-');entry=state['jobs'].setdefault(key,{'operation':state['run']+'-'+key});persist()
        if 'quote_id' not in entry:
            body={'project_id':item['id'],'base_revision':item['base_revision'],'action':action,'requested_units':1,'prompt':prompt,'face_id':'front'}
            if previous_asset:body['reference_asset_id']=previous_asset
            quote=request('POST','/quotes',json=body);entry['quote_id']=quote['id'];persist()
        if 'job_id' not in entry:
            job=request('POST','/jobs',headers={'Idempotency-Key':entry['operation']},json={'quote_id':entry['quote_id']});entry['job_id']=job['id'];persist()
        final=wait_job(entry['job_id'])
        assert final['credit_charged']==10 and final['credit_reserved']==0 and final['credit_returned']==0
        asset=final['result']['assets'][0];previous_asset=asset['id']
        data=client.get('/assets/'+asset['id']+'/content');assert data.status_code==200
        output=LOCAL/('cloud-'+key+'.png');output.write_bytes(data.content)
        with Image.open(output) as generated:
            assert generated.size==(1024,1024);generated.verify()
        replay=request('POST','/jobs',headers={'Idempotency-Key':entry['operation']},json={'quote_id':entry['quote_id']});assert replay['id']==entry['job_id']
        report[key]={'job_id':entry['job_id'],'asset_id':asset['id'],'dimensions':[1024,1024],'bytes':len(data.content),'sha256':sha256(data.content).hexdigest(),'credit_charged':10}
        passed(action+' real image/private asset/one charge/replay')
    report['ai_balance_after']=request('GET','/credits')['available']
    assert report['ai_balance_before']-report['ai_balance_after']==20
    original=report['image-generate-standard']
    assert sha256(client.get('/assets/'+original['asset_id']+'/content').content).hexdigest()==original['sha256']
    if 'ai_applied_asset' not in state:
        current=request('GET','/projects/'+item['id']);scene=current['scene'];face=scene['faces'][0]
        text_before=[obj['text'] for obj in face['objects'] if obj['type']=='text']
        face['objects']=[obj for obj in face['objects'] if obj['id']!='cloud-ai-background']
        face['objects'].insert(0,{'id':'cloud-ai-background','type':'image','face_id':face['id'],'asset_id':previous_asset,'x_mm':0,'y_mm':0,'width_mm':face['width_mm'],'height_mm':face['height_mm'],'rotation_deg':0,'z_index':-100,'visible':True,'print_enabled':True,'locked':True})
        saved=request('PATCH','/projects/'+item['id']+'/draft',json={'base_revision':current['base_revision'],'scene':scene})
        reopened=request('GET','/projects/'+item['id'])
        assert [obj['text'] for obj in reopened['scene']['faces'][0]['objects'] if obj['type']=='text']==text_before
        state['ai_applied_asset']=previous_asset;persist()
    if 'ai_review_job' not in state:
        current=request('GET','/projects/'+item['id'])
        created=request('POST','/exports',headers={'Idempotency-Key':state['run']+'-ai-review'},json={'project_id':item['id'],'base_revision':current['base_revision'],'kind':'review'})
        state['ai_review_job']=created['id'];persist()
    wait_job(state['ai_review_job'])
    final_pdf=client.get('/exports/'+state['ai_review_job']+'/download');assert final_pdf.status_code==200
    reader=PdfReader(BytesIO(final_pdf.content));assert len(reader.pages)==2 and len(reader.pages[0].images)>=1
    assert '높은 단백질 함량' in reader.pages[0].extract_text()
    (LOCAL/'cloud-real-ai-review.pdf').write_bytes(final_pdf.content)
    report['ai_review']={'job_id':state['ai_review_job'],'pdf_bytes':len(final_pdf.content),'image_embedded':True,'editable_korean_preserved':True}
    passed('real AI result applied/reopened/exported with editable Korean preserved')
    passed('real generate+edit debit exactly20, originals preserved')


try:
    auth=attach_session(client,args.session_file,ORIGIN)
    if state.get('user_id') and state['user_id']!=auth['user']['id']:
        raise RuntimeError('This QA state belongs to another Google account')
    state['user_id']=auth['user']['id'];persist()
    ai() if args.ai else core()
    print('Platform integration checks complete.',flush=True)
except Exception as exc:
    persist()
    # HTTP libraries may include private signed URLs in exception text.
    print('Platform smoke stopped: '+(str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__),flush=True)
    raise SystemExit(1)
finally:client.close()
