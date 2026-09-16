"""Configure this project's approved Supabase and Vercel resources without logging secrets.

Reads gitignored provisioning files created locally. Never commits credentials.
"""
from pathlib import Path
import json
import subprocess
from urllib.parse import urlparse, quote

import httpx
import psycopg

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local'
def read(name):
    return json.loads((LOCAL / name).read_text(encoding='utf-8-sig'))

private = read('provisioning-secrets.json')
project = read('supabase-project-create.json')
keys = read('supabase-api-keys.json')
service_key = next(x['api_key'] for x in keys if x.get('name') == 'service_role')
pooler = urlparse((ROOT / 'supabase/.temp/pooler-url').read_text().strip())
db_url = f'postgresql://{pooler.username}:{quote(private["database_password"], safe="")}@{pooler.hostname}:6543/postgres?sslmode=require'
supabase_url = f'https://{project["id"]}.supabase.co'

with psycopg.connect(db_url, prepare_threshold=None, connect_timeout=20) as db:
    version = db.execute('select current_database(), version()').fetchone()
    print('Supabase PostgreSQL connection verified:', version[0])

with httpx.Client(timeout=30) as client:
    response = client.post(f'{supabase_url}/storage/v1/bucket', headers={
        'apikey': service_key, 'Authorization': f'Bearer {service_key}'
    }, json={'id': 'phoenix-private', 'name': 'phoenix-private', 'public': False,
             'file_size_limit': 20971520, 'allowed_mime_types': ['image/png','image/jpeg','image/webp','application/pdf','application/json']})
    if response.status_code not in {200, 201, 409} and 'already exists' not in response.text:
        raise RuntimeError(f'Private storage setup failed: HTTP {response.status_code}')
    print('Private Supabase Storage bucket ready.')

api_env = {
    'APP_ENV': 'staging', 'DATABASE_URL': db_url,
    'STORAGE_BACKEND': 'supabase', 'SUPABASE_URL': supabase_url,
    'SUPABASE_SERVICE_ROLE_KEY': service_key, 'SUPABASE_STORAGE_BUCKET': 'phoenix-private',
    'COOKIE_SECURE': 'true', 'DEMO_MODE': 'true',
    'ALLOWED_ORIGINS': 'https://phoenix-packaging.vercel.app',
    'WORKER_SECRET': private['worker_secret'], 'CRON_SECRET': private['worker_secret'],
    'STORAGE_DIR': '/tmp/phoenix-storage', 'UPLOAD_LIMIT_BYTES': '4194304',
}
web_env = {'API_ORIGIN': 'https://phoenix-packaging-api.vercel.app', 'WORKER_SECRET': private['worker_secret']}
(LOCAL / 'cloud-env.json').write_text(json.dumps(api_env, indent=2), encoding='utf-8')
for kind, values in [('api', api_env), ('web', web_env)]:
    target = read(f'vercel-{kind}-created.json')
    payload = [{'key': key, 'value': value, 'type': 'encrypted', 'target': ['production', 'preview']} for key, value in values.items()]
    for variable in payload:
        env_path = LOCAL / f'vercel-{kind}-env.json'
        env_path.write_text(json.dumps(variable), encoding='utf-8')
        result = subprocess.run(['vercel.cmd', 'api', f'/v10/projects/{target["id"]}/env?upsert=true',
            '-X', 'POST', '--scope', 'phoenixs-projects-ea6a0b8e', '--input', str(env_path.relative_to(ROOT)), '--silent'],
            cwd=ROOT, capture_output=True, text=True)
        if result.returncode:
            error = result.stderr + result.stdout
            for value in [service_key, private['database_password'], private['worker_secret'], db_url]:
                error = error.replace(value, '[REDACTED]')
            raise RuntimeError(f'{kind} environment update failed: {error[:2000]}')
    print(f'{kind}: encrypted deployment environment configured ({len(values)} variables).')
