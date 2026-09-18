"""Isolated machining fixtures. No real manufacturer approval is asserted."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile
import pytest
from pypdf import PdfReader,PdfWriter
from pypdf.generic import DecodedStreamObject,NameObject
from services.api.geometry import new_scene,geometry_for_scene,validate_scene,GeometryValidationError
from services.api.geometry.finishing import finishing_approval_for_geometry,validate_finishing_for_template,finishing_approval_issues
from services.api.geometry.finishing_paths import circle_curves,notch_segments
from services.api.geometry.print_paths import structure_paths
from services.api.geometry.snapshots import canonical_hash,compile_structure,structure_ref
from services.api.exporters.print_pdf import render_print_artifacts,inspect_print
from services.api.exporters.print_verification import verify_print_artifacts
from tests.geometry_pdf.test_print_engine import ICC,profile
from tests.geometry_pdf.test_structure_v2 import separated
from tests.geometry_pdf.test_production_worker import setup,enqueue
from tests.geometry_pdf.test_print_production import cmyk_setup


def finishing_project(shape='round',hole=True):
    scene=new_scene('three-side-seal',160,230)
    scene['pouch_features']={'notch_shape':shape}
    scene['holes']=[{'id':'ui-hole','face_id':'front','center_x_mm':65,'center_y_mm':16,'diameter_mm':6}] if hole else []
    scene['geometry_hash']=None
    for face in scene['faces']:
        face['background']='#572A63'
        face['objects']=[{'id':'title-'+face['id'],'type':'text','face_id':face['id'],'text':'오리스틱 37.5g × 4개입','font_size_pt':20,'font_weight':700,'x_mm':20,'y_mm':60,'width_mm':120,'height_mm':20,'color':'#F9F4E4'}]
    return {'scene':validate_scene(scene)}


def registered_pouch():
    base=geometry_for_scene(new_scene('stand-up-pouch',160,230,bottom_mm=60));panels=[]
    for face in base['faces']:
        s=face['regions']['safe']
        panels.append({k:deepcopy(face[k]) for k in ('id','width_mm','height_mm','net','assembly')}|{'safe_inset_mm':{'left':s['x_mm'],'top':s['y_mm'],'right':face['width_mm']-s['x_mm']-s['width_mm'],'bottom':face['height_mm']-s['y_mm']-s['height_mm']},'fold':face['regions']['fold']})
    return {'schema_version':'2.0','recipe_id':'fixed-panel-net-v1','family':'stand-up-pouch',
        'dimensions':{'width_mm':160,'height_mm':230,'bottom_mm':60},'dimension_semantics':{'basis':'finished_outer','bottom_definition':'expanded_gusset'},
        'panels':panels,'fold_lines':base['fold_lines'],'feature_policy':'pouch-finishing-v1',
        'finishing':{'pouch_features':{'notch_shape':'round'},'holes':[{'id':'registered-hole','face_id':'front','center_x_mm':65,'center_y_mm':16,'diameter_mm':6}]}}


def registered_finishing_project():
    definition=registered_pouch();snap=compile_structure(definition,definition['dimensions'],'fixture-finishing')
    scene=new_scene('stand-up-pouch',160,230,bottom_mm=60)
    scene.update(template_version_id='fixture-finishing',structure_ref=structure_ref(snap),geometry_hash=snap['geometry_hash'],
        holes=deepcopy(snap['geometry']['holes']),pouch_features=deepcopy(snap['geometry']['pouch_features']))
    for face in scene['faces']:face['background']='#572A63'
    return {'scene':validate_scene(scene,structure_snapshot=snap),'structure_snapshot':snap}


def test_finishing_approval_is_physical_and_rebuildable_without_ui_ids():
    item=finishing_project();g=geometry_for_scene(item['scene']);spec=finishing_approval_for_geometry(g)
    assert validate_finishing_for_template('three-side-seal',{'width_mm':160,'height_mm':230},spec)==spec
    scene=deepcopy(item['scene']);scene['holes'][0].update(id='renamed',face_id='back',center_x_mm=95);scene['geometry_hash']=None
    assert finishing_approval_for_geometry(geometry_for_scene(validate_scene(scene)))==spec
    for key,value in [('diameter_mm',7),('center_y_mm',17)]:
        bad=deepcopy(spec);bad['holes'][0][key]=value
        with pytest.raises(GeometryValidationError):validate_finishing_for_template('three-side-seal',{'width_mm':160,'height_mm':230},bad)
    assert finishing_approval_issues(g,{'approved_finishing':spec,'approval':{'finishing_hash':canonical_hash(spec)}},profile(finishing_delivery='separate_process_pdf_v1'))==[]
    assert finishing_approval_issues(g,{'approved_finishing':spec,'approval':{}},profile(finishing_delivery='separate_process_pdf_v1'))[0]['code']=='FINISHING_EVIDENCE_REQUIRED'


@pytest.mark.parametrize('shape',['round','v'])
@pytest.mark.parametrize('bleed',[0,3.175])
def test_vector_cuts_actual_pdf_knockout_and_preserved_bleed(tmp_path,shape,bleed):
    item=finishing_project(shape);p=profile(bleed_mm=bleed,finishing_delivery='separate_process_pdf_v1');out=tmp_path/'result'
    manifest=render_print_artifacts(item,out,p,ICC.read_bytes(),test_mode=True)
    from services.api.contracts.printing import PrintEngineManifest
    PrintEngineManifest.model_validate(manifest)
    assert len(list(out.iterdir()))==9
    assert len(manifest['verification']['finishing']['path_checks'])==6
    assert manifest['finishing']['physical_specification']['holes'][0]['center_x_mm']==65
    paths=manifest['finishing']['pages'];assert paths[1]['holes'][0]['center_x_mm']==95
    assert len(paths[0]['cut_curves'])==(8 if shape=='round' else 4)
    assert len(paths[0]['notches'])==2
    for record in manifest['files']:assert sha256((out/record['name']).read_bytes()).hexdigest()==record['sha256']
    import pypdfium2 as pdfium
    doc=pdfium.PdfDocument(str(out/'production.pdf'));page=doc[0];bitmap=page.render(scale=3)
    try:
        im=bitmap.to_pil().convert('RGB')
        def pixel(x,y):return im.getpixel((round((x+bleed)*72/25.4*3),round((y+bleed)*72/25.4*3)))
        assert min(pixel(65,16))>248 # actual empty circular aperture
        assert max(pixel(60,16))<230 # art beside the circle remains
        assert min(pixel(1,24))>248 # knocked out U or V centre
        assert max(pixel(4,24))<230
        if bleed:assert max(pixel(-1,70))<230 # external bleed was not clipped to trim
    finally:bitmap.close();page.close();doc.close()


def test_round_geometry_error_below_declared_pdf_tolerance():
    import math
    for curve in circle_curves(0,0,5):
        for i in range(101):
            t=i/100;s=1-t;x=s**3*curve[0]+3*s*s*t*curve[2]+3*s*t*t*curve[4]+t**3*curve[6];y=s**3*curve[1]+3*s*s*t*curve[3]+3*s*t*t*curve[5]+t**3*curve[7]
            assert abs(math.hypot(x,y)-5)<.0014


def test_actual_final_cut_bytes_tampered_or_duplicated_rejected(tmp_path):
    item=finishing_project();p=profile(finishing_delivery='separate_process_pdf_v1');out=tmp_path/'result'
    render_print_artifacts(item,out,p,ICC.read_bytes(),test_mode=True)
    g=geometry_for_scene(item['scene']);paths=structure_paths(g,p['layout'])
    data={k:(out/('production.pdf' if k=='artwork' else k+'.pdf')).read_bytes() for k in ('artwork','cut','fold','process')}
    writer=PdfWriter(clone_from=BytesIO(data['cut']));page=writer.pages[0]
    altered=DecodedStreamObject();altered.set_data(page.get_contents().get_data()+b'\n 0 0 m 10 10 l S\n');page[NameObject('/Contents')]=writer._add_object(altered)
    raw=BytesIO();writer.write(raw);data['cut']=raw.getvalue()
    with pytest.raises(GeometryValidationError) as exc:verify_print_artifacts(data,item['scene'],g,paths,p,True)
    assert exc.value.code=='PRINT_FINISHING_PATH_MISMATCH'


def test_registered_net_mirrored_hole_notches_and_immutable_feature_binding(tmp_path):
    item=registered_finishing_project();p=profile(layout='net',finishing_delivery='separate_process_pdf_v1')
    result=render_print_artifacts(item,tmp_path/'net',p,ICC.read_bytes(),test_mode=True)
    path=result['finishing']['pages'][0]
    assert [(h['center_x_mm'],h['center_y_mm'],h['diameter_mm']) for h in path['holes']]==[(65,16,6),(65,504,6)]
    assert len(path['notches'])==4 and len(path['cut_curves'])==16
    spec=finishing_approval_for_geometry(item['structure_snapshot']['geometry'])
    assert validate_finishing_for_template('stand-up-pouch',{'width_mm':160,'height_mm':230,'bottom_mm':60},spec,registered_pouch())==spec
    bad=deepcopy(item['scene']);bad['pouch_features']['zipper_y_mm']=36
    with pytest.raises(GeometryValidationError) as exc:validate_scene(bad,structure_snapshot=item['structure_snapshot'])
    assert exc.value.code=='REGISTERED_FINISHING_MISMATCH'


def test_registered_physical_billing_hash_ignores_hole_ids_and_inactive_sliders():
    definition=registered_pouch();a=compile_structure(definition,definition['dimensions'],'first')
    definition['finishing']['holes'][0]['id']='new-ui-id'
    b=compile_structure(definition,definition['dimensions'],'second')
    assert a['physical_geometry_hash']==b['physical_geometry_hash'] and a['geometry_hash']!=b['geometry_hash']
    definition['finishing']['pouch_features'].update(zipper_enabled=False,tear_enabled=False)
    c=compile_structure(definition,definition['dimensions'],'third')
    definition['finishing']['pouch_features'].update(zipper_y_mm=50,tear_y_mm=18)
    d=compile_structure(definition,definition['dimensions'],'fourth')
    assert c['physical_geometry_hash']==d['physical_geometry_hash']


def test_missing_delivery_is_closed_and_legacy_outputs_unchanged(tmp_path):
    with pytest.raises(GeometryValidationError) as exc:inspect_print(finishing_project(),profile(),test_mode=True)
    assert exc.value.code=='FINISHING_DELIVERY_REQUIRED'
    item={'scene':new_scene('three-side-seal',160,230)}
    result=render_print_artifacts(item,tmp_path/'plain',profile(),ICC.read_bytes(),test_mode=True)
    assert 'finishing' not in result and 'finishing' not in result['verification']
    assert 'finishing_delivery' not in result['profile'] and len(list((tmp_path/'plain').iterdir()))==7


def test_registered_custom_top_seal_cannot_overlap_zipper():
    definition=separated();definition['feature_policy']='pouch-finishing-v1';definition['finishing']={'pouch_features':{}}
    definition['seals_mm']['top']=39
    definition['height_range_mm']['minimum']=100
    with pytest.raises(GeometryValidationError) as exc:compile_structure(definition,{'width_mm':160,'height_mm':230},'fixture')
    assert exc.value.code=='ZIPPER_SEAL_COLLISION'


@pytest.mark.parametrize('line',[{'x1_mm':50,'y1_mm':16,'x2_mm':80,'y2_mm':16},
                                {'x1_mm':20,'y1_mm':35,'x2_mm':140,'y2_mm':35},
                                {'x1_mm':0,'y1_mm':24,'x2_mm':10,'y2_mm':24}])
def test_registered_fold_cannot_cross_punch_zipper_or_notch(line):
    item=registered_finishing_project();geometry=deepcopy(item['structure_snapshot']['geometry'])
    geometry['fold_lines'].append(line)
    with pytest.raises(GeometryValidationError) as exc:structure_paths(geometry,'net')
    assert exc.value.code=='FINISHING_FOLD_COLLISION'


def test_attached_panel_cannot_turn_internal_join_into_a_notch():
    item=registered_finishing_project();geometry=deepcopy(item['structure_snapshot']['geometry'])
    # A registered side tab makes this notch non-exterior. No cut may be
    # invented across the internal join, regardless of a user's slider value.
    geometry['structural_parts']=[{'x_mm':160,'y_mm':10,'width_mm':10,'height_mm':30}]
    geometry['net_width_mm']=170
    with pytest.raises(GeometryValidationError) as exc:structure_paths(geometry,'net')
    assert exc.value.code=='FINISHING_NOTCH_NOT_EXTERIOR'


def test_minimum_size_hole_only_and_header_only_delivery(tmp_path):
    for name,features,holes in [('hole',None,[{'id':'hole','face_id':'front','center_x_mm':30,'center_y_mm':20,'diameter_mm':4}]),
                                ('header',{'header_height_mm':30,'zipper_enabled':False,'tear_enabled':False},[])]:
        scene=new_scene('three-side-seal',60,80);scene.update(pouch_features=features,holes=holes,geometry_hash=None)
        result=render_print_artifacts({'scene':scene},tmp_path/name,profile(finishing_delivery='separate_process_pdf_v1'),ICC.read_bytes(),test_mode=True)
        assert result['verification']['finishing']['cut_duplicate_check']=='passed'


def test_final_finishing_artwork_preserves_machine_readable_vector_barcode(tmp_path):
    from services.api.geometry import barcode_geometry
    item=finishing_project();barcode=barcode_geometry('9520000000011',barcode_usage='sample')
    item['scene']['faces'][1]['objects'].append({'id':'barcode','type':'barcode','face_id':'back','x_mm':30,'y_mm':120,
        'width_mm':barcode['width_mm'],'height_mm':barcode['height_mm'],'barcode_value':'9520000000011',
        'module_mm':.33,'bar_height_mm':22.85,'barcode_owned':False,'barcode_usage':'sample'})
    result=render_print_artifacts(item,tmp_path/'barcode',profile(finishing_delivery='separate_process_pdf_v1'),ICC.read_bytes(),test_mode=True)
    assert result['verification']['barcodes']==[{'face_id':'back','object_id':'barcode','value':'9520000000011','digital_decode':'passed','raster_dpi':300,'physical_scan':'not_tested'}]


@pytest.fixture
def approved_finishing_setup(cmyk_setup):
    from services.api.models import Project
    from services.api.feature_models import RegistryVersion
    s=cmyk_setup
    with s.factory() as db:
        project=db.get(Project,s.project_id);template=db.get(RegistryVersion,project.template_version_id);profile_row=db.get(RegistryVersion,project.print_profile_version_id)
        scene=deepcopy(project.scene);source=finishing_project()['scene']
        scene.update(holes=source['holes'],pouch_features=source['pouch_features'],geometry_hash=None,faces=source['faces'])
        scene=validate_scene(scene);project.scene=scene
        spec=finishing_approval_for_geometry(geometry_for_scene(scene))
        template.details={**template.details,'approved_finishing':spec};template.approval={**template.approval,'finishing_hash':canonical_hash(spec)}
        profile_row.details={**profile_row.details,'requirements':{**profile_row.details['requirements'],'finishing_delivery':'separate_process_pdf_v1'}}
        db.commit()
    return s


def test_worker_exact_approval_ten_files_changed_hole_charges_revert_is_free(approved_finishing_setup):
    from services.api.models import Project,Job
    from services.api.feature_models import RegistryVersion
    from services.api.production_jobs import process_production_jobs
    s=approved_finishing_setup
    with s.factory() as db:original_scene=deepcopy(db.get(Project,s.project_id).scene)
    first,_,_=enqueue(s);assert process_production_jobs(s.factory,s.storage,s.settings)==1
    with s.factory() as db:
        job=db.get(Job,first);assert job.status=='succeeded',job.error
        assert job.result['credits_charged']==40
        with ZipFile(BytesIO(s.storage.get(job.result['storage_key']))) as archive:
            assert len(archive.namelist())==11
            manifest=json.loads(archive.read('manifest.json'))
            from services.api.contracts.printing import PrintEngineManifest
            PrintEngineManifest.model_validate(manifest)
            assert manifest['finishing']['physical_specification_hash']==canonical_hash(manifest['finishing']['physical_specification'])
            assert all(x['passed'] for x in manifest['verification']['finishing']['path_checks'])
            for item in manifest['files']:assert sha256(archive.read(item['name'])).hexdigest()==item['sha256']
        project=db.get(Project,s.project_id);old=db.get(RegistryVersion,project.template_version_id)
        scene=deepcopy(project.scene);scene['holes'][0]['center_x_mm']=66;scene['geometry_hash']=None
        spec=finishing_approval_for_geometry(geometry_for_scene(validate_scene(scene)))
        replacement=RegistryVersion(kind='template',name='Changed physical hole fixture',manufacturer=old.manufacturer,status='approved',is_demo=False,created_by=old.created_by,
            details={**old.details,'approved_finishing':spec},approval={**old.approval,'finishing_hash':canonical_hash(spec)})
        db.add(replacement);db.flush();scene['template_version_id']=replacement.id
        project.template_version_id=replacement.id;project.scene=validate_scene(scene);project.base_revision+=1;db.commit()
    changed,_,_=enqueue(s);assert process_production_jobs(s.factory,s.storage,s.settings)==1
    with s.factory() as db:
        job=db.get(Job,changed);assert job.status=='succeeded',job.error
        assert job.result['credits_charged']==40
        project=db.get(Project,s.project_id);project.template_version_id=s.template_id;project.scene=original_scene;project.base_revision+=1;db.commit()
    restored,_,_=enqueue(s);assert process_production_jobs(s.factory,s.storage,s.settings)==1
    with s.factory() as db:
        job=db.get(Job,restored);assert job.status=='succeeded',job.error
        assert job.result['credits_charged']==0


@pytest.mark.parametrize('revocation',['status','hash','material','dimensions'])
def test_finishing_approval_rechecked_after_stored_bundle_no_capture(approved_finishing_setup,revocation):
    from services.api.models import Job
    from services.api.feature_models import RegistryVersion
    from services.api.billing.models import Reservation,LedgerEntry
    from services.api.production_jobs import process_production_jobs
    from sqlalchemy import select,func
    s=approved_finishing_setup;identity,_,_=enqueue(s);put=s.storage.put
    def changed(key,raw,mime):
        put(key,raw,mime)
        with s.factory() as db:
            template=db.get(RegistryVersion,s.template_id)
            if revocation=='status':template.status='revoked'
            elif revocation=='hash':template.approval={**template.approval,'finishing_hash':'0'*64}
            elif revocation=='material':template.details={**template.details,'material':'other material'}
            else:template.details={**template.details,'approved_dimensions':{'width_mm':161,'height_mm':230}}
            db.commit()
    s.storage.put=changed
    assert process_production_jobs(s.factory,s.storage,s.settings)==1
    with s.factory() as db:
        job=db.get(Job,identity);assert job.status=='failed' and job.result is None
        assert db.get(Reservation,job.snapshot['reservation_id']).status=='released'
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event=='CAPTURE'))==0
