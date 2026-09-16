"""Real deployment smoke test in an explicitly selected Google QA workspace."""
from pathlib import Path
import json
import argparse
import time
import httpx
from pypdf import PdfReader
from smoke_session import attach_session

root = Path(__file__).resolve().parents[1]
local = root / '.local'
local.mkdir(exist_ok=True)
origin = 'https://phoenix-packaging.vercel.app'
client = httpx.Client(base_url=origin+'/api/v1', headers={'Origin':origin}, timeout=60, follow_redirects=True)
parser = argparse.ArgumentParser()
parser.add_argument('--session-file', type=Path, default=local/'google-smoke-session.json')
args = parser.parse_args()
attach_session(client, args.session_file, origin)
print('PASS authenticated server session', flush=True)
response = client.post('/projects', json={'name':'배포 검수 '+time.strftime('%H:%M'), 'product_name':'제주 말차 그래놀라','brand_name':'피닉스 푸드','width_mm':230,'height_mm':310,'template_id':'three-side-seal'})
response.raise_for_status()
project = response.json()['data']
scene = project['scene']
edited = '높은 단백질 함량\n한글 · English 123'
scene['faces'][0]['objects'][1]['text'] = edited
scene['faces'][0]['objects'][1]['height_mm'] = 40
payload = {'base_revision':project['base_revision'],'scene':scene}
response = client.patch(f'/projects/{project["id"]}/draft', json=payload)
response.raise_for_status()
saved = response.json()['data']
assert client.patch(f'/projects/{project["id"]}/draft', json=payload).status_code == 409
reopened = client.get(f'/projects/{project["id"]}').json()['data']
assert reopened['scene']['faces'][0]['objects'][1]['text'] == edited
print('PASS Korean save, reopen and stale revision conflict', flush=True)
response = client.post('/exports', json={'project_id':project['id'],'base_revision':saved['base_revision']})
response.raise_for_status()
job = response.json()['data']
for _ in range(35):
    status = client.get(f'/jobs/{job["id"]}').json()['data']
    if status['status'] in ['succeeded','failed']:
        break
    time.sleep(2)
assert status['status'] == 'succeeded', f'Export state: {status["status"]}; {status.get("error")}'
pdf = client.get(f'/exports/{job["id"]}/download')
pdf.raise_for_status()
assert pdf.content.startswith(b'%PDF-')
output = local / 'cloud-review.pdf'
output.write_bytes(pdf.content)
reader = PdfReader(output)
assert len(reader.pages) == 2
for page in reader.pages:
    assert abs(float(page.trimbox.width)*25.4/72-230)<.01
    assert abs(float(page.trimbox.height)*25.4/72-310)<.01
assert '높은 단백질 함량' in reader.pages[0].extract_text()
print('PASS durable queue, private PDF download, Korean text, 2 pages at 230x310mm', flush=True)
result = {'origin':origin,'project_id':project['id'],'job_id':job['id'],'pdf_bytes':len(pdf.content),'pages':len(reader.pages),'checks':['auth','server-save','reopen','409-conflict','queued-export','private-download','korean-text','exact-size']}
(local / 'cloud-smoke-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False), flush=True)
