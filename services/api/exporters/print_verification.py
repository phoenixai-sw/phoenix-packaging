"""Final-byte checks for our bounded ordinary-PDF adapter, not PDF/X certification."""
from hashlib import sha256
from io import BytesIO
from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.lib.units import mm
from ..geometry.collisions import bounds
from .review_pdf import ExportValidationError


def _stroked_segments(operations):
    """Read actual stroked PDF paths after graphics transforms, excluding filled glyphs."""
    matrix=(1,0,0,1,0,0);stack=[];pending={'lines':[],'curves':[]};result={'lines':[],'curves':[]};current=start=None
    def point(x,y):
        a,b,c,d,e,f=matrix;return (a*float(x)+c*float(y)+e,b*float(x)+d*float(y)+f)
    for values,op in operations:
        if op==b'q':stack.append(matrix)
        elif op==b'Q':matrix=stack.pop()
        elif op==b'cm':
            a,b,c,d,e,f=matrix;u,v,w,x,y,z=map(float,values)
            matrix=(a*u+c*v,b*u+d*v,a*w+c*x,b*w+d*x,a*y+c*z+e,b*y+d*z+f)
        elif op==b'm':current=start=point(*values)
        elif op==b'l':
            end=point(*values);pending['lines'].append([*current,*end]);current=end
        elif op==b'c':
            a=point(*values[:2]);b=point(*values[2:4]);end=point(*values[4:6]);pending['curves'].append([*current,*a,*b,*end]);current=end
        elif op==b're':
            x,y,w,h=map(float,values);p=[point(x,y),point(x+w,y),point(x+w,y+h),point(x,y+h)]
            pending['lines'].extend([*a,*b] for a,b in zip(p,p[1:]+p[:1]));current=start=p[0]
        elif op in (b'h',b's',b'b',b'b*') and current!=start:
            pending['lines'].append([*current,*start]);current=start
        if op in (b'S',b's',b'B',b'B*',b'b',b'b*'):
            for key in result:result[key].extend(pending[key])
        if op in (b'n',b'S',b's',b'f',b'f*',b'F',b'B',b'B*',b'b',b'b*'):
            pending={'lines':[],'curves':[]};current=start=None
    return result


def _verify_segments(operations,spec,role,bleed,footer):
    from ..geometry.finishing_paths import expected_segments
    actual=_stroked_segments(operations);expected=expected_segments(spec,role)
    for key,segments in expected.items():
        if len(actual[key])!=len(segments):raise ExportValidationError('PRINT_FINISHING_PATH_MISMATCH','최종 CUT/FOLD/가공 PDF의 경로 개수가 동결 기하와 다릅니다.')
        remaining=list(actual[key]);seen=set()
        for segment in segments:
            normalized=[coordinate for x,y in zip(segment[::2],segment[1::2]) for coordinate in ((x+bleed)*mm,(spec['height_mm']-y+bleed+footer)*mm)]
            pairs=list(zip(normalized[::2],normalized[1::2]));reverse=[v for pair in reversed(pairs) for v in pair]
            signature=min(tuple(round(v,5) for v in normalized),tuple(round(v,5) for v in reverse))
            if role=='cut' and signature in seen:raise ExportValidationError('PRINT_DUPLICATE_CUT','동일한 CUT 경로가 중복되었습니다.')
            seen.add(signature)
            found=next((i for i,path in enumerate(remaining) if min(max(abs(a-b) for a,b in zip(path,normalized)),max(abs(a-b) for a,b in zip(path,reverse)))<=.01*mm),None)
            if found is None:raise ExportValidationError('PRINT_FINISHING_PATH_MISMATCH','최종 벡터 경로가 승인 기하의 0.01mm 허용오차를 벗어났습니다.')
            remaining.pop(found)
    return {'role':role,'face_id':spec['face_id'],'line_count':len(actual['lines']),'cubic_count':len(actual['curves']),'tolerance_mm':.01,'passed':True}


def verify_print_artifacts(payloads,scene,geometry,paths,profile,test_mode):
    checks=[];b=profile['bleed_mm'];footer=12 if test_mode else 0
    finishing=bool(geometry.get('holes') or geometry.get('pouch_features') is not None);finishing_checks=[]
    if finishing and set(payloads)!={'artwork','cut','fold','process'}:raise ExportValidationError('PRINT_FINISHING_FILE_MISSING','가공 출력에는 아트·CUT·FOLD·PROCESS 네 PDF가 필요합니다.')
    for role,data in payloads.items():
        pdf=PdfReader(BytesIO(data))
        if len(pdf.pages)!=len(paths):raise ExportValidationError('PRINT_PAGE_COUNT','출력 페이지 수가 구조와 다릅니다.')
        if '/GTS_PDFXVersion' in pdf.metadata:raise ExportValidationError('FALSE_PDFX_CLAIM','검증하지 않은 PDF/X 선언을 허용하지 않습니다.')
        intent=pdf.trailer['/Root']['/OutputIntents'][0].get_object()['/DestOutputProfile'].get_object()
        if sha256(intent.get_data()).hexdigest()!=profile['icc_sha256']:raise ExportValidationError('ICC_HASH_MISMATCH','최종 PDF의 ICC 해시가 다릅니다.')
        for i,(page,spec) in enumerate(zip(pdf.pages,paths)):
            w,h=spec['width_mm'],spec['height_mm']
            expected={'MediaBox':[0,0,w+2*b,h+2*b+footer],'TrimBox':[b,b+footer,w+b,h+b+footer],'BleedBox':[0,footer,w+2*b,h+2*b+footer]}
            for key,values in expected.items():
                actual=[float(v)/mm for v in page['/'+key]]
                if any(abs(a-e)>.01 for a,e in zip(actual,values)):raise ExportValidationError('PRINT_BOX_MISMATCH','최종 PDF 치수가 원본 구조와 다릅니다.')
            operations=ContentStream(page.get_contents(),pdf).operations
            if finishing and role in ('cut','fold','process'):finishing_checks.append(_verify_segments(operations,spec,role,b,footer))
            if any(op in (b'rg',b'RG',b'Tj',b'TJ',b"'",b'"') for _,op in operations):
                raise ExportValidationError('PRINT_COLOR_OR_FONT_MISMATCH','RGB 또는 윤곽선으로 처리하지 않은 글자가 남아 있습니다.')
            resources=page['/Resources']
            for ref in resources.get('/XObject',{}).values():
                image=ref.get_object()
                if image.get('/Subtype')!='/Image':raise ExportValidationError('PRINT_UNVERIFIED_XOBJECT','검증되지 않은 PDF 객체입니다.')
                color=image.get('/ColorSpace')
                if not isinstance(color,list) or color[0]!='/ICCBased' or color[1].get_object().get('/N')!=4:
                    raise ExportValidationError('PRINT_NON_CMYK_IMAGE','최종 PDF 이미지가 ICC CMYK가 아닙니다.')
                if '/SMask' in image:raise ExportValidationError('PRINT_TRANSPARENCY_UNSUPPORTED','이미지 투명도가 남아 있습니다.')
            for value in resources.get('/ExtGState',{}).values():
                state=value.get_object()
                if any(float(state.get(k,1))!=1 for k in ('/ca','/CA')) or state.get('/OP') or state.get('/op'):
                    raise ExportValidationError('PRINT_UNSUPPORTED_GRAPHICS_STATE','반투명·오버프린트 상태가 남아 있습니다.')
            checks.append({'role':role,'page':i+1,'boxes_mm':expected,'tolerance_mm':.01,'icc_cmyk':True,'text_outlined':True})
    import pypdfium2 as pdfium
    import zxingcpp
    barcodes=[];doc=pdfium.PdfDocument(payloads['artwork']);structural={f['id']:f for f in geometry['faces']}
    try:
        for fi,face in enumerate(scene['faces']):
            for obj in face['objects']:
                if obj['type']!='barcode' or not obj['visible'] or not obj['print_enabled']:continue
                index=next(i for i,p in enumerate(paths) if p['face_id']==face['id']) if profile['layout']=='face_pages' else 0
                x1,y1,x2,y2=bounds(obj)
                if profile['layout']=='net':
                    net=structural[face['id']]['net']
                    if net['rotation_deg']==180:x1,x2=face['width_mm']-x2,face['width_mm']-x1;y1,y2=face['height_mm']-y2,face['height_mm']-y1
                    x1+=net['x_mm'];x2+=net['x_mm'];y1+=net['y_mm'];y2+=net['y_mm']
                page=doc[index];pw,ph=page.get_width(),page.get_height()
                crop=(max(0,(x1+b-2)*mm),max(0,ph-(y2+b+2)*mm),max(0,pw-(x2+b+2)*mm),max(0,(y1+b-2)*mm))
                try:
                    bitmap=page.render(scale=300/72,crop=crop)
                    try:decoded=[r.text for r in zxingcpp.read_barcodes(bitmap.to_pil(),formats=zxingcpp.BarcodeFormat.EAN13)]
                    finally:bitmap.close()
                finally:page.close()
                if obj['barcode_value'] not in decoded:raise ExportValidationError('BARCODE_DECODE_FAILED','최종 CMYK PDF의 바코드 판독에 실패했습니다.')
                barcodes.append({'face_id':face['id'],'object_id':obj['id'],'value':obj['barcode_value'],'digital_decode':'passed','raster_dpi':300,'physical_scan':'not_tested'})
    finally:doc.close()
    result={'page_checks':checks,'barcodes':barcodes,'pdf_x':'not_claimed','manufacturer_approval':False}
    if finishing:result['finishing']={'path_checks':finishing_checks,'cut_duplicate_check':'passed','physical_tooling_tested':False,'cubic_tolerance_mm':.01}
    return result
