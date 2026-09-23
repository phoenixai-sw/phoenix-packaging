from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import pytest
from PIL import Image
from pypdf import PdfReader
from services.api.geometry import new_scene,validate_scene,barcode_geometry,GeometryValidationError
from services.api.exporters.print_color import inspect_icc,PrintColor
from services.api.exporters.print_profile import parse_print_profile
from services.api.exporters.print_pdf import render_print_artifacts

ICC=Path(__file__).resolve().parents[2]/'fixtures/icc/synthetic-cmyk-test.icc'


def profile(**overrides):
    return parse_print_profile({'icc_id':'test-fixture','icc_sha256':sha256(ICC.read_bytes()).hexdigest(),**overrides})


def project():
    scene=new_scene('three-side-seal',160,230)
    for f in scene['faces']:
        f['objects']=[{'id':f['id']+'-text','type':'text','face_id':f['id'],'x_mm':20,'y_mm':40,'width_mm':110,'height_mm':30,
                       'font_size_pt':24,'font_weight':700 if f['id']=='front' else 400,'text':'한글 오리스틱 37.5g × 4','color':'#103A55'}]
    code=barcode_geometry('9520000000011',barcode_usage='sample')
    scene['faces'][1]['objects'].append({'id':'barcode','type':'barcode','face_id':'back','x_mm':20,'y_mm':100,
        'width_mm':code['width_mm'],'height_mm':code['height_mm'],'barcode_value':'9520000000011','module_mm':.33,'bar_height_mm':22.85,'barcode_owned':False,'barcode_usage':'sample'})
    return {'id':'engine-fixture','scene':validate_scene(scene),'revision_id':'1'}


def test_icc_transform_changes_real_channels_and_validates_integrity():
    raw=ICC.read_bytes();assert inspect_icc(raw)['color_space']=='CMYK'
    from reportlab.lib.colors import HexColor
    color=PrintColor(raw,profile()).color(HexColor('#FF8000'))
    assert color.cyan==0 and color.magenta>0 and color.yellow>0
    assert PrintColor(raw,profile()).color(HexColor('#000000')).black==1
    with pytest.raises(GeometryValidationError,match='ICC'):PrintColor(raw,profile(icc_sha256='0'*64))
    for broken in (raw[:100],raw+b'x',raw[:36]+b'nope'+raw[40:]):
        with pytest.raises(GeometryValidationError):inspect_icc(broken)


@pytest.mark.parametrize('bleed',[0,2,3,3.175,10])
def test_actual_cmyk_outlined_pdf_boxes_barcode_and_original_text(tmp_path,bleed):
    item=project();out=tmp_path/'print'
    result=render_print_artifacts(item,out,profile(bleed_mm=bleed),ICC.read_bytes(),test_mode=True)
    assert result['verification']['barcodes'][0]['value']=='9520000000011'
    assert result['original_texts'][0]['text']==item['scene']['faces'][0]['objects'][0]['text']
    reader=PdfReader(out/'production.pdf');assert len(reader.pages)==2
    assert reader.pages[0].extract_text()==''
    assert float(reader.pages[0].mediabox.width)*25.4/72==pytest.approx(160+2*bleed,abs=.01)
    assert [f['weight'] for f in result['fonts']]==[400,700]
    assert (out/'cut.pdf').is_file() and (out/'fold.pdf').is_file() and (out/'preview.png').is_file()
    assert result['pdf_x_conformance']=='not_claimed'
    from services.api.contracts.printing import PrintEngineManifest
    PrintEngineManifest.model_validate(result)


def test_image_conversion_crop_pixels_and_original_resolution_warning(tmp_path):
    item=project();f=item['scene']['faces'][0]
    f['objects'].append({'id':'image','type':'image','face_id':'front','x_mm':30,'y_mm':130,'width_mm':80,'height_mm':60,'asset_id':'asset',
        'crop':{'x':.2,'y':.25,'width':.5,'height':.5}})
    raw=BytesIO();Image.new('RGB',(400,300),'#AA3366').save(raw,format='PNG')
    result=render_print_artifacts(item,tmp_path/'print',profile(),ICC.read_bytes(),lambda _:raw.getvalue(),test_mode=True)
    assert result['images'][0]['output_mode']=='CMYK'
    assert any(i['code']=='LOW_PPI' for i in result['issues'])
    assert result['images'][0]['pixels']==[400,300]


def test_net_cut_has_no_shared_panel_edges_and_same_art_registration(tmp_path):
    from tests.geometry_pdf.test_structure_v2 import fixed_box
    from services.api.geometry.snapshots import compile_structure,structure_ref
    definition=fixed_box();snap=compile_structure(definition,definition['dimensions'],'own-test')
    scene=new_scene('folding-box',160,230,depth_mm=60)
    scene.update(template_version_id='own-test',structure_ref=structure_ref(snap),geometry_hash=snap['geometry_hash'])
    result=render_print_artifacts({'scene':scene,'structure_snapshot':snap},tmp_path/'print',profile(layout='net'),ICC.read_bytes(),test_mode=True)
    assert len(result['verification']['page_checks'])==4
    from services.api.geometry.print_paths import structure_paths
    page=structure_paths(snap['geometry'],'net')[0]
    cuts={tuple(sorted((tuple(line[:2]),tuple(line[2:])))) for line in page['cut']}
    assert len(cuts)==len(page['cut'])
    assert not any(x1==x2==175 and y1==0 and y2==230 for x1,y1,x2,y2 in page['cut'])
    # A top flap touching its neighbor must separate along an internal slit.
    assert any(x1==x2==175 and y1==33 and y2==60 for x1,y1,x2,y2 in page['cut'])
    # The front/right body connection remains a crease, not a duplicate cut.
    assert not any(x1==x2==175 and y1>=60 and y2<=290 for x1,y1,x2,y2 in page['cut'])


@pytest.mark.parametrize('fields',[{'pdf_standard':'PDF/X-4'},{'white_ink':True},{'spot_colors':True},{'overprint':True},{'bleed_mm':float('nan')},{'bleed_mm':True}])
def test_unsupported_profile_is_rejected(fields):
    with pytest.raises(GeometryValidationError):profile(**fields)


def test_transparency_is_rejected_instead_of_rasterizing_vector_art(tmp_path):
    item=project();item['scene']['faces'][0]['objects'][0]['opacity']=.5
    with pytest.raises(GeometryValidationError,match='반투명'):
        render_print_artifacts(item,tmp_path/'print',profile(),ICC.read_bytes(),test_mode=True)


def test_production_image_without_real_bleed_is_blocked(tmp_path):
    item=project();item['scene']['faces'][0]['objects'].append({'id':'image','type':'image','face_id':'front','x_mm':0,'y_mm':0,'width_mm':160,'height_mm':230,'asset_id':'asset'})
    raw=BytesIO();Image.new('RGB',(200,300),'red').save(raw,format='PNG')
    with pytest.raises(GeometryValidationError):render_print_artifacts(item,tmp_path/'print',profile(min_ppi=72),ICC.read_bytes(),lambda _:raw.getvalue())


def test_fractional_bleed_shape_shortfall_is_not_hidden_by_background():
    from services.api.exporters.print_pdf import inspect_print
    item=project();item['scene']['faces'][0]['objects'].append({'id':'shape','type':'shape','face_id':'front','x_mm':-3,'y_mm':-3,'width_mm':166,'height_mm':236,'fill':'#FF0000'})
    checked=inspect_print(item,profile(bleed_mm=3.175))
    issue=next(i for i in checked['issues'] if i['code']=='PRINT_SHAPE_BLEED_MISSING')
    assert issue['severity']=='error' and set(issue['edges'])=={'left','top','right','bottom'}


def test_partial_cut_fold_overlap_is_blocked():
    from tests.geometry_pdf.test_structure_v2 import fixed_box
    from services.api.geometry.snapshots import compile_structure
    from services.api.geometry.print_paths import structure_paths
    definition=fixed_box();geometry=compile_structure(definition,definition['dimensions'],'test')['geometry']
    cut=structure_paths(geometry,'net')[0]['cut'][0]
    x1,y1,x2,y2=cut
    geometry['fold_lines'].append({'x1_mm':(x1+x2)/2,'y1_mm':(y1+y2)/2,'x2_mm':x2,'y2_mm':y2})
    with pytest.raises(GeometryValidationError) as exc:structure_paths(geometry,'net')
    assert exc.value.code=='PRINT_CUT_FOLD_OVERLAP'


@pytest.mark.parametrize('line,code',[
    ({'x1_mm':20,'y1_mm':80,'x2_mm':60,'y2_mm':120},'PRINT_DIAGONAL_FOLD_UNSUPPORTED'),
    ({'x1_mm':20,'y1_mm':10,'x2_mm':400,'y2_mm':10},'PRINT_FOLD_OUTSIDE_MATERIAL')])
def test_unverified_fold_paths_are_blocked(line,code):
    from tests.geometry_pdf.test_structure_v2 import fixed_box
    from services.api.geometry.snapshots import compile_structure
    from services.api.geometry.print_paths import structure_paths
    definition=fixed_box();geometry=compile_structure(definition,definition['dimensions'],'test')['geometry']
    geometry['fold_lines'].append(line)
    with pytest.raises(GeometryValidationError) as exc:structure_paths(geometry,'net')
    assert exc.value.code==code


def test_structural_identity_changes_only_new_fingerprint():
    from services.api.billing.service import production_fingerprint
    identity={'brand_id':'brand','product_variant_id':'variant','billing_family_key':'family','content_amount':100,'content_unit':'g','width_mm':160,'height_mm':230}
    legacy=production_fingerprint('tenant',identity)
    assert production_fingerprint('tenant',dict(identity))==legacy
    assert production_fingerprint('tenant',{**identity,'structure_geometry_hash':'a'*64})!=legacy
    assert production_fingerprint('tenant',{**identity,'structure_geometry_hash':'a'*64})!=production_fingerprint('tenant',{**identity,'structure_geometry_hash':'b'*64})


def test_registered_stand_pouch_net_closes_around_the_gusset(tmp_path):
    """The shipped stand-pouch definition must cut one closed outline and crease the gusset.

    A stand pouch is the case a rectangular per-face die-line gets wrong: the bottom gusset folds
    inside, so the flat net is front, gusset, then the back upside down. The cut has to run around
    all three as one shape and the creases have to land on the gusset's two edges and its middle."""
    from services.api.geometry.snapshots import compile_structure
    from services.api.geometry.print_paths import structure_paths
    definition=json.loads((Path(__file__).resolve().parents[2]/'fixtures/structures/stand-up-pouch-260x340x120.json').read_text(encoding='utf-8'))
    snap=compile_structure(definition,definition['dimensions'],'fixture-stand-pouch')
    geometry=snap['geometry']
    assert [geometry['net_width_mm'],geometry['net_height_mm']]==[260,800]
    assert all(face['registered_structure'] for face in geometry['faces'])
    pages=structure_paths(geometry,'net')
    assert len(pages)==1 and pages[0]['face_id']=='net'
    cut=pages[0]['cut']
    assert len(cut)==8
    # One closed ring: every point is left exactly once and arrived at exactly once.
    starts=sorted(tuple(line[:2]) for line in cut)
    assert starts==sorted(tuple(line[2:]) for line in cut)
    assert len(set(starts))==len(starts)
    xs={line[0] for line in cut}|{line[2] for line in cut}
    ys={line[1] for line in cut}|{line[3] for line in cut}
    assert xs=={0,260} and ys=={0,340,460,800}
    folds=sorted(line[1] for line in pages[0]['fold'])
    assert folds==[340,400,460], 'gusset top, middle and bottom'
    assert all(line[1]==line[3] for line in pages[0]['fold']), 'every crease runs across the web'


def test_a_panel_fold_survives_a_net_placement_with_no_rotation():
    """An unrotated panel may leave `rotation_deg` off; that is not a reason to crash.

    Demo stand-pouch geometry writes the gusset's net placement without `rotation_deg`, and the
    gusset is the panel that carries a fold. Reading the key directly turned that into a KeyError
    instead of a die-line."""
    from services.api.geometry.print_paths import structure_paths
    face=lambda i,y,h,net:{'id':i,'width_mm':100,'height_mm':h,'registered_structure':True,'net':net,
        'regions':{'fold':[{'x1_mm':0,'y1_mm':h/2,'x2_mm':100,'y2_mm':h/2}] if i=='bottom' else []}}
    geometry={'faces':[face('front',0,200,{'x_mm':0,'y_mm':0,'rotation_deg':0}),
                       face('bottom',200,80,{'x_mm':0,'y_mm':200}),  # no rotation_deg, as the demo writes it
                       face('back',280,200,{'x_mm':0,'y_mm':280,'rotation_deg':180})],
              'structural_parts':[],'fold_lines':[],'net_width_mm':100,'net_height_mm':480}
    pages=structure_paths(geometry,'net')
    assert [line[1] for line in pages[0]['fold']]==[240], 'the crease sits mid-gusset, unrotated'
