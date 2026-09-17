"""The review stage is an audited gate, not approval by another name."""
import pytest
from sqlalchemy import select
from services.api.tests.test_business import business
from services.api.tests.test_api import image_file
from services.api.feature_models import AuditEvent, RegistryVersion


@pytest.mark.parametrize("collection", ["template-versions", "print-profiles"])
def test_review_gate_and_revocation_preserve_original_record(business, collection):
    app, owner, auth, member = business
    app.state.settings.admin_emails = (auth["user"]["email"],)
    spec = {"name":"Isolated workflow fixture", "manufacturer":"Not a manufacturer approval",
        "is_demo":False, "source":"local test fixture", "license":"test only", "material":"test paper",
        "geometry_template_id":"three-side-seal", "billing_family_key":"review-fixture",
        "approved_dimensions":{"width_mm":160,"height_mm":230},
        "requirements":{"color_space":"RGB","pdf_standard":"PDF"}}
    row = owner.post(f"/v1/admin/{collection}", json=spec).json()["data"]
    path = f"/v1/admin/{collection}/{row['id']}"
    evidence = owner.post("/v1/admin/evidence", files={"file":image_file()}).json()["data"]
    approve = {"evidence_asset_id":evidence["id"],"notes":"Isolated mock approval", "approved_by_name":"Test fixture"}
    denied = owner.post(path+"/approve", json=approve)
    assert denied.status_code == 409 and denied.json()["code"] == "REVIEW_REQUIRED"
    assert owner.post(path+"/revoke",json={"reason":"Never approved"}).status_code == 409
    assert owner.post(path+"/review",json={"reason":"x"}).status_code == 422
    assert owner.post(path+"/review",json={"reason":"Review fixture"},headers={"X-CSRF-Token":"bad"}).status_code == 403
    reviewed = owner.post(path+"/review", json={"reason":"Verify fixture geometry and source"})
    assert reviewed.status_code == 200 and reviewed.json()["data"]["status"] == "review"
    assert reviewed.json()["data"]["approval"] == {}
    assert owner.post(path+"/review",json={"reason":"Duplicate submission"}).status_code == 409
    accepted = owner.post(path+"/approve",json=approve)
    assert accepted.status_code == 200, accepted.text
    approval_snapshot = accepted.json()["data"]["approval"]
    assert owner.post(path+"/revoke",json={"reason":"Private internal diagnosis","public_reason":"Use the replacement print condition."}).status_code == 200
    assert owner.post(path+"/revoke",json={"reason":"Replace original reason"}).status_code == 409
    assert owner.post(path+"/review",json={"reason":"Reopen revoked version"}).status_code == 409
    assert owner.post(path+"/approve",json=approve).status_code == 409
    with app.state.session_factory() as db:
        saved=db.get(RegistryVersion,row["id"])
        assert saved.approval == approval_snapshot
        assert saved.status == "revoked"
        events=list(db.scalars(select(AuditEvent).where(AuditEvent.entity_id==row["id"]).order_by(AuditEvent.created_at)))
        assert [event.action for event in events] == ["registry_created","registry_review_requested","registry_approved","registry_revoked"]
        assert events[-1].details == {"reason":"Private internal diagnosis","public_reason":"Use the replacement print condition."}


def test_demo_and_non_admin_cannot_enter_review(business):
    app, owner, auth, member = business
    app.state.settings.admin_emails=(auth["user"]["email"],)
    row=owner.post("/v1/admin/template-versions",json={"name":"Demo review fixture","manufacturer":"fixture",
        "is_demo":True,"source":"fixture","license":"fixture","geometry_template_id":"three-side-seal","billing_family_key":"demo"}).json()["data"]
    path=f"/v1/admin/template-versions/{row['id']}/review"
    assert owner.post(path,json={"reason":"Should not promote demo"}).json()["code"] == "DEMO_NOT_APPROVABLE"
    other,_=member("not-admin@example.com")
    assert other.post(path,json={"reason":"Unauthorized review"}).status_code == 403
