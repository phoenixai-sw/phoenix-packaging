from copy import deepcopy
from datetime import datetime,timezone
from io import StringIO
from pathlib import Path
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine,MetaData,Table,select,text
from sqlalchemy.exc import IntegrityError

CFG=Path(__file__).resolve().parents[1]/'alembic.ini'


def test_0018_preserves_old_quotes_and_orders_and_protects_evidence(tmp_path,monkeypatch):
    url=f"sqlite:///{tmp_path/'service-migration.db'}"
    monkeypatch.setenv('APP_ENV','test');monkeypatch.setenv('DATABASE_URL',url)
    cfg=Config(str(CFG));command.upgrade(cfg,'0017_metrics')
    engine=create_engine(url);meta=MetaData();meta.reflect(engine)
    now=datetime.now(timezone.utc)
    values={
        'tenants':{'id':'tenant','name':'Migration customer','created_at':now},
        'users':{'id':'owner','tenant_id':'tenant','name':'Owner','email':'migration-service@example.com','password_hash':'','role':'owner','is_active':True,'is_admin':False,'google_email_authoritative':True,'created_at':now},
        'service_orders':{'id':'service','tenant_id':'tenant','requested_by':'owner','service_code':'pilot_pro_first_month','request_note':'Old accepted inquiry','catalog_snapshot':{'checkout_enabled':False},'catalog_policy_version':'old','operation_key':'old-service','request_hash':'a'*64,'status':'accepted','revision':3,'current_quote_id':'quote','accepted_quote_id':'quote','created_at':now,'updated_at':now},
        'service_order_quotes':{'id':'quote','tenant_id':'tenant','order_id':'service','number':1,'amount_inc_vat':99000,'currency':'KRW','scope':'Old proposal only','exclusions':'No recurring consent','expires_at':now,'policy_version':'old','quoted_by':'owner','reason':'Legacy terms','created_at':now},
        'payment_orders':{'id':'payment-order','tenant_id':'tenant','order_id':'pp_old','operation_key':'old-order','request_hash':'b'*64,'kind':'topup','amount':42900,'currency':'KRW','credits':500,'pricing_version':'old','status':'pending','credits_expires_at':now,'created_at':now}}
    with engine.begin() as db:
        for name,data in values.items():db.execute(meta.tables[name].insert().values(**data))
        before={name:dict(db.execute(select(meta.tables[name])).mappings().one()) for name in values}
    command.upgrade(cfg,'0018_service_checkout')
    new=MetaData();new.reflect(engine)
    with engine.connect() as db:
        for name,row in before.items():
            result=dict(db.execute(select(new.tables[name])).mappings().one())
            assert {key:result[key] for key in row}==row
        assert db.execute(select(new.tables['service_order_quotes'].c.checkout_terms)).scalar_one() is None
        assert not list(db.execute(text('PRAGMA foreign_key_check')))
    with engine.begin() as db:
        db.execute(new.tables['service_checkouts'].insert().values(payment_order_id='payment-order',tenant_id='tenant',service_order_id='service',service_quote_id='quote',actor_id='owner',accepted_revision=3,snapshot={'test_fixture':True},consent=None,created_at=now))
    for statement in ["UPDATE service_order_quotes SET amount_inc_vat=1","DELETE FROM service_order_quotes","UPDATE service_checkouts SET snapshot='{}'","DELETE FROM service_checkouts"]:
        with pytest.raises(IntegrityError),engine.begin() as db:db.execute(text(statement))
    command.downgrade(cfg,'0017_metrics')
    old=MetaData();old.reflect(engine)
    with engine.connect() as db:
        for name,row in before.items():assert dict(db.execute(select(old.tables[name])).mappings().one())==row
        assert not list(db.execute(text('PRAGMA foreign_key_check')))
    engine.dispose()


def test_0018_postgresql_offline_has_rls_and_no_legacy_backfill(monkeypatch):
    monkeypatch.setenv('APP_ENV','test');monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://unused:unused@invalid.invalid/unused')
    stream=StringIO();cfg=Config(str(CFG),output_buffer=stream)
    command.upgrade(cfg,'0017_metrics:0018_service_checkout',sql=True)
    sql=stream.getvalue()
    assert 'ALTER TABLE service_checkouts ENABLE ROW LEVEL SECURITY' in sql
    assert 'REVOKE ALL ON TABLE service_checkouts FROM PUBLIC' in sql
    assert 'CREATE TRIGGER immutable_service_quote' in sql
    assert 'UPDATE service_order_quotes' not in sql and 'UPDATE payment_orders' not in sql
    assert 'REFERENCES payment_orders' in sql and 'UNIQUE (service_order_id)' in sql
