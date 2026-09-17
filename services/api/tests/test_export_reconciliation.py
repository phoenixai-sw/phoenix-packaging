"""Actual published artifacts; local storage only, no paid regeneration."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json
import httpx
import pytest
from sqlalchemy import select,func
from services.api import export_reconciliation as integrity
from services.api.database import utcnow
from services.api.models import Job,Project,User
from services.api.feature_models import AuditEvent
from services.api.billing.models import LedgerEntry,ProductionEntitlement,Reservation
from services.api.billing.service import create_quote,reserve
from services.api.errors import APIError
from services.api.jobs import process_pending_jobs
from services.api.editable_exports import process_editable_jobs
from services.api.production_jobs import process_production_jobs
from services.api.production_routes import prepare_production_quote
from services.api.tests.test_api import app,client,register,project
from tests.geometry_pdf.test_production_worker import setup,enqueue,payload,revision


def completed(client,app,kind='review'):
    register(client);item=project(client)
    if kind=='print_engine':
        from services.api.print_engine import BUILTIN_ID
        result=client.post('/v1/print-engine/tests',json={'project_id':item['id'],'base_revision':1,'profile_id':BUILTIN_ID})
    else:result=client.post('/v1/exports',json={'project_id':item['id'],'base_revision':1,'kind':kind})
    assert result.status_code==202,result.text
    job_id=result.json()['data']['id']
    assert (process_editable_jobs if kind=='editable' else process_pending_jobs)(app.state.session_factory,app.state.storage)==1
    with app.state.session_factory() as db:
        row=db.get(Job,job_id);return row.id,row.result['storage_key'],deepcopy(row.snapshot),deepcopy(row.result)


@pytest.mark.parametrize('kind',['review','editable','print_engine'])
@pytest.mark.parametrize('failure',['missing','corrupt'])
def test_download_blocks_bad_file_then_free_retry_uses_original_snapshot(client,app,kind,failure):
    identity,key,snapshot,result=completed(client,app,kind)
    path=app.state.storage.path(key)
    if failure=='missing':path.unlink()
    else:path.write_bytes(b'corrupt export')
    response=client.get(f'/v1/exports/{identity}/download')
    assert response.status_code==409 and response.json()['code']=='EXPORT_INTEGRITY_PENDING'
    status=client.get(f'/v1/jobs/{identity}').json()['data']
    assert status['status']=='succeeded' and status['availability']['status']=='suspect' and status['download_url'] is None
    assert '_integrity' not in status['result'] and 'storage_key' not in status['result']
    now=utcnow()+integrity.PROBE_INTERVAL+timedelta(seconds=1)
    assert integrity.reconcile_exports(app.state.session_factory,app.state.storage,now=now)==1
    with app.state.session_factory() as db:
        row=db.get(Job,identity);assert row.status=='failed' and row.snapshot==snapshot
        assert {k:v for k,v in row.result.items() if k!='_integrity'}==result
    assert client.get(f'/v1/exports/{identity}/download').status_code==410
    retried=client.post(f'/v1/jobs/{identity}/retry');assert retried.status_code==202,retried.text
    assert (process_editable_jobs if kind=='editable' else process_pending_jobs)(app.state.session_factory,app.state.storage)==1
    assert client.get(f'/v1/exports/{identity}/download').status_code==200
    with app.state.session_factory() as db:
        row=db.get(Job,identity);assert row.result['credits_charged']==0
        assert row.snapshot==snapshot and row.result['storage_key']!=key


@pytest.mark.parametrize('error',['timeout','permission','ambiguous404'])
def test_storage_uncertainty_clears_confirmation_and_does_not_refund(setup,error):
    s=setup;identity,_,_=enqueue(s);process_production_jobs(s.factory,s.storage,s.settings)
    with s.factory() as db:key=db.get(Job,identity).result['storage_key']
    s.storage.path(key).unlink();now=utcnow()+integrity.PROBE_INTERVAL
    integrity.reconcile_exports(s.factory,s.storage,now=now)
    class Ambiguous:
        def get_limited(self,*_):
            if error=='timeout':raise httpx.ReadTimeout('transient')
            request=httpx.Request('GET','https://example.invalid/private')
            response=httpx.Response(403 if error=='permission' else 404,json={'code':'NoSuchKey'},request=request)
            response.raise_for_status()
        def confirm_missing(self,_):return False
    integrity.reconcile_exports(s.factory,Ambiguous(),now=now+integrity.PROBE_INTERVAL)
    integrity.reconcile_exports(s.factory,s.storage,now=now+2*integrity.PROBE_INTERVAL)
    with s.factory() as db:
        assert db.get(Job,identity).status=='succeeded'
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event=='COMPENSATE'))==0
        assert db.scalar(select(ProductionEntitlement)).status=='entitled'


def test_paid_loss_compensates_once_new_quote_charges_and_old_free_job_is_blocked(setup):
    s=setup;paid,_,_=enqueue(s);process_production_jobs(s.factory,s.storage,s.settings)
    free,_,_=enqueue(s) # Already-reserved free repeat must not evade compensation.
    with s.factory() as db:
        row=db.get(Job,paid);key=row.result['storage_key'];snapshot=deepcopy(row.snapshot);result=deepcopy(row.result)
        user=db.get(User,s.user_id);item=db.get(Project,s.project_id);db.info['principal']=user
        values=prepare_production_quote(db,user,{'project_id':item.id,'base_revision':item.base_revision,'reviewed_face_ids':['front','back']},s.settings,payload,revision,s.storage)
        old_quote=create_quote(db,s.tenant_id,**values);assert old_quote.unit_cost==0;old_quote_id=old_quote.id;db.commit()
    s.storage.path(key).unlink();now=utcnow()+integrity.PROBE_INTERVAL
    def parallel(at):
        with ThreadPoolExecutor(max_workers=2) as pool:return sum(pool.map(lambda _:integrity.reconcile_exports(s.factory,s.storage,now=at),range(2)))
    assert parallel(now)==1
    assert parallel(now+integrity.PROBE_INTERVAL)==1
    with s.factory() as db:
        row=db.get(Job,paid);assert row.status=='unavailable' and row.snapshot==snapshot
        assert {k:v for k,v in row.result.items() if k!='_integrity'}==result
        assert row.result['_integrity']['credit_restored']==40
        assert db.scalar(select(ProductionEntitlement)).status=='compensated'
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event=='COMPENSATE'))==1
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action=='export_loss_confirmed'))==1
        from services.api.billing.models import Quote
        old=db.get(Quote,old_quote_id)
        with pytest.raises(APIError) as exc:reserve(db,s.tenant_id,'stale-free','export.production.first',1,quote_id=old.id,project_id=old.project_id,base_revision=old.base_revision,input_data=old.input_data,now=old.created_at+timedelta(seconds=1))
        assert exc.value.code=='QUOTE_CHANGED';db.rollback()
    assert process_production_jobs(s.factory,s.storage,s.settings)==1
    with s.factory() as db:
        row=db.get(Job,free);assert row.status=='failed' and row.result is None
        assert db.get(Reservation,row.snapshot['reservation_id']).status=='released'
    again,_,_=enqueue(s);assert process_production_jobs(s.factory,s.storage,s.settings)==1
    with s.factory() as db:
        assert db.get(Job,again).result['credits_charged']==40
        assert db.scalar(select(ProductionEntitlement)).status=='entitled'
    assert integrity.reconcile_exports(s.factory,s.storage,now=now+2*integrity.PROBE_INTERVAL)==1 # new artifact checked, never old compensated job
    with s.factory() as db:assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event=='COMPENSATE'))==1


def test_lost_free_repeat_does_not_refund_or_remove_original_entitlement(setup):
    s=setup;paid,_,_=enqueue(s);process_production_jobs(s.factory,s.storage,s.settings)
    free,_,_=enqueue(s);process_production_jobs(s.factory,s.storage,s.settings)
    with s.factory() as db:key=db.get(Job,free).result['storage_key']
    s.storage.path(key).write_bytes(b'bad free zip');now=utcnow()+integrity.PROBE_INTERVAL
    integrity.reconcile_exports(s.factory,s.storage,now=now,limit=5)
    integrity.reconcile_exports(s.factory,s.storage,now=now+integrity.PROBE_INTERVAL,limit=5)
    with s.factory() as db:
        assert db.get(Job,free).status=='unavailable' and db.get(Job,paid).status=='succeeded'
        assert db.get(Job,free).result['_integrity']['credit_restored']==0
        assert db.scalar(select(ProductionEntitlement)).status=='entitled'
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event=='COMPENSATE'))==0
    again,_,_=enqueue(s);process_production_jobs(s.factory,s.storage,s.settings)
    with s.factory() as db:assert db.get(Job,again).result['credits_charged']==0


def test_intent_baseline_old_review_and_healthy_file_restore_clears_suspicion(client,app):
    identity,key,_,result=completed(client,app);raw=app.state.storage.get(key)
    with app.state.session_factory() as db:completed_at=db.get(Job,identity).updated_at
    app.state.storage.path(key).unlink();assert client.get(f'/v1/exports/{identity}/download').status_code==409
    app.state.storage.put(key,raw,'application/pdf')
    assert client.get(f'/v1/exports/{identity}/download').content==raw
    value=client.get(f'/v1/jobs/{identity}').json()['data']
    assert value['availability']['status']=='available' and value['download_url']
    with app.state.session_factory() as db:assert db.get(Job,identity).updated_at==completed_at
    now=utcnow()+timedelta(minutes=6)
    assert integrity.reconcile_exports(app.state.session_factory,app.state.storage,now=now)==0


def test_retention_deletion_is_not_loss_or_compensation(setup):
    s=setup;identity,_,_=enqueue(s);process_production_jobs(s.factory,s.storage,s.settings)
    with s.factory() as db:
        job=db.get(Job,identity);key=job.result['storage_key'];job.result={**job.result,'_retention':{'state':'deleted','request_id':'fixture'}};db.commit()
    s.storage.path(key).unlink()
    for step in (1,2):assert integrity.reconcile_exports(s.factory,s.storage,now=utcnow()+step*integrity.PROBE_INTERVAL)==0
    with s.factory() as db:
        assert db.scalar(select(ProductionEntitlement)).status=='entitled'
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event=='COMPENSATE'))==0
        with pytest.raises(APIError) as exc:integrity.check_export_download(db,db.get(Job,identity),s.storage)
        assert exc.value.code=='FILE_DELETED_BY_REQUEST'


def test_cross_tenant_download_denied_before_storage_probe(client,app,monkeypatch):
    from fastapi.testclient import TestClient
    identity,_,_,_=completed(client,app)
    monkeypatch.setattr(app.state.storage,'get_limited',lambda *_:pytest.fail('No unauthorized storage read'))
    with TestClient(app) as other:
        register(other,'other-owner@example.com')
        assert other.get(f'/v1/exports/{identity}/download').status_code==404


def test_cloud_404_needs_independent_confirmation():
    class Cloud:
        def __init__(self,confirm):self.confirm=confirm
        def get_limited(self,*_):httpx.Response(404,json={'code':'NoSuchKey'},request=httpx.Request('GET','https://example.invalid')).raise_for_status()
        def confirm_missing(self,_):return self.confirm
    expected=('tenant/file','a'*64,123)
    assert integrity._probe(Cloud(False),expected)[:2]==('uncertain','storage_http_error')
    assert integrity._probe(Cloud(True),expected)[:2]==('missing','object_absent')


def test_free_publication_acquires_wallet_before_project_and_entitlement(setup):
    from sqlalchemy import event
    from services.api.production_jobs import _current_conditions
    s=setup;enqueue(s);process_production_jobs(s.factory,s.storage,s.settings)
    free,_,_=enqueue(s)
    with s.factory() as db:snapshot=deepcopy(db.get(Job,free).snapshot)
    with s.factory() as db:
        engine=db.get_bind();statements=[]
        def record(_,__,statement,___,____,_____):statements.append(statement.lower())
        event.listen(engine,'before_cursor_execute',record)
        try:_current_conditions(db,snapshot,s.tenant_id,lock=True)
        finally:event.remove(engine,'before_cursor_execute',record)
    first=lambda prefix:next(i for i,text in enumerate(statements) if text.startswith(prefix))
    tenant=first('update tenants ');wallet=first('update credit_wallets ')
    project_read=next(i for i,text in enumerate(statements) if text.startswith('select ') and 'from projects ' in text.replace('\n',' '))
    entitlement_read=next(i for i,text in enumerate(statements) if text.startswith('select ') and 'from production_entitlements ' in text.replace('\n',' '))
    assert tenant<wallet<project_read<entitlement_read


def test_compensation_after_free_zip_write_still_prevents_publication(setup,monkeypatch):
    s=setup;paid,_,_=enqueue(s);process_production_jobs(s.factory,s.storage,s.settings)
    free,_,_=enqueue(s)
    with s.factory() as db:paid_key=db.get(Job,paid).result['storage_key']
    put=s.storage.put
    def compensate_before_publication(key,raw,content_type):
        put(key,raw,content_type)
        s.storage.path(paid_key).unlink()
        now=utcnow()+integrity.PROBE_INTERVAL
        assert integrity.reconcile_exports(s.factory,s.storage,now=now)==1
        assert integrity.reconcile_exports(s.factory,s.storage,now=now+integrity.PROBE_INTERVAL)==1
    monkeypatch.setattr(s.storage,'put',compensate_before_publication)
    assert process_production_jobs(s.factory,s.storage,s.settings)==1
    with s.factory() as db:
        row=db.get(Job,free)
        assert row.status=='failed' and row.result is None
        assert db.get(Reservation,row.snapshot['reservation_id']).status=='released'
        assert db.scalar(select(ProductionEntitlement)).status=='compensated'
        from services.api.retention.models import StorageIntent
        assert db.scalar(select(StorageIntent).where(StorageIntent.job_id==free)).status=='planned'


def test_download_releases_auth_wallet_transaction_before_storage(client,app,monkeypatch):
    identity,_,_,_=completed(client,app)
    with app.state.session_factory() as db:
        row=db.get(Job,identity);integrity.lock_wallet(db,row.tenant_id)
        get=app.state.storage.get_limited
        def read_without_db_transaction(*args):
            assert not db.in_transaction()
            return get(*args)
        monkeypatch.setattr(app.state.storage,'get_limited',read_without_db_transaction)
        assert integrity.check_export_download(db,row,app.state.storage).startswith(b'%PDF')


@pytest.mark.parametrize('failure',['overdue','disabled'])
def test_production_failure_cleanup_uses_tenant_wallet_before_job_lock(setup,failure):
    from sqlalchemy import event
    s=setup;identity,_,_=enqueue(s)
    with s.factory() as db:
        if failure=='overdue':db.get(Job,identity).created_at=utcnow()-timedelta(minutes=36);db.commit()
        engine=db.get_bind()
    if failure=='disabled':s.settings.enable_production_export=False
    transactions=[]
    def begin(_):transactions.append([])
    def record(_,__,statement,parameters,____,_____):transactions[-1].append((statement.lower(),parameters))
    event.listen(engine,'begin',begin);event.listen(engine,'before_cursor_execute',record)
    try:process_production_jobs(s.factory,s.storage,s.settings)
    finally:event.remove(engine,'begin',begin);event.remove(engine,'before_cursor_execute',record)
    cleanup=[group for group in transactions if any(text.startswith('update jobs ') and 'failed' in parameters for text,parameters in group)]
    assert len(cleanup)==1
    statements=[text.replace('\n',' ') for text,_ in cleanup[0]]
    tenant=next(i for i,text in enumerate(statements) if text.startswith('update tenants '))
    wallet=next(i for i,text in enumerate(statements) if text.startswith('update credit_wallets '))
    first_job=next(i for i,text in enumerate(statements) if text.startswith('update jobs ') or 'from jobs ' in text)
    assert tenant<wallet<first_job
    with s.factory() as db:
        row=db.get(Job,identity);assert row.status=='failed'
        assert db.get(Reservation,row.snapshot['reservation_id']).status=='released'


def test_expired_paid_allocation_restored_once_in_original_scope_for_seven_days(setup):
    from services.api.billing.models import Allocation,CreditBucket
    from services.api.billing.policy import aware
    s=setup;identity,_,_=enqueue(s);process_production_jobs(s.factory,s.storage,s.settings)
    now=utcnow()+integrity.PROBE_INTERVAL
    with s.factory() as db:
        row=db.get(Job,identity);key=row.result['storage_key']
        allocation=db.scalar(select(Allocation).where(Allocation.reservation_id==row.snapshot['reservation_id']))
        source=db.get(CreditBucket,allocation.bucket_id);source.expires_at=now-timedelta(days=1)
        source_id,scope=source.id,source.scope;db.commit()
    s.storage.path(key).unlink()
    for step in (0,1,2):integrity.reconcile_exports(s.factory,s.storage,now=now+step*integrity.PROBE_INTERVAL)
    with s.factory() as db:
        restored=list(db.scalars(select(CreditBucket).where(CreditBucket.source_bucket_id==source_id,CreditBucket.kind=='compensation')))
        assert len(restored)==1 and restored[0].granted==40 and restored[0].available==40
        assert restored[0].scope==scope=='paid'
        assert aware(restored[0].expires_at)==now+integrity.PROBE_INTERVAL+timedelta(days=7)
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event=='COMPENSATE'))==1


def test_legacy_without_hash_never_receives_false_loss_refund(client,app):
    from services.api.retention.models import StorageIntent
    identity,key,_,_=completed(client,app)
    with app.state.session_factory() as db:
        row=db.get(Job,identity);result=deepcopy(row.result);result['manifest'].pop('sha256');row.result=result
        db.delete(db.scalar(select(StorageIntent).where(StorageIntent.job_id==identity)));db.commit()
    app.state.storage.path(key).unlink();now=utcnow()+integrity.PROBE_INTERVAL
    for step in (0,1):integrity.reconcile_exports(app.state.session_factory,app.state.storage,now=now+step*integrity.PROBE_INTERVAL)
    with app.state.session_factory() as db:
        row=db.get(Job,identity);assert row.status=='succeeded' and integrity.export_availability(row)['status']=='temporarily_unverified'


def test_backup_restores_loss_evidence_without_missing_or_corrupt_bytes(tmp_path,monkeypatch,client,app):
    from services.api.tests.test_backup_restore import backup_script,verify_restored_app
    identity,key,snapshot,result=completed(client,app)
    app.state.storage.path(key).write_bytes(b'definitively corrupt')
    now=utcnow()+integrity.PROBE_INTERVAL
    integrity.reconcile_exports(app.state.session_factory,app.state.storage,now=now)
    integrity.reconcile_exports(app.state.session_factory,app.state.storage,now=now+integrity.PROBE_INTERVAL)
    monkeypatch.setenv('APP_ENV','test');monkeypatch.setenv('DATABASE_URL',app.state.settings.database_url)
    monkeypatch.setenv('STORAGE_BACKEND','local');monkeypatch.setenv('STORAGE_DIR',str(app.state.settings.storage_dir))
    output=tmp_path/'backup';keyfile=tmp_path/'key';restored=tmp_path/'restored'
    report=backup_script.backup(output,keyfile)
    assert report['known_unavailable_exports']==1 and report['objects_verified']==0
    backup_script.verify(output,keyfile,restored)
    creds=tmp_path/'selector.json';creds.write_text(json.dumps({'email':'owner@example.com'}),encoding='utf-8')
    reopened=verify_restored_app(restored,creds)
    assert reopened['known_unavailable_exports']==1 and reopened['export_files_reopened']==0
    assert app.state.storage.get(key)==b'definitively corrupt' # no source deletion disguised as recovery
