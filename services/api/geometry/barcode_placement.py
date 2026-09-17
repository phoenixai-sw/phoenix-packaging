"""Bounded placement using the same scene/collision validator as PDF export."""
from copy import deepcopy
from . import GeometryValidationError, geometry_for_scene, validate_scene
from .collisions import bounds, overlap, holes_for_face
from .validation import normalize_mm

MAX_CANDIDATES = 1024


def place_barcode(scene, face_id, barcode, *, x_mm=None, y_mm=None):
    scene = validate_scene(scene)
    geometry = geometry_for_scene(scene)
    face = next((item for item in scene["faces"] if item["id"] == face_id), None)
    if face is None:
        raise GeometryValidationError("INVALID_FACE", "바코드를 놓을 면을 선택해 주세요.", "face_id")
    if len(face["objects"]) >= 200:
        raise GeometryValidationError("INVALID_OBJECTS", "면당 객체 한도 200개에 도달했습니다.", "objects")
    safe = next(item for item in geometry["faces"] if item["id"] == face_id)["regions"]["safe"]
    used = {obj["id"] for item in scene["faces"] for obj in item["objects"]}
    identity = "barcode-placement"
    while identity in used: identity += "-x"
    if len(identity) > 100:
        raise GeometryValidationError("INVALID_OBJECT_ID", "바코드 배치 식별자를 만들 수 없습니다.")
    obj = {"id":identity,"type":"barcode","face_id":face_id,"x_mm":0,"y_mm":0,
           "width_mm":barcode["width_mm"],"height_mm":barcode["height_mm"],"rotation_deg":0,
           "z_index":min(10000,max((item["z_index"] for item in face["objects"]),default=0)+1),
           "barcode_value":barcode["value"],"module_mm":barcode["module_mm"],"bar_height_mm":barcode["bar_height_mm"]}
    if (x_mm is None) != (y_mm is None):
        raise GeometryValidationError("BARCODE_POSITION_REQUIRED", "직접 배치할 때는 X와 Y 좌표를 모두 입력해 주세요.")
    if x_mm is not None:
        obj.update(x_mm=normalize_mm(x_mm,"mm","x_mm"),y_mm=normalize_mm(y_mm,"mm","y_mm"))
        face["objects"].append(obj)
        validate_scene(scene)
        return {"x_mm":obj["x_mm"],"y_mm":obj["y_mm"],"mode":"manual"}
    width,height = barcode["width_mm"],barcode["height_mm"]
    min_x,min_y = safe["x_mm"],safe["y_mm"]
    max_x,max_y = min_x+safe["width_mm"]-width,min_y+safe["height_mm"]-height
    obstacles = [bounds(item) for item in face["objects"] if item["visible"] and item["print_enabled"] and item["type"] in {"text","barcode"}]
    regions = next(item for item in geometry["faces"] if item["id"]==face_id)["regions"]
    for fold in regions.get("fold",[]):
        obstacles.append((min(fold["x1_mm"],fold["x2_mm"])-2,min(fold["y1_mm"],fold["y2_mm"])-2,
                          max(fold["x1_mm"],fold["x2_mm"])+2,max(fold["y1_mm"],fold["y2_mm"])+2))
    for hole in holes_for_face(scene,face_id):
        radius=hole["diameter_mm"]/2+2
        obstacles.append((hole["center_x_mm"]-radius,hole["center_y_mm"]-radius,hole["center_x_mm"]+radius,hole["center_y_mm"]+radius))
    xs={min_x,max_x};ys={min_y,max_y}
    for left,top,right,bottom in obstacles:
        xs.update((right+2,left-width-2));ys.update((bottom+2,top-height-2))
    xs=sorted({round(x,4) for x in xs if min_x<=x<=max_x})
    ys=sorted({round(y,4) for y in ys if min_y<=y<=max_y},reverse=True)
    attempts=0
    for y in ys:
        for x in xs:
            attempts+=1
            if attempts>MAX_CANDIDATES: break
            if any(overlap((x,y,x+width,y+height),other) for other in obstacles): continue
            candidate=deepcopy(scene)
            target=next(item for item in candidate["faces"] if item["id"]==face_id)
            target["objects"].append({**obj,"x_mm":x,"y_mm":y})
            try: validate_scene(candidate)
            except GeometryValidationError: continue
            return {"x_mm":x,"y_mm":y,"mode":"automatic"}
        if attempts>MAX_CANDIDATES: break
    raise GeometryValidationError("BARCODE_NO_SPACE", "현재 면에 바코드와 여백을 놓을 빈 공간이 없습니다. 문구를 이동하거나 더 큰 면을 선택해 주세요.", "face_id")
