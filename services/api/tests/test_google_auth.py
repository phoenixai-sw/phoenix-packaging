from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from google.auth import crypt, jwt
import json
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from services.api.auth import hash_token
from services.api.config import Settings
from services.api.database import utcnow
from services.api.errors import APIError
from services.api.google_auth import CHALLENGE_COOKIE, google_verifier, verify_google_signature
from services.api.main import create_app
from services.api.models import AuthAttempt, GoogleLoginChallenge, LoginSession, Tenant, User
from services.api.tests.auth_helpers import TEST_CLIENT_ID, google_login, register


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(environment="test", database_url=f"sqlite:///{tmp_path/'google.db'}", storage_dir=tmp_path/'storage', google_client_id=TEST_CLIENT_ID, admin_emails=()))


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def test_missing_configuration_and_retired_endpoints_fail_closed(client, app):
    app.state.settings.google_client_id = ""
    assert client.get('/v1/auth/google/challenge').status_code == 503
    assert client.post('/v1/auth/google', json={'credential':'x'*30, 'csrf_token':'x'*32}).status_code == 503
    for path in ('register','login','request-verification','verify-email','request-password-reset','reset-password'):
        result = client.post('/v1/auth/'+path, json={'email':'nobody@example.com','password':'ignored-secret'})
        assert result.status_code == 410 and result.json()['code'] == 'GOOGLE_LOGIN_REQUIRED'
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(GoogleLoginChallenge)) == 0


def test_challenge_csrf_nonce_expiry_and_origin(client, app):
    challenge_response = client.get('/v1/auth/google/challenge')
    assert 'no-store' in challenge_response.headers['cache-control']
    assert 'httponly' in challenge_response.headers['set-cookie'].lower()
    challenge = challenge_response.json()['data']
    raw = client.cookies.get(CHALLENGE_COOKIE)
    with app.state.session_factory() as db:
        row = db.get(GoogleLoginChallenge, hash_token(raw))
        assert row.nonce_hash != challenge['nonce']
        assert row.token_hash != raw
    body = {'credential':'x'*30,'csrf_token':challenge['csrf_token']}
    assert client.post('/v1/auth/google', json={**body,'csrf_token':'y'*32}).status_code == 403
    assert client.post('/v1/auth/google', headers={'Origin':'https://hostile.example'}, json=body).status_code == 403
    with TestClient(app) as other:
        assert other.post('/v1/auth/google',json=body).status_code == 403
    with app.state.session_factory() as db:
        db.get(GoogleLoginChallenge,hash_token(raw)).expires_at=utcnow()-timedelta(seconds=1);db.commit()
    assert client.post('/v1/auth/google',json=body).status_code == 403
    assert google_login(client,claims={'nonce':'wrong-request'}).json()['code']=='GOOGLE_NONCE_INVALID'


@pytest.mark.parametrize('override', [
    {'aud':'another-client'}, {'iss':'https://attacker.example'}, {'azp':'other-client'},
    {'exp':1}, {'exp':True}, {'iat':9999999999}, {'email_verified':False}, {'email_verified':'true'},
    {'sub':''}, {'sub':'x'*256}, {'email':'not-an-email'}, {'nonce':'다른 요청'},
])
def test_server_rejects_invalid_claims_even_after_signature_dependency(client, override):
    assert google_login(client,claims=override).status_code==401
    assert client.get('/v1/me').status_code==401


def test_cookie_rotation_sub_identity_allowlist_and_trial_once(client, app):
    first=register(client,'Owner@Gmail.com')
    cookie=client.cookies.get('phoenix_session')
    assert first['user']['email']=='owner@gmail.com' and first['user']['email_verified']
    assert first['user']['is_admin'] is False
    assert client.get('/v1/credits').json()['data']['balance']==30
    second=register(client,'owner@gmail.com')
    assert second['user']['id']==first['user']['id'] and second['tenant']==first['tenant']
    assert client.cookies.get('phoenix_session') != cookie
    assert client.get('/v1/credits').json()['data']['balance']==30
    with app.state.session_factory() as db:
        user=db.get(User,first['user']['id']);user.is_admin=True;db.commit()
    assert client.get('/v1/admin/overview').status_code==403  # stale DB flag grants nothing
    app.state.settings.admin_emails=('OWNER@GMAIL.COM',)
    assert client.get('/v1/me').json()['data']['user']['is_admin'] is True
    assert client.get('/v1/admin/overview').status_code==200
    app.state.settings.admin_emails=()
    assert client.get('/v1/admin/overview').status_code==403
    with TestClient(app) as old:
        old.cookies.set('phoenix_session',cookie)
        assert old.get('/v1/me').status_code==401


def test_legacy_gmail_links_without_changing_role_and_revokes_sessions(client, app):
    with app.state.session_factory() as db:
        tenant=Tenant(name='Legacy team');db.add(tenant);db.flush()
        user=User(name='Old name',email='legacy@gmail.com',password_hash='legacy-hash',tenant_id=tenant.id,role='owner')
        db.add(user);db.flush();identity=user.id
        db.add(LoginSession(token_hash=hash_token('legacy-cookie'),csrf_token='old-csrf',user_id=user.id,expires_at=utcnow()+timedelta(days=1)));db.commit()
    client.cookies.set('phoenix_session','legacy-cookie')
    assert client.get('/v1/me').status_code==401
    data=google_login(client,'legacy@gmail.com',claims={'hd':None}).json()['data']
    assert data['user']['id']==identity and data['user']['role']=='owner'
    with app.state.session_factory() as db:
        assert db.get(User,identity).password_hash==''
        assert db.scalar(select(LoginSession).where(LoginSession.token_hash==hash_token('legacy-cookie'))) is None


def test_google_sub_takes_precedence_email_conflicts_fail_closed(client,app):
    first=register(client,'first@gmail.com')
    register(client,'second@gmail.com')
    original_sub=sha256(b'first@gmail.com').hexdigest()
    assert google_login(client,'second@gmail.com',sub=original_sub).status_code==409
    assert google_login(client,'first@gmail.com',sub='anotherGoogleSubject').status_code==409
    renamed=google_login(client,'renamed@gmail.com',sub=original_sub)
    assert renamed.status_code==200
    assert renamed.json()['data']['user']['id']==first['user']['id']
    with app.state.session_factory() as db:
        original=db.get(User,first['user']['id'])
        db.add(User(tenant_id=original.tenant_id,name='collision',email='collision@gmail.com',google_sub=original_sub))
        with pytest.raises(IntegrityError): db.commit()


def test_external_email_legacy_link_and_admin_grants_are_not_automatic(client,app):
    with app.state.session_factory() as db:
        tenant=Tenant(name='Legacy');db.add(tenant);db.flush()
        db.add(User(name='Legacy',email='legacy@example.com',tenant_id=tenant.id,password_hash='old'));db.commit()
    assert google_login(client,'legacy@example.com',claims={'hd':None}).status_code==409
    app.state.settings.admin_emails=('external@example.com',)
    response=google_login(client,'external@example.com',claims={'hd':None})
    assert response.status_code==200  # ordinary Google accounts may still create their own workspace
    assert response.json()['data']['user']['is_admin'] is False


def test_claimed_challenge_cannot_be_replayed(client,app):
    value=client.get('/v1/auth/google/challenge').json()['data']
    raw=client.cookies.get(CHALLENGE_COOKIE)
    now=int(utcnow().timestamp())
    claims={'aud':TEST_CLIENT_ID,'iss':'https://accounts.google.com','exp':now+3600,'iat':now,'sub':'1234','email':'replay@gmail.com','email_verified':True,'nonce':value['nonce']}
    app.dependency_overrides[google_verifier]=lambda:lambda *_:claims
    body={'credential':'TEST_ONLY_GOOGLE_CREDENTIAL','csrf_token':value['csrf_token']}
    assert client.post('/v1/auth/google',json=body).status_code==200
    with app.state.session_factory() as db:
        row=db.scalar(select(GoogleLoginChallenge));assert row.consumed_at is not None
    assert CHALLENGE_COOKIE not in client.cookies
    client.cookies.set(CHALLENGE_COOKIE,raw)
    assert client.post('/v1/auth/google',json=body).status_code==403


def test_challenge_rate_limit_is_durable_and_inactive_account_cannot_login(client,app):
    data=register(client)
    with app.state.session_factory() as db:
        db.get(User,data['user']['id']).is_active=False;db.commit()
    assert google_login(client).status_code==403
    assert client.get('/v1/me').status_code==401
    with app.state.session_factory() as db:
        db.add_all([AuthAttempt(key_hash=hash_token('auth:google-challenge-peer:testclient')) for _ in range(120)]);db.commit()
    assert client.get('/v1/auth/google/challenge').status_code==429
    with TestClient(create_app(app.state.settings)) as restarted:
        assert restarted.get('/v1/auth/google/challenge').status_code==429


def test_official_verifier_checks_real_rsa_signature_audience_issuer_and_expiry(monkeypatch):
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    private_pem=private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    public_pem=private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    class Certificates:
        def __call__(self,url,**kwargs):
            assert url.startswith('https://www.googleapis.com/')
            return SimpleNamespace(status=200,data=json.dumps({'test-key':public_pem.decode()}).encode())
    monkeypatch.setattr('services.api.google_auth.BoundedGoogleRequest',Certificates)
    signer=crypt.RSASigner.from_string(private_pem,key_id='test-key')
    now=int(utcnow().timestamp())
    claims={'iss':'https://accounts.google.com','aud':TEST_CLIENT_ID,'sub':'123456','exp':now+3600,'iat':now,'email':'owner@gmail.com','email_verified':True}
    def token(payload):return jwt.encode(signer,payload).decode()
    assert verify_google_signature(token(claims),TEST_CLIENT_ID)['sub']=='123456'
    for payload in ({**claims,'aud':'wrong'}, {**claims,'iss':'https://evil.example'}, {**claims,'exp':now-1,'iat':now-100}):
        with pytest.raises(APIError) as error: verify_google_signature(token(payload),TEST_CLIENT_ID)
        assert error.value.status==401
    encoded=token(claims);parts=encoded.split('.');parts[1]=parts[1][:-2]+'xx'
    with pytest.raises(APIError):verify_google_signature('.'.join(parts),TEST_CLIENT_ID)
