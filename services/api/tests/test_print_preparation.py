from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from services.api.tests.test_business import business, paid, invitation, accept
from services.api.tests.test_api import project


def catalog(client, name="오리스틱", barcode=""):
    response = client.post("/v1/products", json={"name": name, "variants": [{"name": "4개입", "barcode": barcode}]})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def declare(client, variant_id, **overrides):
    current = client.get(f"/v1/variants/{variant_id}/barcode-registration").json()["data"]
    body = {"barcode": "0123456789012", "holder_name": "시험 제조사", "registration_reference": "테스트용 등록 근거",
            "source_url": "https://www.koreannet.or.kr/", "confirmed_rights": True,
            "expected_updated_at": current["updated_at"], **overrides}
    return client.put(f"/v1/variants/{variant_id}/barcode-registration", json=body)


def test_compose_requires_attestation_preserves_zeros_and_never_issues(business):
    _, client, _, _ = business
    payload = {"company_prefix": "0123456", "item_reference": "78901", "registered_prefix_confirmed": True}
    response = client.post("/v1/barcodes/gtin13/compose", json=payload)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["value"] == "0123456789012" and data["check_digit"] == "2"
    assert data["issued"] is False and "GS1" in data["notice"]
    for patch in ({"registered_prefix_confirmed": False}, {"company_prefix": "9520000"},
                  {"item_reference": "7"}, {"company_prefix": "０１２３４５６"}):
        assert client.post("/v1/barcodes/gtin13/compose", json={**payload, **patch}).status_code == 422


def test_registration_auth_tenant_rights_and_checksum(business):
    _, client, _, member = business
    variant = catalog(client)["variants"][0]
    other, _ = member("outside@example.com")
    path = f"/v1/variants/{variant['id']}/barcode-registration"
    assert other.get(path).status_code == 404
    assert declare(client, variant["id"], confirmed_rights=False).status_code == 422
    assert declare(client, variant["id"], barcode="0123456789013").status_code == 422
    assert declare(client, variant["id"], barcode="9520000000011").status_code == 422
    assert declare(client, variant["id"], source_url="http://example.com/").status_code == 422
    assert declare(client, variant["id"], source_url="https://name:password@example.com/").status_code == 422
    saved = declare(client, variant["id"])
    assert saved.status_code == 200, saved.text
    data = saved.json()["data"]
    assert data["registration"]["verification"] == "user_attested"
    assert data["registration"]["barcode"] == data["barcode"] == "0123456789012"
    assert "confirmed_at" in data["registration"] and data["duplicate_variants"] == []


def test_duplicate_registration_and_catalog_writes_share_guard(business):
    _, client, _, _ = business
    first, second = catalog(client, "첫 상품"), catalog(client, "다른 상품")
    assert declare(client, first["variants"][0]["id"]).status_code == 200
    duplicate = declare(client, second["variants"][0]["id"])
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "BARCODE_ALREADY_ASSIGNED"
    response = client.patch(f"/v1/products/{second['id']}", json={"name": "다른 상품", "variants": [{"id": second["variants"][0]["id"], "name": "중복", "barcode": "0123456789012"}]})
    assert response.status_code == 409
    # Duplicates within one new product must roll back the whole transaction.
    response = client.post("/v1/products", json={"name": "동일 번호 둘", "variants": [
        {"name": "A", "barcode": "4006381333931"}, {"name": "B", "barcode": "4006381333931"}]})
    assert response.status_code == 409
    assert "동일 번호 둘" not in [p["name"] for p in client.get("/v1/products").json()["data"]["items"]]


def test_stale_declaration_and_barcode_change_invalidates_record(business):
    _, client, _, _ = business
    product = catalog(client)
    variant = product["variants"][0]
    old_stamp = client.get(f"/v1/variants/{variant['id']}/barcode-registration").json()["data"]["updated_at"]
    assert declare(client, variant["id"]).status_code == 200
    assert declare(client, variant["id"], expected_updated_at=old_stamp).status_code == 409
    body = {"name": "오리스틱 수정", "variants": [{"id": variant["id"], "name": "4개입", "barcode": "0123456789012"}]}
    result = client.patch(f"/v1/products/{product['id']}", json=body)
    assert result.status_code == 200
    assert result.json()["data"]["variants"][0]["barcode_registration"]["barcode"] == "0123456789012"
    body["variants"][0]["barcode"] = "4006381333931"
    result = client.patch(f"/v1/products/{product['id']}", json=body)
    assert result.status_code == 200
    assert "barcode_registration" not in result.json()["data"]["variants"][0]


def test_preparation_is_readonly_and_does_not_approve_demo(business):
    _, client, _, member = business
    saved = project(client)
    before = deepcopy(client.get(f"/v1/projects/{saved['id']}").json()["data"])
    response = client.get(f"/v1/projects/{saved['id']}/print-preparation")
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["base_revision"] == before["base_revision"]
    assert result["barcode"]["variant_id"] is None
    assert next(c for c in result["manufacturing"]["checks"] if c["key"] == "template")["status"] == "needs_input"
    assert result["capabilities"]["pdf_x"] is False
    assert before == client.get(f"/v1/projects/{saved['id']}").json()["data"]
    other, _ = member("other-preparation@example.com")
    assert other.get(f"/v1/projects/{saved['id']}/print-preparation").status_code == 404


def test_viewer_cannot_register_or_calculate_on_mutating_routes(business):
    app, client, auth, member = business
    paid(app, auth["tenant"]["id"])
    variant = catalog(client)["variants"][0]
    invited = invitation(client, "viewer-print@example.com", role="viewer")
    viewer, _ = member("viewer-print@example.com")
    accept(viewer, invited)
    assert viewer.get(f"/v1/variants/{variant['id']}/barcode-registration").status_code == 200
    assert declare(viewer, variant["id"]).status_code == 403
    assert viewer.post("/v1/barcodes/gtin13/compose", json={"company_prefix": "0123456", "item_reference": "78901", "registered_prefix_confirmed": True}).status_code == 403


@pytest.mark.parametrize("change",[
    {"sku":"a-different-sku"}, {"net_weight":"150g"},
    {"net_quantity":300,"net_unit":"g"}, {"ingredients":"다른 배합"},
    {"allergens":"새 알레르기"}, {"manufacturer":"다른 제조사"},
])
def test_product_content_change_requires_new_declaration_without_changing_number(business,change):
    _,client,_,_=business
    product=catalog(client);variant=product["variants"][0]
    assert declare(client,variant["id"]).status_code==200
    response=client.patch(f"/v1/products/{product['id']}",json={"name":product["name"],"variants":[
        {"id":variant["id"],"name":variant["name"],"barcode":"0123456789012",**change}]})
    assert response.status_code==200,response.text
    saved=client.get(f"/v1/variants/{variant['id']}/barcode-registration").json()["data"]
    assert saved["barcode"]=="0123456789012" and saved["registration"] is None
    assert declare(client,variant["id"]).status_code==200


def test_brand_change_invalidates_omitted_variant_declaration_and_stamp(business):
    _,client,_,_=business
    product=catalog(client);variant=product["variants"][0]
    declaration=declare(client,variant["id"]).json()["data"]
    brand=client.post("/v1/brands",json={"name":"새 브랜드"}).json()["data"]
    response=client.patch(f"/v1/products/{product['id']}",json={"name":product["name"],"brand_id":brand["id"],"variants":[]})
    assert response.status_code==200,response.text
    current=client.get(f"/v1/variants/{variant['id']}/barcode-registration").json()["data"]
    assert current["barcode"]==declaration["barcode"] and current["registration"] is None
    assert declare(client,variant["id"],expected_updated_at=declaration["updated_at"]).status_code==409


def test_concurrent_registration_and_catalog_write_cannot_assign_same_number(business):
    from services.api.feature_models import Variant
    app,client,auth,_=business
    first,second=catalog(client,"first"),catalog(client,"second")
    variant=first["variants"][0]
    stamp=client.get(f"/v1/variants/{variant['id']}/barcode-registration").json()["data"]["updated_at"]
    cookie=client.cookies.get("phoenix_session")
    def submit(which):
        with TestClient(app) as actor:
            actor.cookies.set("phoenix_session",cookie);actor.headers["X-CSRF-Token"]=auth["csrf_token"]
            if which==0:
                return actor.put(f"/v1/variants/{variant['id']}/barcode-registration",json={
                    "barcode":"0123456789012","holder_name":"holder","registration_reference":"test reference",
                    "confirmed_rights":True,"expected_updated_at":stamp}).status_code
            return actor.patch(f"/v1/products/{second['id']}",json={"name":"second","variants":[
                {"id":second["variants"][0]["id"],"name":"4개입","barcode":"0123456789012"}]}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(submit,range(2)))
    assert sorted(statuses)==[200,409]
    with app.state.session_factory() as db:
        assigned=[v for v in db.scalars(select(Variant).where(Variant.tenant_id==auth["tenant"]["id"])) if v.details.get("barcode")=="0123456789012"]
        assert len(assigned)==1


@pytest.mark.parametrize("extra",[{"vector_text":True},{"min_ppi":0},{"min_ppi":True},{"required_fields":[]},{"required_fields":[""]},{"spot_colors":True}])
def test_manufacturing_readiness_uses_same_output_requirement_rules_as_final_preflight(business,monkeypatch,extra):
    from services.api.models import Project
    from services.api.exporters.preflight import preflight_project
    app,client,_,_=business;saved=project(client)
    requirements={"pdf_standard":"PDF","color_space":"RGB","font_mode":"embedded",**extra}
    # The route now validates the public contract too: keep this output-rule
    # fixture shaped like the real registry snapshots returned by the service.
    conditions={"template":{"id":None,"status":"unapproved","is_demo":True},
                "profile":{"id":"test-profile","kind":"profile","name":"Test profile",
                           "manufacturer":"Test manufacturer","status":"draft","is_demo":True,
                           "approval":{},"requirements":requirements}}
    monkeypatch.setattr("services.api.print_preparation.approved_conditions",lambda *args:conditions)
    preparation=client.get(f"/v1/projects/{saved['id']}/print-preparation").json()["data"]
    output=next(c for c in preparation["manufacturing"]["checks"] if c["key"]=="output")
    assert output["status"]=="blocked"
    with app.state.session_factory() as db:
        report=preflight_project({"scene":db.get(Project,saved["id"]).scene},conditions)
    output_errors=[i for i in report["issues"] if i["code"] in {"UNSUPPORTED_OUTPUT_REQUIREMENT","UNSUPPORTED_OUTPUT_CAPABILITY","INVALID_PPI_REQUIREMENT","INVALID_REQUIRED_FIELDS"}]
    assert output_errors and all(i["message"] in output["detail"] for i in output_errors)


def test_confirmations_require_actual_boolean(business):
    _,client,_,_=business;variant=catalog(client)["variants"][0]
    assert declare(client,variant["id"],confirmed_rights="yes").status_code==422
    assert client.post("/v1/barcodes/gtin13/compose",json={"company_prefix":"0123456","item_reference":"78901","registered_prefix_confirmed":"true"}).status_code==422
