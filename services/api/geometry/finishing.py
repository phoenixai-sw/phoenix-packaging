"""Bounded pouch finishing and evidence-bound physical approval values.

No approval is inferred here. Registry code must bind the normalized value's
hash to immutable manufacturer evidence before production preflight accepts it.
"""
from copy import deepcopy
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from ..schemas import Hole, PouchFeatures
from .validation import GeometryValidationError, _r, normalize_mm
from .pouch_features import normalize_pouch_features, apply_pouch_features, physical_pouch_features

DELIVERY = "separate_process_pdf_v1"
FEATURE_POLICY = "pouch-finishing-v1"


class FinishingDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    pouch_features: PouchFeatures | None = None
    holes: list[Hole] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def physical_holes(self):
        if any(h.face_id != "front" for h in self.holes):
            raise ValueError("등록 가공의 구멍은 앞면 물리 좌표로 입력해야 합니다.")
        if self.pouch_features is None and not self.holes:
            raise ValueError("등록 가공에는 구멍 또는 파우치 가공값이 필요합니다.")
        return self


class PhysicalHole(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    center_x_mm: float = Field(ge=0, le=600, strict=True)
    center_y_mm: float = Field(ge=0, le=800, strict=True)
    diameter_mm: float = Field(ge=4, le=10, strict=True)


class FinishingApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal["1.0"] = "1.0"
    geometry_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    pouch_features: dict | None = None
    holes: list[PhysicalHole] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def canonical(self):
        if self.pouch_features is not None:
            self.pouch_features = physical_pouch_features(PouchFeatures.model_validate(self.pouch_features).model_dump())
        for h in self.holes:
            h.center_x_mm = normalize_mm(h.center_x_mm, "mm")
            h.center_y_mm = normalize_mm(h.center_y_mm, "mm")
            h.diameter_mm = normalize_mm(h.diameter_mm, "mm")
        self.holes.sort(key=lambda h: (h.center_x_mm, h.center_y_mm, h.diameter_mm))
        if len({(h.center_x_mm,h.center_y_mm,h.diameter_mm) for h in self.holes}) != len(self.holes):
            raise ValueError("중복된 물리 구멍은 승인할 수 없습니다.")
        if self.pouch_features is None and not self.holes:
            raise ValueError("승인할 가공값이 없습니다.")
        return self


def parse_finishing_approval(value):
    try:
        return FinishingApproval.model_validate(value).model_dump(mode="json")
    except (ValidationError, GeometryValidationError) as exc:
        raise GeometryValidationError("FINISHING_APPROVAL_INVALID", "승인 가공의 구조 해시·구멍·지퍼·노치 치수를 확인해 주세요.", "approved_finishing") from exc


def finishing_approval_for_geometry(geometry):
    holes=[]
    for h in geometry.get("holes", []):
        x=h["center_x_mm"] if h["face_id"]=="front" else geometry["width_mm"]-h["center_x_mm"]
        holes.append({"center_x_mm":x,"center_y_mm":h["center_y_mm"],"diameter_mm":h["diameter_mm"]})
    return parse_finishing_approval({"geometry_hash":finishing_geometry_hash(geometry),
        "pouch_features":physical_pouch_features(geometry.get("pouch_features")),"holes":holes})


def finishing_geometry_hash(geometry):
    """Physical surfaces/guards, independent of database version and hole UI IDs."""
    from .snapshots import canonical_hash
    def clean(value):
        if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if k not in {"id","hole_id"}}
        if isinstance(value,(list,tuple)):return [clean(v) for v in value]
        if isinstance(value,(int,float)) and not isinstance(value,bool):return round(float(value),4)
        return value
    physical={k:geometry.get(k) for k in ("template_id","width_mm","height_mm","bottom_mm","depth_mm","net_width_mm","net_height_mm","structural_parts","fold_lines","dimension_semantics")}
    physical["faces"]=[{"face_id":f["id"],**{k:f.get(k) for k in ("width_mm","height_mm","net","regions")}} for f in geometry["faces"]]
    # A hole list order is not a manufacturing difference.
    for face in physical["faces"]:
        face["regions"]=deepcopy(face["regions"])
        for key in ("hole","no_print"):
            face["regions"][key]=sorted((clean(x) for x in face["regions"].get(key,[])),key=canonical_hash)
    return canonical_hash(clean(physical))


def validate_finishing_for_template(kind, dims, approved_finishing, definition=None):
    """Rebuild trusted geometry; this validates evidence data, never grants approval."""
    spec=parse_finishing_approval(approved_finishing)
    if definition is not None:
        from .definitions import parse_definition
        from .recipes import resolve_recipe,normalized_inputs
        definition=parse_definition(definition)
        if definition["family"]!=kind:raise GeometryValidationError("FINISHING_APPROVAL_MISMATCH","가공 구조 종류가 등록 도면과 다릅니다.")
        geometry=resolve_recipe(definition,normalized_inputs(dims))
    else:
        from .structures import build_geometry
        holes=[{"id":f"approved-{i}","face_id":"front",**h} for i,h in enumerate(spec["holes"])]
        geometry=build_geometry(kind,dims["width_mm"],dims["height_mm"],bottom_mm=dims.get("bottom_mm"),depth_mm=dims.get("depth_mm"),holes=holes,pouch_features=spec["pouch_features"])
    if finishing_approval_for_geometry(geometry)!=spec:
        raise GeometryValidationError("FINISHING_APPROVAL_MISMATCH","승인 가공의 물리 치수·구조 해시가 등록 도면과 다릅니다.","approved_finishing")
    return spec


def finishing_approval_issues(geometry, template, profile):
    """Only the trusted registry payload may enter this production gate."""
    if not geometry.get("holes") and geometry.get("pouch_features") is None:
        return []
    issues=[]
    def add(code,message):issues.append({"code":code,"message":message,"severity":"error","scope":"production"})
    if profile.get("finishing_delivery") != DELIVERY:
        add("FINISHING_DELIVERY_REQUIRED", "승인된 프로필에 분리 CUT·가공 안내 전달 규칙이 필요합니다.")
    from .snapshots import canonical_hash
    try:
        expected=finishing_approval_for_geometry(geometry)
        actual=parse_finishing_approval(template.get("approved_finishing"))
        if actual != expected:
            add("FINISHING_APPROVAL_MISMATCH", "구멍·개봉부·지퍼·노치 또는 구조 해시가 승인 가공값과 다릅니다.")
        if (template.get("approval") or {}).get("finishing_hash") != canonical_hash(actual):
            add("FINISHING_EVIDENCE_REQUIRED", "가공값 해시가 제조사 도면 승인 증빙에 연결되어 있지 않습니다.")
    except GeometryValidationError as exc:
        add(exc.code, exc.message)
    return issues


def apply_registered_finishing(geometry, value):
    """Static definition additions; never changes legacy recipes without opt-in."""
    spec=FinishingDefinition.model_validate(value).model_dump(mode="json")
    w,h=geometry["width_mm"],geometry["height_mm"]
    features=normalize_pouch_features(spec["pouch_features"],geometry["template_id"],w,h)
    original={f["id"]:deepcopy(f["regions"]["safe"]) for f in geometry["faces"]}
    apply_pouch_features(geometry,features)
    for face in geometry["faces"]:
        if face["id"] not in {"front","back"}:continue
        regions=face["regions"];safe=regions["safe"];before=original[face["id"]]
        # Registered safe insets remain lower bounds, including thick top seals.
        bottom=before["y_mm"]+before["height_mm"]
        safe["y_mm"]=max(safe["y_mm"],before["y_mm"])
        safe["height_mm"]=round(bottom-safe["y_mm"],4)
        if safe["height_mm"]<=0:
            raise GeometryValidationError("EMPTY_SAFE_AREA","등록 가공 아래 안전영역이 비어 있습니다.")
        if features is None:
            regions["hole_allowed"]=_r(20,16,w-40,12)
        if regions.get("zipper"):
            z=regions["zipper"]["band"]
            z.update(x_mm=before["x_mm"],width_mm=before["width_mm"])
            regions["zipper"]["line"].update(x1_mm=z["x_mm"],x2_mm=z["x_mm"]+z["width_mm"])
            for region in regions["no_print"]+regions.get("structural_guards",[]):
                if region.get("kind")=="zipper_guard":region.update(x_mm=z["x_mm"],width_mm=z["width_mm"])
            # A zipper may not occupy a registered solid seal/no-print region.
            from .recipes import _overlap
            if any(_overlap(z,r) for r in regions["no_print"] if r.get("kind") not in {"header_guard","zipper_guard","tear_guard","notch_guard"}):
                raise GeometryValidationError("ZIPPER_SEAL_COLLISION","지퍼 대역이 등록 실링·금지영역과 겹칩니다.")
    geometry["holes"]=deepcopy(spec["holes"])
    for face in geometry["faces"]:
        if face["id"] not in {"front","back"}:continue
        for hole in spec["holes"]:
            x=hole["center_x_mm"] if face["id"]=="front" else w-hole["center_x_mm"]
            y,r=hole["center_y_mm"],hole["diameter_mm"]/2
            face["regions"]["hole"].append({"id":hole["id"],"kind":"circle","center_x_mm":x,"center_y_mm":y,"radius_mm":r,"closed":True})
            face["regions"]["no_print"].append({"kind":"hole_guard","hole_id":hole["id"],**_r(x-r-2,y-r-2,2*r+4,2*r+4)})
    from .collisions import collision_report
    issues=collision_report({"holes":spec["holes"],"faces":[{**f,"objects":[]} for f in geometry["faces"]]},geometry)
    if issues:
        raise GeometryValidationError(issues[0]["code"],issues[0]["message"],"structure_definition.finishing")
