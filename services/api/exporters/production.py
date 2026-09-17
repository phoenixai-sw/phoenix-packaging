"""Narrow, real RGB face-page PDF adapter. Unsupported print requirements fail closed."""
from datetime import datetime,timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import tempfile
from PIL import Image,ImageDraw,ImageFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from .preflight import preflight_project,CAPABILITIES
from .review_pdf import ExportValidationError,_scene_from_project,_font,FONT_ID,FONT_PATH,_draw_object,_font_manifest
from ..geometry import validate_scene,geometry_for_scene
from ..geometry.collisions import bounds


def _json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,default=str)+"\n",encoding="utf-8")


def _production_pdf(scene,resolver):
    output=BytesIO();canvas=Canvas(output,pageCompression=1,invariant=1)
    canvas.setTitle("Phoenix Packaging — approved RGB face pages")
    _font();lookup={face["id"]:face for face in scene["faces"]}
    for structural in geometry_for_scene(scene)["faces"]:
        face=lookup[structural["id"]];w,h=face["width_mm"]*mm,face["height_mm"]*mm
        canvas.setPageSize((w,h));canvas.setTrimBox((0,0,w,h));canvas.setBleedBox((0,0,w,h))
        canvas.setFillColor(HexColor(face["background"]));canvas.rect(0,0,w,h,fill=1,stroke=0)
        for obj in sorted(face["objects"],key=lambda o:o["z_index"]):
            _draw_object(canvas,obj,face["height_mm"],resolver,[])
        canvas.showPage()
    canvas.save();return output.getvalue()


def _ticket_pdf(path,ticket):
    canvas=Canvas(str(path),pagesize=(210*mm,297*mm),invariant=1)
    canvas.setTitle("Phoenix Packaging 작업지시서")
    lines=["Phoenix Packaging 작업지시서",f"프로젝트: {ticket['project_id']}",f"리비전: {ticket['revision_id']}",
           f"템플릿 버전: {ticket['template_id']}",f"프로파일: {ticket['profile_id']}",f"재질: {ticket['material']}",
           "출력: 일반 PDF / RGB / 글꼴 임베드 / 면별 페이지 / 블리드 0mm", "프로파일에 명시된 기본 출력 조건만 지원합니다.",
           "PDF/X, CMYK, 별색, 화이트 잉크, 오버프린트, 칼선 출력은 포함하지 않습니다."]
    lines += [f"{face['name']}: {face['width_mm']:g} × {face['height_mm']:g} mm" for face in ticket["faces"]]
    lines += ["고객 확인: "+", ".join(ticket["confirmed_fields"]),"바코드 실물 판독 및 제조 공정 적합성은 제조사가 최종 확인합니다."]
    y=280*mm;canvas.setFont(FONT_ID,10)
    for text in lines:
        line=""
        for char in text:
            if canvas.stringWidth(line+char,FONT_ID,10)>180*mm:
                canvas.drawString(15*mm,y,line);y-=6*mm;line=char
            else:line+=char
        canvas.drawString(15*mm,y,line);y-=7*mm
    canvas.save()


def _decode_barcodes(document,scene,*,bleed_mm=0):
    """Decode the actual final PDF, independently of the encoder, before publishing."""
    import zxingcpp
    lookup={f["id"]:f for f in scene["faces"]};checks=[]
    for index,structural in enumerate(geometry_for_scene(scene)["faces"]):
        face=lookup[structural["id"]]
        for obj in face["objects"]:
            if obj["type"]!="barcode" or not obj["visible"] or not obj["print_enabled"]:continue
            left,top,right,bottom=bounds(obj)
            crop=(max(0,left+bleed_mm-2)*mm,max(0,face["height_mm"]+bleed_mm-bottom-2)*mm,max(0,face["width_mm"]+bleed_mm-right-2)*mm,max(0,top+bleed_mm-2)*mm)
            page=document[index]
            try:
                bitmap=page.render(scale=300/72,crop=crop)
                try:
                    results=zxingcpp.read_barcodes(bitmap.to_pil(),formats=zxingcpp.BarcodeFormat.EAN13)
                    if obj["barcode_value"] not in [result.text for result in results]:
                        raise ExportValidationError("BARCODE_DECODE_FAILED","최종 PDF 바코드 디지털 판독에 실패했습니다.",f"objects.{obj['id']}")
                finally:bitmap.close()
            finally:page.close()
            usage=obj.get("barcode_usage","retail")
            checks.append({"face_id":face["id"],"object_id":obj["id"],"value":obj["barcode_value"],"digital_decode":"passed","tool":"zxing-cpp","raster_dpi":300,
                           "barcode_usage":usage,"physical_print_scan":"not_for_real_world_use" if usage=="sample" else "manufacturer_confirmation_required"})
    return checks


def _preview_all_faces(document,scene,path):
    faces=geometry_for_scene(scene)["faces"];columns=min(3,len(faces));rows=(len(faces)+columns-1)//columns
    preview=Image.new("RGB",(columns*450,rows*650),"#eeeae2");draw=ImageDraw.Draw(preview);font=ImageFont.truetype(str(FONT_PATH),16)
    for index,face in enumerate(faces):
        page=document[index]
        try:
            bitmap=page.render(scale=min(3,420/page.get_width(),600/page.get_height()))
            try:
                rendered=bitmap.to_pil().convert("RGB")
                x=(index%columns)*450+(450-rendered.width)//2;y=(index//columns)*650+15
                preview.paste(rendered,(x,y))
                draw.text(((index%columns)*450+15,(index//columns)*650+620),f"{face['name']} · {face['width_mm']:g} × {face['height_mm']:g} mm",font=font,fill="#273f34")
            finally:bitmap.close()
        finally:page.close()
    preview.save(path)


def export_production_bundle(project:dict,output_dir:Path,approved_conditions:dict,asset_resolver=None,approval_recheck=None) -> dict:
    """Caller must load conditions from registry DB. Optional callback rechecks revocation before publication."""
    preflight=preflight_project(project,approved_conditions,asset_resolver)
    if not preflight["production_allowed"]:
        first=next(issue for issue in preflight["issues"] if issue["severity"]=="error")
        raise ExportValidationError(first["code"],first["message"],first.get("field","production"))
    scene=validate_scene(_scene_from_project(project)); geometry=geometry_for_scene(scene)
    output_dir=Path(output_dir)
    if output_dir.exists():
        raise ExportValidationError("OUTPUT_EXISTS","출력 디렉터리는 작업별 새 경로여야 합니다.")
    output_dir.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix=".production-",dir=output_dir.parent))
    try:
        (staging/"production.pdf").write_bytes(_production_pdf(scene,asset_resolver))
        import pypdfium2 as pdfium
        document=pdfium.PdfDocument(str(staging/"production.pdf"))
        try:
            preflight["barcode_checks"]=_decode_barcodes(document,scene)
            _preview_all_faces(document,scene,staging/"preview.png")
        finally:document.close()
        ticket={"schema_version":"1.0","project_id":str(project.get("id",project.get("project_id",""))),"revision_id":str(project["revision_id"]),
                "template_id":approved_conditions["template"]["id"],"profile_id":approved_conditions["profile"]["id"],"material":approved_conditions["material"],
                "geometry_hash":geometry["geometry_hash"],"faces":[{k:f[k] for k in ("id","name","width_mm","height_mm")} for f in geometry["faces"]],
                "confirmed_fields":scene.get("confirmed_fields",[]),"reviewed_face_ids":approved_conditions["reviewed_face_ids"],
                "approval_evidence":{"template":approved_conditions["template"]["approval"],"profile":approved_conditions["profile"]["approval"]},"capabilities":CAPABILITIES}
        _json(staging/"job-ticket.json",ticket);_ticket_pdf(staging/"job-ticket.pdf",ticket);_json(staging/"preflight.json",preflight)
        manifest={"schema_version":"1.0","kind":"production","adapter":"rgb-face-pages-v1","generated_at":datetime.now(timezone.utc).isoformat(),
                  "project_id":ticket["project_id"],"revision_id":ticket["revision_id"],"geometry_hash":geometry["geometry_hash"],"template_id":ticket["template_id"],"profile_id":ticket["profile_id"],
                  "font":{"id":"NotoSansKR","embedded":True,"sha256":hashlib.sha256(FONT_PATH.read_bytes()).hexdigest()},"capabilities":CAPABILITIES,
                  "font_weights":_font_manifest(scene),
                  "files":[{"name":p.name,"bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(staging.iterdir())],
                  "manifest_hash_policy":"The manifest hashes the five payload files; it does not claim a self hash."}
        _json(staging/"manifest.json",manifest)
        if approval_recheck is not None:
            latest=approval_recheck()
            check=preflight_project(project,latest,asset_resolver)
            if not check["production_allowed"] or latest!=approved_conditions:
                raise ExportValidationError("APPROVAL_CHANGED","출력 중 승인 조건이 변경되었습니다. 최신 조건으로 다시 확인해 주세요.")
        staging.rename(output_dir)
        return manifest
    finally:
        if staging.exists() and staging.resolve().parent==output_dir.parent.resolve() and staging.name.startswith(".production-"):
            shutil.rmtree(staging)
