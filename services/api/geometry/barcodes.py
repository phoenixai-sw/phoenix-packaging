"""EAN-13 validation and ReportLab's version-pinned, digitally tested encoder."""
import re
from reportlab.graphics.barcode.eanbc import Ean13BarcodeWidget
from reportlab.graphics.shapes import Rect
from .validation import GeometryValidationError, normalize_mm


def validate_ean13(value):
    if not isinstance(value,str) or not re.fullmatch(r"[0-9]{13}",value):
        raise GeometryValidationError("EAN13_FORMAT","EAN-13은 선행 0을 포함한 13자리 숫자 문자열이어야 합니다.","barcode_value")
    check=(10-(sum(int(n) for n in value[:12:2])+3*sum(int(n) for n in value[1:12:2]))%10)%10
    if int(value[-1])!=check:
        raise GeometryValidationError("EAN13_CHECK_DIGIT",f"체크 숫자가 일치하지 않습니다. 입력 번호를 확인해 주세요. 예상 마지막 숫자: {check}","barcode_value")
    return value


def sample_ean13():
    # GS1 example prefix: a documentation/demo symbol, never retail ownership.
    stem="952000000001"
    check=(10-(sum(int(n) for n in stem[::2])+3*sum(int(n) for n in stem[1::2]))%10)%10
    return stem+str(check)


def barcode_geometry(value,module_mm=.33,bar_height_mm=22.85,*,barcode_usage="retail"):
    if not isinstance(barcode_usage,str) or barcode_usage not in {"retail","sample"}:
        raise GeometryValidationError("BARCODE_USAGE_INVALID", "바코드 용도를 정식 상품 번호 또는 검토용 샘플로 선택해 주세요.", "barcode_usage")
    value=validate_ean13(value)
    if barcode_usage=="retail" and value.startswith("952"):
        raise GeometryValidationError("BARCODE_SAMPLE_REQUIRED", "952 예시 번호는 검토용 샘플로만 사용할 수 있습니다.", "barcode_usage")
    x=normalize_mm(module_mm,"mm","module_mm")
    h=normalize_mm(bar_height_mm,"mm","bar_height_mm")
    if not .264<=x<=.66:
        raise GeometryValidationError("BARCODE_MODULE_SIZE","EAN-13 모듈 폭은 0.264~0.660mm로 설정해 주세요.","module_mm")
    if h+1e-9<22.85*x/.33 or h>45.7:
        raise GeometryValidationError("BARCODE_HEIGHT","소비자용 EAN-13 바 높이는 배율에 필요한 최소 높이 이상, 최대 45.70mm여야 합니다.","bar_height_mm")
    # Explicit quiet zones; ReportLab's default symmetric 9X is not used.
    # The widget validates lquiet/rquiet as booleans in this pinned version.
    # Generate the exact 95-module symbol without margins, then add explicit 11X/7X zones.
    widget=Ean13BarcodeWidget(value[:12],barWidth=x,barHeight=h,quiet=False,humanReadable=False)
    bars=[{"x_mm":round(item.x+11*x,6),"width_mm":round(item.width,6)} for item in widget.draw().contents if isinstance(item,Rect) and item.fillColor is not None]
    return {"value":value,"symbology":"EAN13","module_mm":x,"bar_height_mm":h,"width_mm":round(113*x,4),"height_mm":round(h+(9 if barcode_usage=="sample" else 5),4),
            "quiet_left_mm":round(11*x,4),"quiet_right_mm":round(7*x,4),"bars":bars,"foreground":"#000000","background":"#ffffff",
            "barcode_usage":barcode_usage,"barcode_owned":False,"sample_label":"SAMPLE / 검토용" if barcode_usage=="sample" else None,
            "notice":"체크 숫자 확인은 GS1 정식 발급·번호 소유·실물 인쇄 판독 확인을 대신하지 않습니다."}


def validate_barcode_object(obj):
    result=barcode_geometry(obj.get("barcode_value"),obj.get("module_mm") or .33,obj.get("bar_height_mm") or 22.85,barcode_usage=obj.get("barcode_usage","retail"))
    if abs(obj["width_mm"]-result["width_mm"])>.01 or abs(obj["height_mm"]-result["height_mm"])>.01:
        raise GeometryValidationError("BARCODE_FREE_SCALE","바코드는 가로·세로를 임의 변경할 수 없습니다. 모듈 폭과 바 높이로 다시 생성해 주세요.",f"objects.{obj['id']}")
    if obj["rotation_deg"]%90!=0 or obj["opacity"]!=1:
        raise GeometryValidationError("BARCODE_TRANSFORM","바코드는 불투명 상태와 90도 단위 회전만 지원합니다.",f"objects.{obj['id']}")
    obj.update(module_mm=result["module_mm"],bar_height_mm=result["bar_height_mm"],barcode_value=result["value"])
    if result["barcode_usage"]=="sample":
        obj["barcode_owned"]=False
        obj["barcode_usage"]="sample"
    return result
