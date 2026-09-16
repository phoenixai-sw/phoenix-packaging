from datetime import timedelta
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile
import pytest
from sqlalchemy import select,func
from services.api.database import Base,build_database,utcnow
from services.api.config import Settings
from services.api.models import Tenant,User,Project,Revision,Job
from services.api.feature_models import Brand,Product,Variant,RegistryVersion,Evidence
from services.api.billing.models import Wallet,Reservation,LedgerEntry,ProductionEntitlement
from services.api.billing.service import ensure_trial,grant_credits,create_quote
from services.api.production_routes import prepare_production_quote,create_production_export
from services.api.production_jobs import process_production_jobs
from services.api.schemas import ExportInput
from services.api.storage import LocalStorage
from services.api.errors import APIError
from services.api.geometry import new_scene
from services.api.exporters.preflight import DEFAULT_CONFIRMED_FIELDS

def payload(project):
    return {key:getattr(project,key) for key in ("id","name","base_revision","scene","template_id","width_mm","height_mm")}

def revision(db,project,reason):
    row=db.scalar(select(Revision).where(Revision.project_id==project.id,Revision.number==project.base_revision))
    if row is None:
        row=Revision(project_id=project.id,tenant_id=project.tenant_id,number=project.base_revision,scene=project.scene,reason=reason);db.add(row);db.flush()
    return row

@pytest.fixture
def setup(tmp_path):
    settings=Settings(environment="test",database_url=f"sqlite:///{tmp_path/'production.db'}",storage_dir=tmp_path/"storage",enable_production_export=True,policy_approved=True)
    engine,factory=build_database(settings);Base.metadata.create_all(engine);storage=LocalStorage(settings.storage_dir)
    with factory() as db:
        tenant=Tenant(name="Test tenant");db.add(tenant);db.flush()
        user=User(tenant_id=tenant.id,name="Owner",email="owner@example.test",password_hash="unused",role="owner");db.add(user);db.flush()
        brand=Brand(tenant_id=tenant.id,name="Brand");db.add(brand);db.flush()
        product=Product(tenant_id=tenant.id,brand_id=brand.id,name="Product");db.add(product);db.flush()
        variant=Variant(tenant_id=tenant.id,product_id=product.id,name="200g",details={"net_quantity":200,"net_unit":"g"});db.add(variant);db.flush()
        evidence=Evidence(tenant_id=tenant.id,uploaded_by=user.id,storage_key="test-evidence",name="Own test fixture",sha256=sha256(b"test-evidence").hexdigest(),content_type="application/pdf",byte_size=13);db.add(evidence);db.flush()
        approval={"evidence_asset_id":evidence.id,"evidence_sha256":evidence.sha256,"approved_by":user.id,"approved_at":utcnow().isoformat(),"source":"local-test-fixture","license":"test-only"}
        template=RegistryVersion(kind="template",name="Fixture template",manufacturer="Fixture manufacturer",status="approved",is_demo=False,created_by=user.id,approval=approval,details={"geometry_template_id":"three-side-seal","billing_family_key":"fixture-family","material":"fixture-paper","approved_dimensions":{"width_mm":160,"height_mm":230}})
        profile=RegistryVersion(kind="profile",name="Fixture profile",manufacturer="Fixture manufacturer",status="approved",is_demo=False,created_by=user.id,approval=approval,details={"material":"fixture-paper","requirements":{"pdf_standard":"PDF","color_space":"RGB","font_mode":"embedded","min_ppi":150}})
        db.add_all([template,profile]);db.flush()
        scene=new_scene("three-side-seal",160,230);scene["template_version_id"]=template.id;scene["confirmed_fields"]=DEFAULT_CONFIRMED_FIELDS[:]
        project=Project(tenant_id=tenant.id,created_by=user.id,name="Project",product_name="Product",brand_name="Brand",template_id="three-side-seal",width_mm=160,height_mm=230,scene=scene,brand_id=brand.id,product_variant_id=variant.id,template_version_id=template.id,print_profile_version_id=profile.id,material="fixture-paper")
        db.add(project);db.flush();wallet=ensure_trial(db,tenant.id);wallet.ever_paid=True
        grant_credits(db,tenant.id,100,kind="topup",scope="paid",expires_at=utcnow()+timedelta(days=365),grant_key="test-paid",reason="fixture")
        db.commit();identities=(tenant.id,user.id,project.id,template.id)
    yield SimpleNamespace(settings=settings,factory=factory,storage=storage,tenant_id=identities[0],user_id=identities[1],project_id=identities[2],template_id=identities[3])
    engine.dispose()

def enqueue(setup,operation=None):
    with setup.factory() as db:
        user=db.get(User,setup.user_id);db.info["principal"]=user;project=db.get(Project,setup.project_id)
        quote_input={"project_id":project.id,"base_revision":project.base_revision,"reviewed_face_ids":["front","back"]}
        values=prepare_production_quote(db,user,quote_input,setup.settings,payload,revision,setup.storage)
        quote=create_quote(db,user.tenant_id,**values);db.commit()
        body=ExportInput(**quote_input,kind="production",quote_id=quote.id)
        request=SimpleNamespace(headers={"idempotency-key":operation or str(uuid4())},app=SimpleNamespace(state=SimpleNamespace(storage=setup.storage,settings=setup.settings)))
        job=create_production_export(db,user,body,request,payload,revision)
        return job.id,body,request

def test_real_worker_bundle_capture_and_free_repeat(setup):
    job_id,body,request=enqueue(setup)
    with setup.factory() as db:
        same=create_production_export(db,db.get(User,setup.user_id),body,request,payload,revision)
        assert same.id==job_id
    assert process_production_jobs(setup.factory,setup.storage,setup.settings)==1
    with setup.factory() as db:
        job=db.get(Job,job_id);assert job.status=="succeeded",job.error
        assert job.result["credits_charged"]==40
        with ZipFile(BytesIO(setup.storage.get(job.result["storage_key"]))) as archive:assert len(archive.namelist())==6
        assert db.scalar(select(func.count()).select_from(ProductionEntitlement).where(ProductionEntitlement.status=="entitled"))==1
        assert db.scalar(select(func.sum(LedgerEntry.amount)).where(LedgerEntry.event=="CAPTURE"))==40
    repeat,_,_=enqueue(setup)
    assert process_production_jobs(setup.factory,setup.storage,setup.settings)==1
    with setup.factory() as db:
        assert db.get(Job,repeat).result["credits_charged"]==0
        assert db.scalar(select(func.sum(LedgerEntry.amount)).where(LedgerEntry.event=="CAPTURE"))==40

@pytest.mark.parametrize("change",["revision","revocation","actor","disabled","storage"])
def test_failure_returns_reservation_without_entitlement(setup,change):
    job_id,_,_=enqueue(setup)
    if change=="disabled":setup.settings.enable_production_export=False
    elif change=="storage":
        def fail(*args):raise OSError("fixture failure")
        setup.storage.put=fail
    else:
        with setup.factory() as db:
            if change=="revision":db.get(Project,setup.project_id).base_revision+=1
            elif change=="revocation":db.get(RegistryVersion,setup.template_id).status="revoked"
            else:db.get(User,setup.user_id).is_active=False
            db.commit()
    process_production_jobs(setup.factory,setup.storage,setup.settings)
    with setup.factory() as db:
        job=db.get(Job,job_id);assert job.status=="failed" and job.result is None
        assert db.get(Reservation,job.snapshot["reservation_id"]).status=="released"
        assert db.scalar(select(func.count()).select_from(ProductionEntitlement).where(ProductionEntitlement.status=="entitled"))==0
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event=="CAPTURE"))==0

def test_revocation_after_uploaded_zip_prevents_publication(setup):
    job_id,_,_=enqueue(setup);original=setup.storage.put
    def revoke(key,body,content_type):
        original(key,body,content_type)
        with setup.factory() as db:db.get(RegistryVersion,setup.template_id).status="revoked";db.commit()
    setup.storage.put=revoke
    process_production_jobs(setup.factory,setup.storage,setup.settings)
    with setup.factory() as db:
        job=db.get(Job,job_id);assert job.status=="failed" and not job.result
        assert db.get(Reservation,job.snapshot["reservation_id"]).status=="released"

def test_other_tenant_cannot_quote_project(setup):
    with setup.factory() as db:
        user=SimpleNamespace(id="other-user",tenant_id="other-tenant",role="owner")
        with pytest.raises(APIError) as exc:prepare_production_quote(db,user,{"project_id":setup.project_id,"base_revision":1},setup.settings,payload,revision,setup.storage)
        assert exc.value.status==404
