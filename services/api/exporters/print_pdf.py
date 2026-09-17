"""Measured ordinary ICC CMYK PDF adapter. Never claims PDF/X or approval."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from PIL import features
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, DecodedStreamObject, TextStringObject
from reportlab.lib.colors import CMYKColor, HexColor
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from ..geometry import validate_scene, geometry_for_scene
from ..geometry.print_paths import structure_paths
from ..geometry.collisions import bounds
from ..image_quality_metadata import resolver_quality_metrics
from .print_profile import parse_print_profile, ADAPTER_ID, capabilities
from .print_color import PrintColor
from .font_outlines import draw_outline_line
from .review_pdf import _draw_object, _layout_text, _font_manifest, _font, ExportValidationError


class PrintCanvas(Canvas):
    def __init__(self, *args, paint, **kwargs): self.paint=paint; super().__init__(*args, **kwargs)
    def setFillColor(self, color, alpha=None):
        return super().setFillColor(color if isinstance(color,CMYKColor) else self.paint.color(color),alpha)
    def setStrokeColor(self, color, alpha=None):
        return super().setStrokeColor(color if isinstance(color,CMYKColor) else self.paint.color(color),alpha)


class Paint(PrintColor):
    line=staticmethod(draw_outline_line)


def inspect_print(project, profile, resolver=None, *, test_mode=False):
    p=parse_print_profile(profile)
    scene=validate_scene(project["scene"],structure_snapshot=project.get("structure_snapshot"))
    geometry=geometry_for_scene(scene,structure_snapshot=project.get("structure_snapshot"))
    paths=structure_paths(geometry,p["layout"])
    issues=[]
    def add(code,message,**kw):issues.append({"code":code,"message":message,"severity":"warning" if test_mode else "error",**kw})
    if p["layout"]=="face_pages" and scene.get("template_kind","three-side-seal")!="three-side-seal":
        raise ExportValidationError("PRINT_LAYOUT_REQUIRED","상자·스탠드 구조는 등록된 전개도 출력이 필요합니다. 면별 직사각 칼선을 실제 전개도로 사용하지 않습니다.")
    for face in scene["faces"]:
        for obj in face["objects"]:
            if not obj["visible"] or not obj["print_enabled"]:continue
            info={"face_id":face["id"],"object_id":obj["id"]}
            if obj["opacity"]!=1:raise ExportValidationError("PRINT_TRANSPARENCY_UNSUPPORTED","반투명 객체는 제작 엔진에서 지원하지 않습니다.",obj["id"])
            if obj["type"]=="text":_layout_text(obj,resolver)
            if obj["type"]=="barcode" and obj.get("barcode_usage")=="sample":add("SAMPLE_BARCODE_PRODUCTION_FORBIDDEN","샘플 바코드는 시험용입니다.",**info)
            if obj["type"]=="image":
                from .review_pdf import _resolve_image
                _,pixels=_resolve_image(obj["asset_id"],resolver)
                metrics=resolver_quality_metrics(resolver,obj["asset_id"],pixels,obj)
                for key,code in (("effective_ppi","LOW_PPI"),("original_effective_ppi","ORIGINAL_LOW_PPI")):
                    if metrics[key]+.001<p["min_ppi"]:add(code,f"이미지 {key} {metrics[key]:.2f}ppi가 최소 {p['min_ppi']:g}ppi보다 낮습니다.",**info)
                if metrics["extended"]:add("PRINT_SYNTHETIC_BLEED","도련이 합성 연장된 이미지입니다. 제조 조건에 대한 별도 확인이 필요합니다.",**info)
            if obj['type'] in ('image','shape'):
                x1,y1,x2,y2=bounds(obj); b=p["bleed_mm"]; w,h=face["width_mm"],face["height_mm"]
                missing=[]
                for name,touches,covers in (("left",x1<=.01 and x2>0,x1<=-b+.0001),("top",y1<=.01 and y2>0,y1<=-b+.0001),
                                            ("right",x2>=w-.01 and x1<w,x2>=w+b-.0001),("bottom",y2>=h-.01 and y1<h,y2>=h+b-.0001)):
                    if b and touches and (not covers or obj["rotation_deg"]%360):missing.append(name)
                if missing:add("PRINT_IMAGE_BLEED_MISSING" if obj['type']=='image' else 'PRINT_SHAPE_BLEED_MISSING',"재단선에 닿는 아트가 요구 도련을 덮지 않습니다. 부족 영역은 면 바탕색으로만 채워집니다.",edges=missing,**info)
    return {"profile":p,"scene":scene,"geometry":geometry,"paths":paths,"issues":issues}


def _embed_icc(data,icc):
    writer=PdfWriter(clone_from=BytesIO(data))
    stream=DecodedStreamObject();stream.set_data(icc);stream[NameObject('/N')]=NumberObject(4)
    stream[NameObject('/Alternate')]=NameObject('/DeviceCMYK');ref=writer._add_object(stream)
    space=ArrayObject([NameObject('/ICCBased'),ref])
    for page in writer.pages:
        resources=page['/Resources']; spaces=resources.get('/ColorSpace',DictionaryObject())
        spaces[NameObject('/DefaultCMYK')]=space;resources[NameObject('/ColorSpace')]=spaces
        for objref in resources.get('/XObject',{}).values():
            obj=objref.get_object()
            if obj.get('/Subtype')=='/Image' and obj.get('/ColorSpace')=='/DeviceCMYK':obj[NameObject('/ColorSpace')]=space
    # An output intent describes the printing condition; it is not a PDF/X declaration.
    intent=DictionaryObject({NameObject('/Type'):NameObject('/OutputIntent'),NameObject('/S'):NameObject('/GTS_PDFX'),
        NameObject('/OutputConditionIdentifier'):TextStringObject('ICC SHA256 '+sha256(icc).hexdigest()),
        NameObject('/Info'):TextStringObject('Ordinary PDF. PDF/X conformance NOT claimed.'),NameObject('/DestOutputProfile'):ref})
    writer._root_object[NameObject('/OutputIntents')]=ArrayObject([writer._add_object(intent)])
    output=BytesIO();writer.write(output);return output.getvalue()


def _page(canvas,w,h,bleed,test):
    footer=12 if test else 0
    canvas.setPageSize(((w+2*bleed)*mm,(h+2*bleed+footer)*mm))
    canvas.setTrimBox((bleed*mm,(bleed+footer)*mm,(w+bleed)*mm,(h+bleed+footer)*mm))
    canvas.setBleedBox((0,footer*mm,(w+2*bleed)*mm,(h+2*bleed+footer)*mm))
    if test:
        canvas.setFillColor(CMYKColor(0,0,0,1))
        draw_outline_line(canvas,"ENGINE TEST / 검토용 · 제작 사용 불가",4*mm,4*mm,7)
    canvas.translate(bleed*mm,(bleed+footer)*mm)


def _paint_face(canvas,face,structural,paint,resolver,b,others=()):
    w,h=face["width_mm"],face["height_mm"]
    canvas.saveState()
    clip=canvas.beginPath();clip.rect(-b*mm,-b*mm,(w+2*b)*mm,(h+2*b)*mm);canvas.clipPath(clip,stroke=0)
    # Net art must not paint another face or an unprinted glue/flap region.
    if others:
        cut=canvas.beginPath();cut.rect(-b*mm,-b*mm,(w+2*b)*mm,(h+2*b)*mm)
        for x,y,rw,rh in others:cut.rect(x*mm,(h-y-rh)*mm,rw*mm,rh*mm)
        canvas.clipPath(cut,stroke=0,fillMode=0)
    canvas.setFillColor(HexColor(face["background"]));canvas.rect(-b*mm,-b*mm,(w+2*b)*mm,(h+2*b)*mm,fill=1,stroke=0)
    for obj in sorted(face["objects"],key=lambda o:o["z_index"]):_draw_object(canvas,obj,h,resolver,[],print_paint=paint)
    canvas.restoreState()


def render_print_artifacts(project, output_dir, profile, icc, resolver=None, *, test_mode=False):
    checked=inspect_print(project,profile,resolver,test_mode=test_mode)
    if any(i["severity"]=="error" for i in checked["issues"]):
        issue=checked["issues"][0];raise ExportValidationError(issue["code"],issue["message"])
    p,scene,g,paths=(checked[k] for k in ("profile","scene","geometry","paths"));b=p["bleed_mm"]
    _font()  # Barcode human-readable digits use the bundled font even on text-free scenes.
    paint=Paint(icc,p);out=Path(output_dir);out.mkdir(parents=True,exist_ok=False)
    lookup={f["id"]:f for f in scene["faces"]};payloads={}
    for role in ("artwork","cut","fold"):
        stream=BytesIO();c=PrintCanvas(stream,paint=paint,invariant=1,pageCompression=1,pdfVersion=(1,5),enforceColorSpace="CMYK")
        c.setTitle(f"Phoenix {role.upper()} — {'ENGINE TEST / NOT FOR PRODUCTION' if test_mode else 'ordinary ICC PDF'}")
        for page in paths:
            w,h=page["width_mm"],page["height_mm"]
            _page(c,w,h,b,test_mode)
            if role=="artwork":
                if p["layout"]=="face_pages":_paint_face(c,lookup[page["face_id"]],None,paint,resolver,b)
                else:
                    for sf in g["faces"]:
                        face=lookup[sf["id"]];net=sf["net"];x,y=net["x_mm"],net["y_mm"];fw,fh=face["width_mm"],face["height_mm"]
                        c.saveState();c.translate(x*mm,(h-y-fh)*mm)
                        others=[]
                        for rx,ry,rw,rh in page["rectangles"]:
                            if (rx,ry,rw,rh)==(x,y,fw,fh):continue
                            lx,ly=rx-x,ry-y
                            if net["rotation_deg"]==180:lx,ly=fw-lx-rw,fh-ly-rh
                            others.append((lx,ly,rw,rh))
                        if net["rotation_deg"]==180:c.translate(fw*mm,fh*mm);c.rotate(180)
                        _paint_face(c,face,sf,paint,resolver,b,others);c.restoreState()
            else:
                c.setStrokeColor(CMYKColor(0,0,0,1));c.setLineWidth(.1*mm)
                for x1,y1,x2,y2 in page[role]:c.line(x1*mm,(h-y1)*mm,x2*mm,(h-y2)*mm)
            c.showPage()
        c.save();data=_embed_icc(stream.getvalue(),icc)
        name='production.pdf' if role=='artwork' else role+'.pdf';(out/name).write_bytes(data);payloads[role]=data
    # The preview is a screen rendering, not a physical contract proof.
    import pypdfium2 as pdfium
    from PIL import Image
    document=pdfium.PdfDocument(payloads['artwork']);tiles=[]
    try:
        for index in range(len(document)):
            page=document[index]
            try:
                bitmap=page.render(scale=min(2,420/page.get_width(),600/page.get_height()))
                try:tiles.append(bitmap.to_pil().convert('RGB').copy())
                finally:bitmap.close()
            finally:page.close()
    finally:document.close()
    preview=Image.new('RGB',(450*min(3,len(tiles)),630*((len(tiles)+2)//3)),'#eeeae2')
    for index,tile in enumerate(tiles):preview.paste(tile,((index%3)*450+15,(index//3)*630+15))
    preview.save(out/'preview.png')
    from .print_verification import verify_print_artifacts
    verified=verify_print_artifacts(payloads,scene,g,paths,p,test_mode)
    (out/'preflight.json').write_text(json.dumps({"issues":checked["issues"],"verification":verified},ensure_ascii=False,indent=2),encoding='utf-8')
    manifest={"schema_version":"2.0","adapter":ADAPTER_ID,"kind":"print_engine_test" if test_mode else "production",
        "review_only":test_mode,"manufacturer_approval":False,"pdf_x_conformance":"not_claimed","profile":p,
        "geometry_hash":g["geometry_hash"],"structure_ref":deepcopy(scene.get("structure_ref")),"icc":paint.info,
        "engine":{"littlecms":features.version('littlecms2')},"fonts":[{**f,"embedded":False,"outlined":True} for f in _font_manifest(scene,resolver)],
        "original_texts":[{"face_id":f["id"],"object_id":o["id"],"text":o["text"]} for f in scene["faces"] for o in f["objects"] if o["type"]=="text" and o["visible"] and o["print_enabled"]],
        "images":paint.records,"issues":checked["issues"],"verification":verified,
        "files":[{"name":file.name,"sha256":sha256(file.read_bytes()).hexdigest(),"bytes":file.stat().st_size} for file in sorted(out.iterdir())]}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest
