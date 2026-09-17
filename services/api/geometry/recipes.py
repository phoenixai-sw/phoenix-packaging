"""Pure bounded recipes emitting the SAME geometry used by existing renderers."""
from copy import deepcopy
from .definitions import parse_definition, Dimensions
from .validation import GeometryValidationError, _r
from .structures import FACE_NAMES


def _reject(code, message):
    raise GeometryValidationError(code, message, "structure_definition")


def _rect_inside(rect, w, h):
    return rect["x_mm"] >= 0 and rect["y_mm"] >= 0 and rect["x_mm"]+rect["width_mm"] <= w+1e-7 and rect["y_mm"]+rect["height_mm"] <= h+1e-7


def _overlap(a, b):
    return (a["x_mm"] < b["x_mm"]+b["width_mm"]-1e-7 and b["x_mm"] < a["x_mm"]+a["width_mm"]-1e-7 and
            a["y_mm"] < b["y_mm"]+b["height_mm"]-1e-7 and b["y_mm"] < a["y_mm"]+a["height_mm"]-1e-7)


def _regions(w, h, margins, no_print=None, folds=None):
    l,r,t,b=(margins[k] for k in ("left", "right", "top", "bottom"))
    if w-l-r <= 0 or h-t-b <= 0:
        _reject("EMPTY_SAFE_AREA", "등록 구조의 안전영역이 비어 있습니다.")
    safe=_r(l,t,round(w-l-r,4),round(h-t-b,4))
    if any(not _rect_inside(rect,w,h) or _overlap(rect,safe) for rect in no_print or []):
        _reject("STRUCTURE_SAFE_COLLISION", "인쇄 금지영역은 면 안에 있어야 하며 안전영역과 겹칠 수 없습니다.")
    for line in folds or []:
        if not all(0 <= line[k] <= (w if k.startswith("x") else h) for k in line):
            _reject("STRUCTURE_FOLD_OUTSIDE", "면 접힘선이 해당 면 밖에 있습니다.")
    return {"cut":_r(0,0,w,h),"safe":safe,"bleed":_r(-3,-3,w+6,h+6),"seal":[],"fold":deepcopy(folds or []),
            "hole":[],"no_print":deepcopy(no_print or []),"hole_allowed":None}


def normalized_inputs(inputs):
    from pydantic import ValidationError
    try:
        return Dimensions.model_validate(inputs).model_dump(mode="json",exclude_none=True)
    except (ValidationError, GeometryValidationError) as exc:
        raise GeometryValidationError("INVALID_STRUCTURE_INPUTS", "등록 구조의 완성 치수를 확인해 주세요.", "structure_inputs") from exc


def resolve_recipe(definition, inputs):
    definition=parse_definition(definition)
    inputs=normalized_inputs(inputs)
    if definition["recipe_id"] == "fixed-panel-net-v1":
        if inputs != definition["dimensions"]:
            _reject("FIXED_STRUCTURE_DIMENSIONS", "고정 도면은 등록한 치수 그대로만 사용할 수 있습니다.")
        faces=[]
        for panel in definition["panels"]:
            w,h=panel["width_mm"],panel["height_mm"]
            faces.append({"id":panel["id"],"name":FACE_NAMES[panel["id"]],"width_mm":w,"height_mm":h,
                          "net":deepcopy(panel["net"]),"assembly":deepcopy(panel["assembly"]),
                          "regions":_regions(w,h,panel["safe_inset_mm"],panel["no_print"],panel["fold"])})
        lookup={face["id"]:face for face in faces}
        if definition["family"]!="folding-box":
            expected={"front":(inputs["width_mm"],inputs["height_mm"]),"back":(inputs["width_mm"],inputs["height_mm"])}
            if definition["family"]=="stand-up-pouch":expected["bottom"]=(inputs["width_mm"],inputs["bottom_mm"])
            if any((lookup[fid]["width_mm"],lookup[fid]["height_mm"])!=size for fid,size in expected.items()):
                _reject("STRUCTURE_DIMENSION_SEMANTICS","파우치 면 치수는 완성 외곽·펼친 바닥 치수와 일치해야 합니다.")
        else:
            w,h,d=lookup["front"]["width_mm"],lookup["front"]["height_mm"],lookup["right"]["width_mm"]
            sizes={"front":(w,h),"back":(w,h),"left":(d,h),"right":(d,h),"top":(w,d),"bottom":(w,d)}
            if any((lookup[fid]["width_mm"],lookup[fid]["height_mm"])!=size for fid,size in sizes.items()):
                _reject("STRUCTURE_BOX_FACE_MISMATCH","첫 고정 상자 레시피는 서로 연결되는 직육면체 6면 치수가 일치해야 합니다.")
            # No arbitrary assembly renderer: support one explicit orthogonal outside-view convention.
            transforms={"front":([0,0,d/2],[0,0,0]),"back":([0,0,-d/2],[0,180,0]),
                        "left":([-w/2,0,0],[0,-90,0]),"right":([w/2,0,0],[0,90,0]),
                        "top":([0,h/2,0],[-90,0,0]),"bottom":([0,-h/2,0],[90,0,0])}
            for fid,(position,rotation) in transforms.items():
                assembly=lookup[fid]["assembly"]
                if assembly["position_mm"]!=position or assembly["rotation_deg"]!=rotation:
                    _reject("STRUCTURE_ASSEMBLY_MISMATCH","등록 상자 3D 면 위치·접힘 방향이 지원하는 6면 조립 규칙과 다릅니다.")
        parts=[{**deepcopy(p),"print_enabled":False} for p in definition["structural_parts"]]
        fold_lines=deepcopy(definition["fold_lines"])
        boxes=[{**f["net"],"width_mm":f["width_mm"],"height_mm":f["height_mm"],"id":f["id"]} for f in faces]+parts
        for i,a in enumerate(boxes):
            for b in boxes[i+1:]:
                if _overlap(a,b):
                    _reject("STRUCTURE_NET_OVERLAP", "전개도의 면·접착부·덮개가 서로 겹칩니다.")
        for part in parts:
            panel=next(f for f in boxes if f["id"]==part["attached_face_id"])
            horizontal=(abs(part["x_mm"]+part["width_mm"]-panel["x_mm"])<1e-7 or abs(panel["x_mm"]+panel["width_mm"]-part["x_mm"])<1e-7) and max(part["y_mm"],panel["y_mm"]) < min(part["y_mm"]+part["height_mm"],panel["y_mm"]+panel["height_mm"])
            vertical=(abs(part["y_mm"]+part["height_mm"]-panel["y_mm"])<1e-7 or abs(panel["y_mm"]+panel["height_mm"]-part["y_mm"])<1e-7) and max(part["x_mm"],panel["x_mm"]) < min(part["x_mm"]+part["width_mm"],panel["x_mm"]+panel["width_mm"])
            if not (horizontal or vertical):
                _reject("STRUCTURE_PART_DETACHED", "구조 부품은 지정한 면의 가장자리에 연결되어야 합니다.")
        net_w=max(p["x_mm"]+p["width_mm"] for p in boxes)
        net_h=max(p["y_mm"]+p["height_mm"] for p in boxes)
        for line in fold_lines:
            if not all(0 <= line[k] <= (net_w if k.startswith("x") else net_h) for k in line):
                _reject("STRUCTURE_FOLD_OUTSIDE", "접힘선이 등록 전개도 밖에 있습니다.")
        assumptions=["등록된 고정 패널 치수·방향을 사용합니다. 치수 변경이나 재질 두께 자동 보정은 하지 않습니다.",
                     "직사각 패널·부품과 등록 조립 위치의 검토이며 실제 접힘·맞물림·제조 적합성 승인이 아닙니다."]
    else:
        if set(inputs)!={"width_mm","height_mm"}:
            _reject("STRUCTURE_INPUTS_MISMATCH", "분리 삼방 패널에는 폭·높이만 입력해 주세요.")
        w,h=inputs["width_mm"],inputs["height_mm"]
        for key,value in (("width",w),("height",h)):
            bounds=definition[key+"_range_mm"]
            if not bounds["minimum"] <= value <= bounds["maximum"]:
                _reject("STRUCTURE_INPUT_OUT_OF_RANGE", "치수가 제조 도면에 등록된 허용 범위를 벗어났습니다.")
        seals=definition["seals_mm"]; margin=definition["safe_margin_mm"]
        l,r,t,b=(seals[k] for k in ("left","right","top","bottom"))
        seal_regions=[_r(0,0,l,h),_r(w-r,0,r,h),_r(l,h-b,w-l-r,b),_r(l,0,w-l-r,t)]
        seal_regions=[rect for rect in seal_regions if rect["width_mm"] and rect["height_mm"]]
        regions=_regions(w,h,{key:round(value+margin,4) for key,value in seals.items()},seal_regions)
        regions["seal"]=deepcopy(seal_regions);regions["top_closure"]=_r(l,0,w-l-r,t)
        gap=definition["panel_gap_mm"];depth=definition["preview_depth_mm"]
        faces=[{"id":fid,"name":FACE_NAMES[fid],"width_mm":w,"height_mm":h,"net":{"x_mm":i*(w+gap),"y_mm":0,"rotation_deg":0},
                "assembly":{"position_mm":[0,0,depth/2 if i==0 else -depth/2],"rotation_deg":[0,0 if i==0 else 180,0],"uv_rotation_deg":0,"mirror_u":False,"mirror_v":False},"regions":deepcopy(regions)}
               for i,fid in enumerate(("front","back"))]
        parts=[];fold_lines=[];net_w=2*w+gap;net_h=h
        assumptions=["완성 외곽 치수 안에 등록된 네 변 실링과 안전 여백을 적용한 분리 패널입니다.","3D 두께는 검토용 가정이며 충전 팽창·주름·제조 적합성 승인이 아닙니다."]
    if net_w>2000 or net_h>2000:
        _reject("STRUCTURE_NET_TOO_LARGE", "전개도는 각 변 2000mm 이하만 지원합니다.")
    for face in faces:
        face["registered_structure"]=True
    geometry={"template_id":definition["family"],"geometry_template_id":definition["family"],"unit":"mm",**inputs,"faces":faces,"structural_parts":parts,"fold_lines":fold_lines,
            "net_width_mm":round(net_w,4),"net_height_mm":round(net_h,4),"holes":[],"assumptions":assumptions,
            "dimension_semantics":deepcopy(definition["dimension_semantics"]),"production_enabled":False,
            "approval_status":"registered_review_only","label":"등록 구조 검토 · 제작 사용 불가"}
    if definition.get("finishing") is not None:
        from .finishing import apply_registered_finishing
        apply_registered_finishing(geometry,definition["finishing"])
    return geometry
