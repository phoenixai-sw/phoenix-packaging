"""Conservative structural collision checks with actionable face/object IDs."""
import math
from .validation import GeometryValidationError, _corners, normalize_mm


def holes_for_face(scene,face_id):
    front=next(f for f in scene["faces"] if f["id"]=="front")
    result=[]
    for hole in scene.get("holes",[]):
        if not isinstance(hole,dict): continue
        if hole.get("face_id")==face_id:
            result.append(hole)
        elif {hole.get("face_id"),face_id}=={"front","back"}:
            try: center_x=front["width_mm"]-normalize_mm(hole.get("center_x_mm"),"mm")
            except GeometryValidationError: center_x=None
            result.append({**hole,"face_id":face_id,"center_x_mm":center_x})
    return result


def bounds(obj):
    points=_corners({"rotation_deg":0,**obj})
    return min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)


def overlap(a,b):
    return a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3]


def collision_report(scene,geometry):
    issues=[]
    def issue(code,message,face_id=None,object_id=None,hole_id=None):
        issues.append({"code":code,"message":message,"face_id":face_id,"object_id":object_id,"hole_id":hole_id})
    ids=set()
    holes=scene.get("holes",[])
    if not isinstance(holes,list) or len(holes)>8:
        return [{"code":"INVALID_HOLES","message":"걸이 구멍은 최대 8개 목록으로 입력해 주세요."}]
    for hole in holes:
        if not isinstance(hole,dict):
            issue("INVALID_HOLE","걸이 구멍 형식을 확인해 주세요.");continue
        hid=hole.get("id")
        if not hid or hid in ids:
            issue("INVALID_HOLE_ID","걸이 구멍 식별자는 고유해야 합니다.",hole_id=hid)
        ids.add(hid)
        if hole.get("face_id") not in {"front","back"} or geometry["template_id"]=="folding-box":
            issue("HOLE_NOT_ALLOWED","이 데모 면에는 걸이 구멍을 지원하지 않습니다.",hole.get("face_id"),hole_id=hid)
    for face in scene["faces"]:
        fid=face["id"]
        regions=next(f for f in geometry["faces"] if f["id"]==fid)["regions"]
        visible=sorted([o for o in face["objects"] if o.get("visible",True) and o.get("print_enabled",True)],key=lambda o:o.get("z_index",0))
        draw_order={o["id"]:index for index,o in enumerate(visible)}
        face_holes=holes_for_face(scene,fid)
        for hole_index,hole in enumerate(face_holes):
            try:
                x=normalize_mm(hole.get("center_x_mm"),"mm")
                y=normalize_mm(hole.get("center_y_mm"),"mm")
                diameter=normalize_mm(hole.get("diameter_mm"),"mm")
            except GeometryValidationError:
                issue("INVALID_HOLE","구멍 중심·지름은 유한한 mm 값이어야 합니다.",fid,hole_id=hole.get("id")); continue
            radius=diameter/2
            for other in face_holes[hole_index+1:]:
                try:
                    other_x=normalize_mm(other.get("center_x_mm"),"mm");other_y=normalize_mm(other.get("center_y_mm"),"mm");other_r=normalize_mm(other.get("diameter_mm"),"mm")/2
                    if math.hypot(x-other_x,y-other_y)<radius+other_r+4:
                        issue("HOLE_SPACING","구멍의 2mm 보호 여백이 서로 겹칩니다.",fid,hole_id=hole.get("id"))
                except GeometryValidationError:pass
            allowed=regions.get("hole_allowed")
            if not 4<=diameter<=10 or not allowed or not (allowed["x_mm"]+radius<=x<=allowed["x_mm"]+allowed["width_mm"]-radius and allowed["y_mm"]+radius<=y<=allowed["y_mm"]+allowed["height_mm"]-radius):
                issue("HOLE_OUTSIDE_ALLOWED","구멍이 데모 허용 영역 밖이거나 지름 4~10mm를 벗어났습니다.",fid,hole_id=hole.get("id"))
            for obj in visible:
                if obj["type"] not in {"text","barcode"}: continue
                left,top,right,bottom=bounds(obj)
                if math.hypot(x-max(left,min(x,right)),y-max(top,min(y,bottom)))<radius+2:
                    issue("HOLE_OBJECT_COLLISION","구멍과 2mm 보호 여백이 중요 문구 또는 바코드와 겹칩니다.",fid,obj["id"],hole.get("id"))
            for region in regions["no_print"]:
                if region.get("kind")=="hole_guard": continue
                left,top=region["x_mm"],region["y_mm"]
                if math.hypot(x-max(left,min(x,left+region["width_mm"])),y-max(top,min(y,top+region["height_mm"])))<radius+2:
                    issue("HOLE_SEAL_COLLISION","구멍 보호 여백이 실링·인쇄 금지영역과 겹칩니다.",fid,hole_id=hole.get("id"))
        for barcode in [o for o in visible if o["type"]=="barcode"]:
            for fold in regions.get("fold",[]):
                fold_bounds=(min(fold["x1_mm"],fold["x2_mm"])-2,min(fold["y1_mm"],fold["y2_mm"])-2,max(fold["x1_mm"],fold["x2_mm"])+2,max(fold["y1_mm"],fold["y2_mm"])+2)
                if overlap(bounds(barcode),fold_bounds):
                    issue("BARCODE_FOLD_COLLISION","바코드와 여백이 접힘선의 2mm 보호 영역을 침범합니다.",fid,barcode["id"])
            for other in visible:
                if other["id"]==barcode["id"]: continue
                if other["type"] in {"text","barcode"} or draw_order[other["id"]]>draw_order[barcode["id"]]:
                    if overlap(bounds(barcode),bounds(other)):
                        issue("BARCODE_QUIET_ZONE_COLLISION","다른 객체가 바코드 또는 여백을 침범합니다.",fid,other["id"])
    return issues
