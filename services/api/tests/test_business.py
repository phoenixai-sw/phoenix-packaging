from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
from urllib.parse import urlparse,parse_qs
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from services.api.config import Settings
from services.api.database import utcnow
from services.api.main import create_app
from services.api.models import User,Project
from services.api.feature_models import Membership,Variant,RegistryVersion,AuditEvent,Invitation
from services.api.billing.models import Subscription
from services.api.tests.test_api import register,project,image_file
from services.api.tests.auth_helpers import google_login

@pytest.fixture
def business(tmp_path):
    app=create_app(Settings(environment="test",database_url=f"sqlite:///{tmp_path/'business.db'}",storage_dir=tmp_path/"storage"))
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
    response=client.post("/v1/team/invitations/accept",json={"token":parse_qs(urlparse(invite["invitation_url"]).query)["invite"][0]});assert response.status_code==200,response.text;return response.json()["data"]

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

def test_partner_seats_manual_invitation_and_replay(business):
    app,owner,auth,member=business
    assert owner.post("/v1/workspaces",json={"name":"no plan"}).status_code==403
    paid(app,auth["tenant"]["id"])
    invitations=[invitation(owner,f"member{i}@example.com") for i in range(4)]
    denied=owner.post("/v1/team/invitations",json={"email":"member5@example.com","role":"editor"})
    assert denied.status_code==403 and denied.json()["code"]=="SEAT_LIMIT"
    wrong,_=member("wrong@example.com")
    assert wrong.post("/v1/team/invitations/accept",json={"token":parse_qs(urlparse(invitations[0]["invitation_url"]).query)["invite"][0]}).status_code==422
    first,first_auth=member("member0@example.com");accepted=accept(first,invitations[0])
    assert accepted["tenant"]["id"]==auth["tenant"]["id"] and accepted["user"]["role"]=="editor"
    assert first.post("/v1/team/invitations/accept",json={"token":parse_qs(urlparse(invitations[0]["invitation_url"]).query)["invite"][0]}).status_code==422
    assert len(owner.get("/v1/team").json()["data"]["members"])==2
    with app.state.session_factory() as db:
        event=db.scalar(select(AuditEvent).where(AuditEvent.action=="invitation_accepted"))
        assert event.tenant_id==auth["tenant"]["id"]


def test_external_google_email_cannot_claim_email_addressed_invitation(business):
    app,owner,auth,_=business
    paid(app,auth["tenant"]["id"])
    invite=invitation(owner,"external@example.com")
    assert invite["delivery"]=="manual_share" and "token" not in invite
    assert "invitation_url" not in owner.get("/v1/team").json()["data"]["invitations"][0]
    with TestClient(app) as external:
        signed=google_login(external,"external@example.com",claims={"hd":None})
        assert signed.status_code==200 and signed.json()["data"]["user"]["email_verified"] is True
        external.headers["X-CSRF-Token"]=signed.json()["data"]["csrf_token"]
        token=parse_qs(urlparse(invite["invitation_url"]).query)["invite"][0]
        denied=external.post("/v1/team/invitations/accept",json={"token":token})
        assert denied.status_code==403 and denied.json()["code"]=="GOOGLE_TEAM_IDENTITY_REQUIRED"


def test_team_lists_only_current_pending_invitations_with_status_and_expiry(business):
    app,owner,auth,member=business
    paid(app,auth["tenant"]["id"])
    pending=invitation(owner,"pending@example.com")
    joined=invitation(owner,"joined@example.com")
    editor,_=member("joined@example.com")
    accept(editor,joined)
    other,other_auth=member("otherowner@example.com")
    paid(app,other_auth["tenant"]["id"])
    invitation(other,"foreign@example.com")
    now=utcnow()
    with app.state.session_factory() as db:
        for state in ("expired","revoked"):
            db.add(Invitation(tenant_id=auth["tenant"]["id"],email=f"{state}@example.com",role="viewer",token_hash=uuid4().hex+uuid4().hex,workspace_ids=[],invited_by=auth["user"]["id"],expires_at=now-timedelta(seconds=1) if state=="expired" else now+timedelta(days=7),revoked_at=now if state=="revoked" else None))
        db.commit()
    response=owner.get("/v1/team")
    assert response.status_code==200,response.text
    rows=response.json()["data"]["invitations"]
    assert len(rows)==1 and rows[0]["id"]==pending["id"]
    assert rows[0]["status"]=="pending" and rows[0]["email"]=="pending@example.com"
    assert rows[0]["expires_at"] and set(rows[0])=={"id","email","role","status","expires_at"}
    # Once the remaining row expires, its formerly returned status must not remain visible.
    with app.state.session_factory() as db:
        db.get(Invitation,pending["id"]).expires_at=now-timedelta(seconds=1)
        db.commit()
    assert owner.get("/v1/team").json()["data"]["invitations"]==[]


def test_owner_can_revoke_pending_invitation_once_and_reinvite_without_losing_member(business):
    app,owner,auth,member=business
    paid(app,auth["tenant"]["id"])
    invitations=[invitation(owner,f"pending{i}@example.com") for i in range(4)]
    original=invitations[0]
    path=f"/v1/team/invitations/{original['id']}"
    assert owner.post("/v1/team/invitations",json={"email":"extra@example.com","role":"editor"}).status_code==403
    revoked=owner.delete(path)
    assert revoked.status_code==200,revoked.text
    assert revoked.json()["data"]=={"id":original["id"],"status":"revoked"}
    assert owner.delete(path).json()["data"]==revoked.json()["data"]
    with app.state.session_factory() as db:
        row=db.get(Invitation,original["id"])
        assert row.revoked_at and not row.accepted_at
        events=list(db.scalars(select(AuditEvent).where(AuditEvent.action=="invitation_revoked",AuditEvent.entity_id==row.id)))
        assert len(events)==1 and events[0].tenant_id==auth["tenant"]["id"] and events[0].actor_id==auth["user"]["id"]
        row.expires_at=utcnow()-timedelta(seconds=1)
        db.commit()
    assert owner.delete(path).status_code==200
    assert original["id"] not in {i["id"] for i in owner.get("/v1/team").json()["data"]["invitations"]}
    replacement=invitation(owner,"pending0@example.com")
    assert replacement["id"]!=original["id"] and replacement["invitation_url"]!=original["invitation_url"]
    editor,editor_auth=member("pending0@example.com")
    old_token=parse_qs(urlparse(original["invitation_url"]).query)["invite"][0]
    assert editor.post("/v1/team/invitations/accept",json={"token":old_token}).status_code==422
    accept(editor,replacement)
    accepted_cancel=owner.delete(f"/v1/team/invitations/{replacement['id']}")
    assert accepted_cancel.status_code==409 and accepted_cancel.json()["code"]=="INVITATION_ACCEPTED"
    with app.state.session_factory() as db:
        row=db.get(Invitation,replacement["id"])
        joined=db.scalar(select(Membership).where(Membership.tenant_id==auth["tenant"]["id"],Membership.user_id==editor_auth["user"]["id"]))
        assert row.accepted_at and not row.revoked_at and joined.is_active
    assert editor.get("/v1/projects").status_code==200


def test_invitation_revoke_requires_tenant_owner_csrf_and_pending_state(business):
    app,owner,auth,member=business
    paid(app,auth["tenant"]["id"])
    pending=invitation(owner,"pending@example.com")
    path=f"/v1/team/invitations/{pending['id']}"
    assert owner.delete(path,headers={"X-CSRF-Token":"invalid"}).status_code==403
    other,_=member("otherowner@example.com")
    assert other.delete(path).status_code==404
    for role in ("editor","viewer"):
        client,_=member(f"{role}@example.com")
        accept(client,invitation(owner,f"{role}@example.com",role=role))
        assert client.delete(path).status_code==403
    with app.state.session_factory() as db:
        row=db.get(Invitation,pending["id"])
        assert not row.revoked_at
        row.expires_at=utcnow()-timedelta(seconds=1)
        db.commit()
    expired=owner.delete(path)
    assert expired.status_code==409 and expired.json()["code"]=="INVITATION_EXPIRED"
    with app.state.session_factory() as db:
        assert db.get(Invitation,pending["id"]).revoked_at is None

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


@pytest.mark.parametrize("role",["editor","viewer"])
def test_project_limit_applies_after_workspace_access_filter(business,role):
    app,owner,auth,member=business
    paid(app,auth["tenant"]["id"])
    allowed,blocked=workspace(owner,"Allowed"),workspace(owner,"Restricted")
    response=owner.post("/v1/projects",json={"name":"Older allowed project","product_name":"Allowed","workspace_id":allowed})
    assert response.status_code==201,response.text
    visible=response.json()["data"]
    shared=project(owner)
    now=utcnow()
    with app.state.session_factory() as db:
        for identity in (visible["id"],shared["id"]):
            db.get(Project,identity).updated_at=now-timedelta(days=1)
        for index in range(101):
            db.add(Project(tenant_id=auth["tenant"]["id"],created_by=auth["user"]["id"],name=f"Restricted {index}",product_name="Restricted",brand_name="",template_id="three-side-seal",width_mm=visible["width_mm"],height_mm=visible["height_mm"],workspace_id=blocked,scene=deepcopy(visible["scene"]),updated_at=now+timedelta(seconds=index)))
        db.commit()
    editor,_=member(f"{role}@example.com")
    accept(editor,invitation(owner,f"{role}@example.com",[allowed],role=role))
    response=editor.get("/v1/projects")
    assert response.status_code==200,response.text
    assert {p["id"] for p in response.json()["data"]["items"]}=={visible["id"],shared["id"]}
    assert editor.get(f"/v1/projects/{visible['id']}").status_code==200
    owner_rows=owner.get("/v1/projects").json()["data"]["items"]
    assert len(owner_rows)==100 and all(p["workspace_id"]==blocked for p in owner_rows)

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
    app.state.settings.admin_emails=(auth["user"]["email"],)
    evidence=owner.post("/v1/admin/evidence",files={"file":image_file()});assert evidence.status_code==201,evidence.text
    evidence_id=evidence.json()["data"]["id"]
    spec={"name":"Own fixture","manufacturer":"Fixture factory","is_demo":False,"geometry_template_id":"three-side-seal","billing_family_key":"family","source":"test-only","license":"test-only","material":"test-paper"}
    row=owner.post("/v1/admin/template-versions",json=spec);assert row.status_code==201,row.text
    path=f"/v1/admin/template-versions/{row.json()['data']['id']}"
    approval={"evidence_asset_id":evidence_id,"notes":"Fixture only","approved_by_name":"Test admin"}
    assert owner.post(path+"/approve",json=approval).json()["code"]=="REVIEW_REQUIRED"
    assert owner.post(path+"/review",json={"reason":"Review fixture dimensions"}).status_code==200
    assert owner.post(path+"/approve",json=approval).status_code==422
    valid=owner.post("/v1/admin/template-versions",json={**spec,"approved_dimensions":{"width_mm":160,"height_mm":230}}).json()["data"]
    path=f"/v1/admin/template-versions/{valid['id']}"
    assert owner.post(path+"/review",json={"reason":"Review valid fixture"}).status_code==200
    accepted=owner.post(path+"/approve",json=approval);assert accepted.status_code==200,accepted.text
    assert owner.post(path+"/revoke",json={"reason":"Test revocation"}).status_code==200
    assert owner.post(path+"/approve",json=approval).status_code==409
    demo=owner.post("/v1/admin/template-versions",json={**spec,"is_demo":True,"approved_dimensions":{"width_mm":160,"height_mm":230}}).json()["data"]
    denied=owner.post(f"/v1/admin/template-versions/{demo['id']}/approve",json=approval)
    assert denied.status_code==422 and denied.json()["code"]=="DEMO_NOT_APPROVABLE"
