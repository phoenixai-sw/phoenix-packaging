"""The same authorized static TTF drives wrapping, embedded review and outlines."""
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import pytest
from fontTools import subset
from fontTools.ttLib import TTFont
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics
from services.api.font_assets.types import FontSource
from services.api.geometry import new_scene,validate_scene,GeometryValidationError
from services.api.exporters.review_pdf import FONT_PATH,_text_font_id,_layout_text,_width,render_review_pdf,export_review_pdf
from services.api.exporters.print_pdf import render_print_artifacts
from tests.geometry_pdf.test_print_engine import profile,ICC


@pytest.fixture(scope='module')
def font_source():
    font=TTFont(FONT_PATH)
    option=subset.Options();option.name_IDs=['*'];option.name_legacy=True;option.name_languages=['*']
    sub=subset.Subsetter(options=option);sub.populate(text='한글 오리스틱 가나 ABC 37.5g × 4검토용');sub.subset(font)
    # Alter one actual advance: falling back to the bundled font cannot pass.
    glyph=font.getBestCmap()[ord('가')];advance,side=font['hmtx'].metrics[glyph];font['hmtx'].metrics[glyph]=(advance*2,side)
    for name in font['name'].names:
        if name.nameID in (1,4,6):name.string='PhoenixQAStatic'.encode(name.getEncoding())
    stream=BytesIO();font.save(stream);raw=stream.getvalue()
    return FontSource(asset_id='4eab88f6-10c3-4e6c-947c-9a574fc02066',sha256=sha256(raw).hexdigest(),family='PhoenixQAStatic',weight=400,data=raw,license_name='OFL-1.1',redistribution_allowed=True)


def resolver(source):
    def assets(_):raise AssertionError('No raster asset in font fixture')
    assets.font=lambda identity:source if identity==source.asset_id else (_ for _ in ()).throw(ValueError('unknown font'))
    return assets


def scene(source):
    value=new_scene('three-side-seal',160,230)
    value['faces'][0]['objects']=[{'id':'text','type':'text','face_id':'front','font_asset_id':source.asset_id,'font_weight':source.weight,
        'x_mm':25,'y_mm':40,'width_mm':115,'height_mm':40,'font_size_pt':24,'text':'한글 오리스틱\n가나 ABC 37.5g × 4','color':'#000000'}]
    return validate_scene(value)


def test_custom_metrics_and_wrapping_use_actual_bytes(font_source):
    obj=scene(font_source)['faces'][0]['objects'][0];access=resolver(font_source)
    custom=_text_font_id(obj,access);builtin=_text_font_id({'font_weight':400})
    assert custom=='PhoenixFont_'+font_source.sha256
    assert _width('가',24,0,custom)==pytest.approx(_width('가',24,0,builtin)*2)
    obj.update(text='가가가가',width_mm=_width('가',24,0,custom)*2*25.4/72+.001)
    assert _layout_text(obj,access)==['가가','가가']
    assert _layout_text({**obj,'font_asset_id':None})==['가가가가']


def test_same_postscript_name_different_font_bytes_never_alias(font_source):
    first=scene(font_source)['faces'][0]['objects'][0]
    first_id=_text_font_id(first,resolver(font_source))
    before=_width('가',24,0,first_id)
    font=TTFont(BytesIO(font_source.data));glyph=font.getBestCmap()[ord('가')]
    advance,side=font['hmtx'].metrics[glyph];font['hmtx'].metrics[glyph]=(advance*2,side)
    output=BytesIO();font.save(output);data=output.getvalue()
    second_source=replace(font_source,asset_id='575c4fbd-fd1e-4987-823d-7ca526957071',sha256=sha256(data).hexdigest(),data=data)
    second=scene(second_source)['faces'][0]['objects'][0]
    second_id=_text_font_id(second,resolver(second_source))
    assert second_id != first_id
    assert _width('가',24,0,first_id)==before
    assert _width('가',24,0,second_id)==pytest.approx(before*2)


def test_custom_font_never_silently_falls_back(font_source):
    obj=scene(font_source)['faces'][0]['objects'][0]
    for source,code in ((None,'FONT_ASSET_UNAVAILABLE'),(replace(font_source,sha256='0'*64),'FONT_HASH_MISMATCH'),(replace(font_source,weight=700),'FONT_ASSET_MISMATCH')):
        with pytest.raises(GeometryValidationError) as exc:_layout_text(obj,resolver(source) if source else None)
        assert exc.value.code==code
    with pytest.raises(GeometryValidationError) as exc:_layout_text({**obj,'text':'없는 字'},resolver(font_source))
    assert exc.value.code=='MISSING_GLYPH'


@pytest.mark.parametrize('text',['\u1100\u1161','A\u0301','\u0627'])
def test_custom_shaping_is_blocked_before_inconsistent_embedding(font_source,text):
    obj=scene(font_source)['faces'][0]['objects'][0]
    with pytest.raises(GeometryValidationError) as exc:_layout_text({**obj,'text':text},resolver(font_source))
    assert exc.value.code=='FONT_SHAPING_UNSUPPORTED'


@pytest.mark.parametrize('weight',[1,500,1000])
def test_custom_weight_range_has_no_builtin_substitution(font_source,weight):
    source=replace(font_source,weight=weight)
    obj=scene(source)['faces'][0]['objects'][0]
    assert obj['font_weight']==weight
    assert _text_font_id(obj,resolver(source))=='PhoenixFont_'+source.sha256
    with pytest.raises(GeometryValidationError):validate_scene({**scene(source),'faces':[{**scene(source)['faces'][0],'objects':[{**obj,'font_asset_id':None}]},scene(source)['faces'][1]]})


@pytest.mark.parametrize('identity,weight',[(None,500),('not-an-id',400),([],400),('4eab88f6-10c3-4e6c-947c-9a574fc02066',True),('4eab88f6-10c3-4e6c-947c-9a574fc02066',1001)])
def test_geometry_rejects_malformed_font_reference(font_source,identity,weight):
    value=scene(font_source);value['faces'][0]['objects'][0].update(font_asset_id=identity,font_weight=weight)
    with pytest.raises(GeometryValidationError):validate_scene(value)


def test_actual_custom_font_embedding_outline_and_manifest_match(tmp_path,font_source):
    from services.api.contracts.printing import ReviewManifest,PrintEngineManifest
    value=scene(font_source);project={'scene':value,'review_profile_id':'phoenix-basic-review-v1'};access=resolver(font_source)
    review=tmp_path/'review.pdf';review_manifest=export_review_pdf(project,review,access)
    ReviewManifest.model_validate(review_manifest)
    custom=next(f for f in review_manifest['font_weights'] if f.get('font_asset_id'))
    assert custom['sha256']==font_source.sha256 and custom['family']=='PhoenixQAStatic' and custom['embedded']
    assert '한글 오리스틱' in PdfReader(review).pages[0].extract_text()
    assert any('PhoenixFont_'+font_source.sha256 in f for f in review_manifest['pdf_verification']['used_fonts_embedded'])
    output=tmp_path/'outlined';manifest=render_print_artifacts(project,output,profile(),ICC.read_bytes(),access,test_mode=True)
    PrintEngineManifest.model_validate(manifest)
    custom=next(f for f in manifest['fonts'] if f.get('font_asset_id'))
    assert custom['sha256']==font_source.sha256 and custom['outlined'] and not custom['embedded']
    assert not PdfReader(output/'production.pdf').pages[0].extract_text()
    # Compare actual ink positions from independently rendered PDF bytes.
    import pypdfium2 as pdfium
    from PIL import ImageChops
    masks=[]
    for file in (review,output/'production.pdf'):
        doc=pdfium.PdfDocument(file);page=doc[0]
        try:
            bitmap=page.render(scale=300/72)
            try:
                image=bitmap.to_pil().convert('RGB');factor=300/25.4
                box=tuple(round(v*factor) for v in (28,43,143,83))
                crop=image.crop(box).convert('L').point(lambda v:255 if v<128 else 0)
                masks.append(crop)
            finally:bitmap.close()
        finally:page.close();doc.close()
    from PIL import ImageStat
    differing=ImageStat.Stat(ImageChops.difference(*masks)).sum[0]/255
    ink=sum(ImageStat.Stat(m).sum[0]/255 for m in masks)/2
    assert differing/max(ink,1)<.20  # embedded hinting vs vector outline antialiasing
