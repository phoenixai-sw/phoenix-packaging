"""Manufacturer finishing is bound to evidence and never imported across tenants."""
from copy import deepcopy
from services.api.tests.test_business import business
from services.api.tests.test_api import project, image_file
from services.api.geometry.snapshots import canonical_hash


def source(owner):
    item=project(owner)
    scene=deepcopy(item['scene'])
    scene['pouch_features']={}
    scene['holes']=[{'id':'hanger-source','face_id':'front','center_x_mm':80,'center_y_mm':15,'diameter_mm':6}]
    # A real draft save validates the complete scene and increments the source.
    response=owner.patch(f"/v1/projects/{item['id']}/draft",json={'base_revision':1,'scene':scene})
    assert response.status_code==200,response.text
    return response.json()['data']


def spec(preview):
    return {'name':'Finishing evidence fixture','manufacturer':'Local test fixture only','is_demo':False,
        'source':'Self-authored test','license':'Test use','material':'Test film',
        'billing_family_key':'finishing-test','geometry_template_id':preview['geometry_template_id'],
        'approved_dimensions':preview['approved_dimensions'],'approved_finishing':preview['approved_finishing']}


def test_finishing_import_revision_scope_and_evidence_hash(business):
    app,owner,auth,member=business
    app.state.settings.admin_emails=(auth['user']['email'],)
    item=source(owner)
    url='/v1/admin/template-finishing/preview'
    body={'project_id':item['id'],'base_revision':item['base_revision']}
    assert owner.post(url,json={**body,'base_revision':1}).status_code==409
    assert owner.post(url,json=body,headers={'X-CSRF-Token':'bad'}).status_code==403
    draft=owner.post(url,json=body)
    assert draft.status_code==200,draft.text
    data=draft.json()['data']
    other,other_auth=member('finishing-other@example.com')
    app.state.settings.admin_emails+=(other_auth['user']['email'],)
    assert other.post(url,json=body).status_code==404
    value=spec(data)
    result=owner.post('/v1/admin/template-versions',json=value)
    assert result.status_code==201,result.text
    row=result.json()['data'];path=f"/v1/admin/template-versions/{row['id']}"
    assert row['approved_finishing']==data['approved_finishing']
    assert owner.post(path+'/review',json={'reason':'Compare actual hole and zipper fixture dimensions'}).status_code==200
    evidence=owner.post('/v1/admin/evidence',files={'file':image_file()}).json()['data']
    approved=owner.post(path+'/approve',json={'evidence_asset_id':evidence['id'],'notes':'Isolated fixture approval only','approved_by_name':'Test fixture'})
    assert approved.status_code==200,approved.text
    assert approved.json()['data']['approval']['finishing_hash']==canonical_hash(data['approved_finishing'])


def test_finishing_mismatched_dimensions_profile_and_missing_dimensions_rejected(business):
    app,owner,auth,_=business
    app.state.settings.admin_emails=(auth['user']['email'],)
    item=source(owner)
    data=owner.post('/v1/admin/template-finishing/preview',json={'project_id':item['id'],'base_revision':item['base_revision']}).json()['data']
    changed=spec(data);changed['approved_dimensions']={**changed['approved_dimensions'],'width_mm':180}
    result=owner.post('/v1/admin/template-versions',json=changed)
    assert result.status_code==422 and result.json()['code']=='FINISHING_APPROVAL_MISMATCH',result.text
    missing=spec(data);missing['approved_dimensions']={}
    assert owner.post('/v1/admin/template-versions',json=missing).status_code==422
    profile={**spec(data),'requirements':{'color_space':'RGB'}}
    result=owner.post('/v1/admin/print-profiles',json=profile)
    assert result.status_code==422 and result.json()['code']=='FINISHING_TEMPLATE_ONLY'


def test_registered_finishing_validated_dimensions_and_approval_are_returned(business):
    app,owner,auth,_=business
    app.state.settings.admin_emails=(auth['user']['email'],)
    definition={'schema_version':'2.0','recipe_id':'three-side-seal-separated-v1','family':'three-side-seal',
        'dimension_semantics':{'basis':'finished_outer'},'width_range_mm':{'minimum':60,'maximum':600},
        'height_range_mm':{'minimum':80,'maximum':800},'seals_mm':{'left':8,'right':8,'top':6,'bottom':8},
        'feature_policy':'pouch-finishing-v1','finishing':{'pouch_features':{},'holes':[
            {'id':'registered-hanger','face_id':'front','center_x_mm':80,'center_y_mm':15,'diameter_mm':6}]}}
    response=owner.post('/v1/admin/structures/validate',json={'structure_definition':definition,'inputs':{'width_mm':160,'height_mm':230}})
    assert response.status_code==200,response.text
    validated=response.json()['data']
    assert validated['approved_dimensions']=={'width_mm':160,'height_mm':230}
    values={**spec({'geometry_template_id':'three-side-seal',**validated}), 'structure_definition':validated['normalized_definition'],'review_available':True}
    created=owner.post('/v1/admin/template-versions',json=values)
    assert created.status_code==201,created.text
    assert created.json()['data']['approved_finishing']==validated['approved_finishing']
