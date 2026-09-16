from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from services.api.config import Settings
from services.api.database import utcnow
from services.api.main import create_app
from services.api.models import User
from services.api.feature_models import Membership,Variant,RegistryVersion,AuditEvent
from services.api.billing.models import Subscription
from services.api.tests.test_api import register,project,image_file

@pytest.fixture
def business(tmp_path):
    app=create_app(Settings(environment="test",database_url=f"sqlite:///{tmp_path/'business.db'}",storage_dir=tmp_path/"storage",mail_outbox_dir=tmp_path/"mail"))
    clients=[]
    with TestClient(app) as owner:
        auth=register(owner)
        def member(email):
            client=TestClient(app);clients.append(client);data=register(client,email);return client,data
        yield app,owner,auth,member
    for client in clients:client.close()

def paid(app,tenant_id,plan="partner"):
    now=utcnow()
    with app.state.session_factory() as db:
        row=db.scalar(select(Subscription).where(Subscription.tenant_id==tenant_id))
        if row is None:
            row=Subscription(tenant_id=tenant_id,plan_id=plan,status="active",anchor_day=now.day,current_period_start=now,current_period_end=now+timedelta(days=30),billing_anchor=now,paid_until=now+timedelta(days=30));db.add(row)
        else:row.plan_id=plan;row.paid_until=now+timedelta(days=30)
        db.commit()

def workspace(client,name):
    response=client.post("/v1/workspaces",json={"name":name});assert response.status_code==201,response.text;return response.json()["data"]["id"]

def invitation(client,email,workspaces=None,role="editor"):
    response=client.post("/v1/team/invitations",json={"email":email,"role":role,"workspace_ids":workspaces or []});assert response.status_code==201,response.text;return response.json()["data"]

def accept(client,invite):
    response=client.post("/v1/team/invitations/accept",json={"token":invite["token"]});assert response.status_code==200,response.text;return response.json()["data"]

def test_catalog_tenant_ids_and_csrf_are_not_trusted(business):
    app,owner,auth,member=business;other,_=member("other@example.com")
    brand=owner.post("/v1/brands",json={"name":"Owner brand","colors":["#123456"]}).json()["data"]
    assert other.get("/v1/brands").json()["data"]["items"]==[]
    assert other.patch(f"/v1/brands/{brand['id']}",json={"name":"steal"}).status_code==404
    assert other.post("/v1/products",json={"name":"foreign","brand_id":brand["id"]}).status_code==404
    assert other.post("/v1/projects",json={"name":"foreign","product_name":"foreign","brand_id":brand["id"]}).status_code==404
    assert owner.post("/v1/brands",json={"name":"bad","tenant_id":str(uuid4())}).status_code==422
    assert owner.post("/v1/brands",headers={"X-CSRF-Token":"invalid"},json={"name":"bad"}).status_code==403
    assert owner.get("/v1/admin/overview").status_code==403
    assert owner.post("/v1/admin/template-versions",json={"name":"fake","manufacturer":"fake","source":"fake","license":"fake"}).status_code==403

def test_partner_seats_invitation_email_and_replay(business):
    app,owner,auth,member=business
    assert owner.post("/v1/workspaces",json={"name":"no plan"}).status_code==403
    paid(app,auth["tenant"]["id"])
    invitations=[invitation(owner,f"member{i}@example.com") for i in range(4)]
    denied=owner.post("/v1/team/invitations",json={"email":"member5@example.com","role":"editor"})
    assert denied.status_code==403 and denied.json()["code"]=="SEAT_LIMIT"
    wrong,_=member("wrong@example.com")
    assert wrong.post("/v1/team/invitations/accept",json={"token":invitations[0]["token"]}).status_code==422
    first,first_auth=member("member0@example.com");accepted=accept(first,invitations[0])
    assert accepted["tenant"]["id"]==auth["tenant"]["id"] and accepted["user"]["role"]=="editor"
    assert first.post("/v1/team/invitations/accept",json={"token":invitations[0]["token"]}).status_code==422
    assert len(owner.get("/v1/team").json()["data"]["members"])==2
    with app.state.session_factory() as db:
        event=db.scalar(select(AuditEvent).where(AuditEvent.action=="invitation_accepted"))
        assert event.tenant_id==auth["tenant"]["id"]

def test_member_workspace_project_asset_and_export_isolation(business):
    app,owner,auth,member=business;paid(app,auth["tenant"]["id"])
    first,second=workspace(owner,"Client A"),workspace(owner,"Client B")
    def scoped(name,space):
        response=owner.post("/v1/projects",json={"name":name,"product_name":name,"workspace_id":space});assert response.status_code==201,response.text;return response.json()["data"]
    a,b=scoped("A",first),scoped("B",second)
    asset=owner.post("/v1/assets",data={"project_id":b["id"]},files={"file":image_file()}).json()["data"]
    job=owner.post("/v1/exports",json={"project_id":b["id"],"base_revision":1}).json()["data"]
    editor,_=member("editor@example.com");accept(editor,invitation(owner,"editor@example.com",[first]))
    assert {p["id"] for p in editor.get("/v1/projects").json()["data"]["items"]}=={a["id"]}
    assert editor.get(f"/v1/projects/{a['id']}").status_code==200
    for path in (f"/v1/projects/{b['id']}",f"/v1/projects/{b['id']}/revisions",f"/v1/assets/{asset['id']}/content",f"/v1/jobs/{job['id']}",f"/v1/exports/{job['id']}/download"):
        assert editor.get(path).status_code==404,path
    assert editor.post("/v1/assets",data={"project_id":b["id"]},files={"file":image_file()}).status_code==404
    scene=deepcopy(a["scene"]);scene["faces"][0]["objects"].append({"id":"foreign","type":"image","face_id":"front","x_mm":20,"y_mm":20,"width_mm":30,"height_mm":30,"asset_id":asset["id"]})
    assert editor.patch(f"/v1/projects/{a['id']}/draft",json={"base_revision":1,"scene":scene}).status_code==404

@pytest.mark.parametrize("reason",["expired-plan","removed-member"])
def test_expired_or_removed_membership_can_switch_home_and_logout(business,reason):
    app,owner,auth,member=business;paid(app,auth["tenant"]["id"])
    editor,home=member("editor@example.com");accept(editor,invitation(owner,"editor@example.com"))
    with app.state.session_factory() as db:
        if reason=="expired-plan":db.scalar(select(Subscription).where(Subscription.tenant_id==auth["tenant"]["id"])).paid_until=utcnow()-timedelta(seconds=1)
        else:db.scalar(select(Membership).where(Membership.user_id==home["user"]["id"])).is_active=False
        db.commit()
    assert editor.get("/v1/projects").status_code==403
    switched=editor.post("/v1/team/switch",json={"tenant_id":home["tenant"]["id"]})
    assert switched.status_code==200,switched.text
    assert switched.json()["data"]["tenant"]["id"]==home["tenant"]["id"]
    assert editor.get("/v1/projects").status_code==200
    assert editor.post("/v1/auth/logout").status_code==200

def test_invitation_from_another_active_editor_context(business):
    app,owner,auth,member=business;paid(app,auth["tenant"]["id"])
    other_owner,other_auth=member("otherowner@example.com");paid(app,other_auth["tenant"]["id"])
    other_space=workspace(other_owner,"Other client")
    editor,_=member("editor@example.com");accept(editor,invitation(owner,"editor@example.com"))
    accepted=accept(editor,invitation(other_owner,"editor@example.com",[other_space]))
    assert accepted["tenant"]["id"]==other_auth["tenant"]["id"] and accepted["workspace_ids"]==[other_space]

def test_catalog_variant_ids_bindings_cas_and_clone_preserve_identity(business):
    app,owner,auth,member=business
    brand=owner.post("/v1/brands",json={"name":"Brand"}).json()["data"]
    catalog=owner.post("/v1/products",json={"name":"Original","brand_id":brand["id"],"variants":[{"name":"200g","net_quantity":200,"net_unit":"g","ingredients":"현미"},{"name":"500g","net_quantity":500,"net_unit":"g"}]}).json()["data"]
    variant=catalog["variants"][0];other_id=catalog["variants"][1]["id"]
    updated=owner.patch(f"/v1/products/{catalog['id']}",json={"name":"Changed product","brand_id":brand["id"],"variants":[{"id":variant["id"],"name":"200g renamed","ingredients":"현미·귀리","net_quantity":200,"net_unit":"g"}]})
    assert updated.status_code==200,updated.text
    assert {v["id"] for v in updated.json()["data"]["variants"]}=={variant["id"],other_id}
    item=project(owner);scene=deepcopy(item["scene"]);scene["faces"][0]["objects"][1]["binding_key"]="product_name"
    saved=owner.patch(f"/v1/projects/{item['id']}/draft",json={"base_revision":1,"scene":scene});assert saved.status_code==200
    preview=owner.post(f"/v1/projects/{item['id']}/bindings/preview",json={"product_variant_id":variant["id"]}).json()["data"]
    assert any(change["after"]=="Changed product" for change in preview["changes"])
    applied=owner.post(f"/v1/projects/{item['id']}/bindings/apply",json={"base_revision":2,"product_variant_id":variant["id"]});assert applied.status_code==200,applied.text
    assert applied.json()["data"]["scene"]["faces"][0]["objects"][1]["text"]=="Changed product"
    assert applied.json()["data"]["product_variant_id"]==variant["id"]
    assert owner.post(f"/v1/projects/{item['id']}/bindings/apply",json={"base_revision":2,"product_variant_id":variant["id"]}).status_code==409
    duplicate=owner.post(f"/v1/projects/{item['id']}/duplicate",json={"name":"Copy"});assert duplicate.status_code==201,duplicate.text
    copied=duplicate.json()["data"];assert copied["id"]!=item["id"] and copied["product_variant_id"]==variant["id"] and copied["base_revision"]==1
    assert copied["scene"]["faces"]==applied.json()["data"]["scene"]["faces"]

def test_registry_approval_requires_dimensions_and_is_irreversible(business):
    app,owner,auth,member=business
    with app.state.session_factory() as db:db.get(User,auth["user"]["id"]).is_admin=True;db.commit()
    evidence=owner.post("/v1/admin/evidence",files={"file":image_file()});assert evidence.status_code==201,evidence.text
    evidence_id=evidence.json()["data"]["id"]
    spec={"name":"Own fixture","manufacturer":"Fixture factory","is_demo":False,"geometry_template_id":"three-side-seal","billing_family_key":"family","source":"test-only","license":"test-only","material":"test-paper"}
    row=owner.post("/v1/admin/template-versions",json=spec);assert row.status_code==201,row.text
    path=f"/v1/admin/template-versions/{row.json()['data']['id']}"
    approval={"evidence_asset_id":evidence_id,"notes":"Fixture only","approved_by_name":"Test admin"}
    assert owner.post(path+"/approve",json=approval).status_code==422
    valid=owner.post("/v1/admin/template-versions",json={**spec,"approved_dimensions":{"width_mm":160,"height_mm":230}}).json()["data"]
    path=f"/v1/admin/template-versions/{valid['id']}"
    accepted=owner.post(path+"/approve",json=approval);assert accepted.status_code==200,accepted.text
    assert owner.post(path+"/revoke",json={"reason":"Test revocation"}).status_code==200
    assert owner.post(path+"/approve",json=approval).status_code==409
    demo=owner.post("/v1/admin/template-versions",json={**spec,"is_demo":True,"approved_dimensions":{"width_mm":160,"height_mm":230}}).json()["data"]
    denied=owner.post(f"/v1/admin/template-versions/{demo['id']}/approve",json=approval)
    assert denied.status_code==422 and denied.json()["code"]=="DEMO_NOT_APPROVABLE"
