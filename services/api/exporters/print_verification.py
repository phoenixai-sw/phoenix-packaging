"""Final-byte checks for our bounded ordinary-PDF adapter, not PDF/X certification."""
from hashlib import sha256
from io import BytesIO
from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.lib.units import mm
from ..geometry.collisions import bounds
from .review_pdf import ExportValidationError


def verify_print_artifacts(payloads,scene,geometry,paths,profile,test_mode):
    checks=[];b=profile['bleed_mm'];footer=12 if test_mode else 0
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
    return {'page_checks':checks,'barcodes':barcodes,'pdf_x':'not_claimed','manufacturer_approval':False}
