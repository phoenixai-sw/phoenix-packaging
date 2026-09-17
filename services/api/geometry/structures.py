"""Versioned demonstration nets. Geometry is not a manufacturer approval."""
from copy import deepcopy
import hashlib
import json
from .validation import GeometryValidationError, normalize_mm, validate_dimensions, _r

TEMPLATES = {"three-side-seal": "three-side-seal-demo-v1", "stand-up-pouch": "stand-up-pouch-demo-v1", "folding-box": "folding-box-demo-v1"}
FACE_NAMES = {"front":"앞면", "back":"뒷면", "left":"왼쪽", "right":"오른쪽", "top":"윗면", "bottom":"바닥"}


def _face(fid, w, h, x, y, position, rotation, margin=5):
    return {"id":fid,"name":FACE_NAMES[fid],"width_mm":w,"height_mm":h,"net":{"x_mm":x,"y_mm":y},
            "assembly":{"position_mm":position,"rotation_deg":rotation,"uv_rotation_deg":0,"mirror_u":False,"mirror_v":False},
            "regions":{"cut":_r(0,0,w,h),"safe":_r(margin,margin,w-2*margin,h-2*margin),"bleed":_r(-3,-3,w+6,h+6),"seal":[],"fold":[],"hole":[],"no_print":[],"hole_allowed":None}}


def build_geometry(template_id, width, height, unit="mm", *, bottom_mm=None, depth_mm=None, holes=None, pouch_features=None):
    if holes is not None and not isinstance(holes,list):
        raise GeometryValidationError("INVALID_HOLES","걸이 구멍을 목록으로 입력해 주세요.","holes")
    if template_id not in TEMPLATES:
        raise GeometryValidationError("UNSUPPORTED_TEMPLATE","지원하지 않는 포장 구조입니다.","template_id")
    basic=validate_dimensions(width,height,unit)
    w,h=basic["width_mm"],basic["height_mm"]
    geometry={**basic,"template_id":template_id,"template_version_id":TEMPLATES[template_id],"structural_parts":[],"fold_lines":[],"assumptions":[]}
    if template_id == "three-side-seal":
        faces=deepcopy(basic["faces"])
        for i,face in enumerate(faces):
            face.update(net={"x_mm":i*(w+15),"y_mm":0},assembly={"position_mm":[0,0,3 if i==0 else -3],"rotation_deg":[0,0 if i==0 else 180,0],"uv_rotation_deg":0,"mirror_u":False,"mirror_v":False})
            face["regions"]["hole_allowed"]=_r(20,16,w-40,12)
        geometry["assumptions"]=["별도 앞·뒷면 패널의 데모입니다. 3D 두께 6mm는 내용물 가정입니다."]
        geometry["net_width_mm"],geometry["net_height_mm"]=2*w+15,h
    elif template_id == "stand-up-pouch":
        if bottom_mm is None:
            raise GeometryValidationError("BOTTOM_REQUIRED","펼친 바닥 폭을 mm로 입력해 주세요.","bottom_mm")
        b=normalize_mm(bottom_mm,"mm","bottom_mm")
        if not 30<=b<=min(w,h/2,180):
            raise GeometryValidationError("INVALID_BOTTOM","데모 펼친 바닥 폭은 30~180mm이며 폭·높이와 조립 가능한 범위여야 합니다.","bottom_mm")
        faces=deepcopy(basic["faces"])
        faces.append(_face("bottom",w,b,0,h,[0,-h/2,0],[90,0,0],margin=10))
        for i,face in enumerate(faces[:2]):
            face.update(net={"x_mm":0,"y_mm":0 if i==0 else h+b,"rotation_deg":0 if i==0 else 180},assembly={"position_mm":[0,0,b/2 if i==0 else -b/2],"rotation_deg":[0,0 if i==0 else 180,0],"uv_rotation_deg":0,"mirror_u":False,"mirror_v":False})
            face["regions"]["hole_allowed"]=_r(20,16,w-40,12)
        faces[2]["regions"]["fold"]=[{"x1_mm":0,"y1_mm":b/2,"x2_mm":w,"y2_mm":b/2}]
        geometry.update(bottom_mm=b,bottom_half_mm=b/2,net_width_mm=w,net_height_mm=2*h+b)
        geometry["fold_lines"]=[{"x1_mm":0,"y1_mm":y,"x2_mm":w,"y2_mm":y} for y in (h,h+b/2,h+b)]
        geometry["assumptions"]=["bottom_mm는 펼친 거싯 폭입니다. 접힌 반폭은 bottom_mm/2입니다.","이 데모는 앞·바닥·뒤 패널 연결 예시이며 제조사 공통 전개 공식이 아닙니다.","3D는 평면 패널 조립 가정으로 충전 팽창·주름을 검증하지 않습니다."]
    else:
        if depth_mm is None:
            raise GeometryValidationError("DEPTH_REQUIRED","박스 깊이를 mm로 입력해 주세요.","depth_mm")
        d=normalize_mm(depth_mm,"mm","depth_mm")
        if not 30<=d<=min(w,300):
            raise GeometryValidationError("INVALID_DEPTH","데모 박스 깊이는 30~300mm이며 앞면 폭 이하여야 합니다.","depth_mm")
        glue=15.0
        faces=[_face("front",w,h,glue,d,[0,0,d/2],[0,0,0]),_face("right",d,h,glue+w,d,[w/2,0,0],[0,90,0]),
               _face("back",w,h,glue+w+d,d,[0,0,-d/2],[0,180,0]),_face("left",d,h,glue+2*w+d,d,[-w/2,0,0],[0,-90,0]),
               _face("top",w,d,glue,0,[0,h/2,0],[-90,0,0]),_face("bottom",w,d,glue,d+h,[0,-h/2,0],[90,0,0])]
        parts=[{"id":"glue-tab","kind":"glue_tab","print_enabled":False,**_r(0,d,glue,h)}]
        for face_id,x,pw in (("right",glue+w,d),("back",glue+w+d,w),("left",glue+2*w+d,d)):
            flap=min(d*.45,pw*.45)
            for end,y in (("top",d-flap),("bottom",d+h)):
                parts.append({"id":f"{face_id}-{end}-flap","kind":"flap","print_enabled":False,**_r(x,y,pw,flap)})
        geometry.update(depth_mm=d,structural_parts=parts,net_width_mm=glue+2*w+2*d,net_height_mm=h+2*d)
        geometry["fold_lines"]=[{"x1_mm":x,"y1_mm":d,"x2_mm":x,"y2_mm":d+h} for x in (glue,glue+w,glue+w+d,glue+2*w+d)]
        geometry["fold_lines"] += [{"x1_mm":glue,"y1_mm":y,"x2_mm":glue+2*w+2*d,"y2_mm":y} for y in (d,d+h)]
        geometry["assumptions"]=["자체 제작한 단순 접이식 상자 데모 전개도입니다. 6면 외 접착부·덮개를 포함합니다.","치수는 데모 외경 기준입니다. 종이 두께·맞물림·가공 여유는 제조사 승인 전 미확정입니다."]
    geometry["faces"]=faces
    from .pouch_features import normalize_pouch_features, apply_pouch_features, physical_pouch_features
    features = normalize_pouch_features(pouch_features, template_id, w, h)
    apply_pouch_features(geometry, features)
    geometry["holes"]=deepcopy(holes or [])
    if isinstance(holes,list):
        for face in faces:
            for hole in holes:
                if not isinstance(hole,dict) or hole.get("face_id") not in {"front","back"} or face["id"] not in {"front","back"}:continue
                x=normalize_mm(hole.get("center_x_mm"),"mm","hole.center_x_mm")
                y=normalize_mm(hole.get("center_y_mm"),"mm","hole.center_y_mm")
                radius=normalize_mm(hole.get("diameter_mm"),"mm","hole.diameter_mm")/2
                if hole["face_id"]!=face["id"]:x=w-x
                face["regions"]["hole"].append({"id":hole.get("id"),"kind":"circle","center_x_mm":x,"center_y_mm":y,"radius_mm":radius,"closed":True})
                face["regions"]["no_print"].append({"kind":"hole_guard","hole_id":hole.get("id"),**_r(x-radius-2,y-radius-2,2*radius+4,2*radius+4)})
    if holes:
        from .collisions import collision_report
        issues=collision_report({"holes":holes,"faces":[{**face,"objects":[]} for face in faces]},geometry)
        if issues:
            issue=issues[0]
            raise GeometryValidationError(issue["code"],issue["message"],"holes")
        geometry["holes"]=[{**hole,**{key:normalize_mm(hole[key],"mm") for key in ("center_x_mm","center_y_mm","diameter_mm")}} for hole in holes]
    normalized={"template_version_id":geometry["template_version_id"],"width_mm":w,"height_mm":h,"bottom_mm":geometry.get("bottom_mm"),"depth_mm":geometry.get("depth_mm"),"holes":geometry["holes"]}
    if features is not None:
        normalized["pouch_features"] = physical_pouch_features(features)
    if template_id != "three-side-seal" or holes or features is not None:
        geometry["geometry_hash"]=hashlib.sha256(json.dumps(normalized,sort_keys=True,separators=(",", ":")).encode()).hexdigest()
    return geometry


def geometry_for_scene(scene):
    version=scene.get("template_version_id") or TEMPLATES["three-side-seal"]
    kind=scene.get("template_kind") or next((k for k,v in TEMPLATES.items() if v==version),None)
    if kind not in TEMPLATES:
        raise GeometryValidationError("UNSUPPORTED_TEMPLATE","지원하지 않는 템플릿 버전입니다.","template_version_id")
    if version in TEMPLATES.values() and version!=TEMPLATES[kind]:
        raise GeometryValidationError("TEMPLATE_KIND_MISMATCH","템플릿 버전과 구조 종류가 일치하지 않습니다.","template_version_id")
    front=next((f for f in scene.get("faces",[]) if isinstance(f,dict) and f.get("id")=="front"),{})
    geometry=build_geometry(kind,front.get("width_mm"),front.get("height_mm"),"mm",bottom_mm=scene.get("bottom_mm"),depth_mm=scene.get("depth_mm"),holes=scene.get("holes"),pouch_features=scene.get("pouch_features"))
    geometry["geometry_template_id"]=TEMPLATES[kind]
    if version!=TEMPLATES[kind]:
        geometry["geometry_hash"]=hashlib.sha256(json.dumps({"template_version_id":version,"engine_geometry_hash":geometry["geometry_hash"]},sort_keys=True,separators=(",",":")).encode()).hexdigest()
        geometry["template_version_id"]=version
    return geometry


def new_scene(template_id,width_mm,height_mm,*,bottom_mm=None,depth_mm=None):
    geometry=build_geometry(template_id,width_mm,height_mm,bottom_mm=bottom_mm,depth_mm=depth_mm)
    return {"schema_version":"1.0","template_kind":template_id,"template_version_id":geometry["template_version_id"],"geometry_hash":geometry["geometry_hash"],
            "active_face_id":"front", "bottom_mm":geometry.get("bottom_mm"),"depth_mm":geometry.get("depth_mm"),"holes":[],"confirmed_fields":[],"reviewed_face_ids":[],
            "faces":[{k:face[k] for k in ("id","name","width_mm","height_mm")}|{"background":"#f4f1e8","objects":[]} for face in geometry["faces"]]}
