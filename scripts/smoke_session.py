"""Use an explicitly supplied, existing Google-authenticated session for QA.

This helper never creates accounts or bypasses Google verification. The local
session file is a secret and must stay outside Git. An expired session requires
a new real Google sign-in; there is no password or synthetic hosted fallback.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit


def attach_session(client, path, origin):
    path = Path(path)
    if not path.is_file():
        raise RuntimeError('A local Google-authenticated QA session file is required; password login is disabled')
    record = json.loads(path.read_text(encoding='utf-8-sig'))
    if record.get('origin') != origin or not isinstance(record.get('session'), str) or not record['session']:
        raise RuntimeError('QA session origin or cookie is invalid')
    target = urlsplit(origin)
    if target.scheme != 'https' and target.hostname not in {'localhost', '127.0.0.1'}:
        raise RuntimeError('QA session requires HTTPS')
    client.cookies.set('phoenix_session', record['session'], domain=target.hostname, path='/')
    response = client.get('/me')
    if response.status_code != 200:
        raise RuntimeError('QA session expired or is not Google-authenticated; sign in again')
    auth = response.json()['data']
    if auth['user'].get('auth_provider') != 'google':
        raise RuntimeError('A Google-authenticated QA account is required')
    client.headers['X-CSRF-Token'] = auth['csrf_token']
    return auth
