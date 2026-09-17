"""Conservative preflight. Registry evidence is supplied only by a trusted server caller."""
from copy import deepcopy
from ..geometry import GeometryValidationError, geometry_for_scene, validate_scene, TEMPLATES, normalize_mm
from .review_pdf import _scene_from_project, _layout_text, _resolve_image
from ..image_quality_metadata import resolver_quality_metrics

CAPABILITIES={"pdf_standard":"PDF","color_space":"RGB","font_mode":"embedded","layout":"face_pages","bleed_mm":0,
              "vector_text":True,"vector_barcode":True,"pdf_x":False,"cmyk":False,"spot_colors":False,"white_ink":False,"overprint":False,"outlined_fonts":False}
DEFAULT_CONFIRMED_FIELDS=["product_name","net_weight","ingredients","allergens","manufacturer","storage"]


def validate_output_requirements(requirements):
    """Shared by readiness and final preflight; advertised capabilities aren't arbitrary registry keys."""
    issues=[]
    if isinstance(requirements,dict) and requirements.get("adapter_id")=="icc-cmyk-outline-v1":
        from .print_profile import parse_print_profile
        try:
            profile=parse_print_profile(requirements)
            return {"issues":[],"min_ppi":profile["min_ppi"],"required_fields":profile["required_fields"],"print_profile":profile}
        except GeometryValidationError as exc:
            return {"issues":[{"code":exc.code,"message":exc.message,"severity":"error","scope":"production"}],"min_ppi":300,"required_fields":DEFAULT_CONFIRMED_FIELDS}
    def add(code,message,**details):
        issues.append({"code":code,"message":message,"severity":"error","scope":"production",**details})
    if not isinstance(requirements,dict):
        add("UNSUPPORTED_OUTPUT_REQUIREMENT","출력 요구조건은 항목별 값으로 등록해야 합니다.")
        requirements={}
    required_capabilities={"pdf_standard":"PDF","color_space":"RGB","font_mode":"embedded"}
    optional_capabilities={"layout":"face_pages","bleed_mm":0,"pdf_x":False,"cmyk":False,"spot_colors":False,"white_ink":False,"overprint":False,"outlined_fonts":False}
    for key,value in {**required_capabilities,**optional_capabilities}.items():
        if (key in required_capabilities and key not in requirements) or requirements.get(key,value)!=value:
            add("UNSUPPORTED_OUTPUT_CAPABILITY",f"현재 출력기는 {key}={value} 조건만 지원합니다.",field=f"profile.requirements.{key}")
    allowed=set(required_capabilities)|set(optional_capabilities)|{"min_ppi","required_fields"}
    for key in sorted(set(requirements)-allowed):
        add("UNSUPPORTED_OUTPUT_REQUIREMENT",f"검증되지 않은 출력 요구조건입니다: {key}",field=f"profile.requirements.{key}")
    min_ppi=requirements.get("min_ppi",150)
    if isinstance(min_ppi,bool) or not isinstance(min_ppi,(int,float)) or not 72<=min_ppi<=2400:
        add("INVALID_PPI_REQUIREMENT","제조사 최소 이미지 해상도 조건을 확인해 주세요.");min_ppi=150
    required_fields=requirements.get("required_fields",DEFAULT_CONFIRMED_FIELDS)
    if not isinstance(required_fields,list) or not required_fields or any(not isinstance(field,str) or not field.strip() for field in required_fields):
        add("INVALID_REQUIRED_FIELDS","제조사가 요구하는 고객 확인 필드 목록이 필요합니다.");required_fields=DEFAULT_CONFIRMED_FIELDS
    return {"issues":issues,"min_ppi":min_ppi,"required_fields":required_fields}


def preflight_project(project:dict, approved_conditions:dict|None=None, asset_resolver=None) -> dict:
    issues=[]
    def add(code,message,scope="production",**details):
        issues.append({"code":code,"message":message,"severity":"error","scope":scope,**details})
    scene=None; geometry=None
    try:
        scene=validate_scene(_scene_from_project(project), structure_snapshot=project.get("structure_snapshot"))
        geometry=geometry_for_scene(scene, structure_snapshot=project.get("structure_snapshot"))
    except GeometryValidationError as exc:
        add(exc.code,exc.message,"review",field=exc.field)
    conditions=approved_conditions or {}
    template=conditions.get("template") or {}; profile=conditions.get("profile") or {}
    requirements=profile.get("requirements") or {}
    print_engine=requirements.get("adapter_id")=="icc-cmyk-outline-v1"
    if project.get("structure_snapshot") is not None or (scene and scene.get("structure_ref")):
        if not print_engine:
            add("STRUCTURE_V2_PRODUCTION_UNSUPPORTED","등록 구조 V2는 검증된 ICC 제작 어댑터와 실제 승인 조건이 필요합니다.")
        else:
            from ..geometry.snapshots import canonical_hash
            snapshot=project.get("structure_snapshot") or {}
            if snapshot.get("definition_hash")!=canonical_hash(template.get("structure_definition")) or (template.get("approval") or {}).get("structure_definition_hash")!=snapshot.get("definition_hash"):
                add("STRUCTURE_APPROVAL_MISMATCH","승인 증빙에 연결된 구조 정의 해시가 현재 동결 구조와 일치하지 않습니다.")
    if conditions.get("registry_verified") is not True:
        add("REGISTRY_UNVERIFIED","서버에서 승인 증빙을 확인한 제조사 템플릿·출력 프로파일이 필요합니다.")
    for label,item in (("template",template),("profile",profile)):
        if item.get("status")!="approved" or item.get("is_demo") is not False or not item.get("id"):
            add("APPROVAL_REQUIRED",f"승인된 비데모 {label} 버전이 필요합니다.",field=label)
        evidence=item.get("approval") or {}
        if not all(evidence.get(key) for key in ("evidence_asset_id","approved_by","approved_at","source","license")):
            add("APPROVAL_EVIDENCE_REQUIRED",f"{label} 승인 증빙·승인자·시각·출처·사용권이 필요합니다.",field=label)
    if template.get("id") in TEMPLATES.values() or (scene and (scene.get("template_version_id") or TEMPLATES["three-side-seal"]) in TEMPLATES.values()):
        add("DEMO_PRODUCTION_FORBIDDEN","기본 데모 템플릿은 제작용으로 승인할 수 없습니다.")
    if scene and scene.get("template_version_id")!=template.get("id"):
        add("TEMPLATE_VERSION_MISMATCH","장면과 승인 템플릿 버전이 일치하지 않습니다.")
    if geometry and template.get("geometry_template_id") not in (geometry["template_id"],geometry["geometry_template_id"]):
        add("GEOMETRY_APPROVAL_MISMATCH","승인 템플릿이 현재 구조 엔진 버전과 일치하지 않습니다.")
    if not template.get("billing_family_key"):
        add("BILLING_FAMILY_REQUIRED","승인 템플릿의 구조 패밀리 식별자가 필요합니다.")
    if not isinstance(conditions.get("material"),str) or not conditions["material"].strip():
        add("MATERIAL_REQUIRED","제조사와 확정한 재질을 선택해 주세요.")
    if not template.get("manufacturer") or template.get("manufacturer")!=profile.get("manufacturer"):
        add("MANUFACTURER_MISMATCH","템플릿과 인쇄 프로파일의 승인 제조사가 일치해야 합니다.")
    for label,item in (("template",template),("profile",profile)):
        if not item.get("material") or item.get("material")!=conditions.get("material"):
            add("MATERIAL_APPROVAL_MISMATCH",f"{label}의 승인 재질과 선택한 재질이 일치해야 합니다.",field=label)
    dimensions=template.get("approved_dimensions")
    if not isinstance(dimensions,dict) or not dimensions:
        add("APPROVED_DIMENSIONS_REQUIRED","제조사 증빙에 연결된 정확한 승인 규격이 필요합니다.")
    elif geometry:
        keys=["width_mm","height_mm"]+[key for key in ("bottom_mm","depth_mm") if geometry.get(key) is not None]
        try:
            if any(key not in dimensions or normalize_mm(dimensions[key],"mm",key)!=geometry[key] for key in keys):
                add("APPROVED_DIMENSIONS_MISMATCH","현재 규격이 제조사 승인 규격과 일치하지 않습니다.")
            if any(key not in keys and value not in (None,0) for key,value in dimensions.items()):
                add("APPROVED_DIMENSIONS_MISMATCH","승인 규격에 현재 구조와 맞지 않는 치수가 있습니다.")
        except GeometryValidationError:
            add("APPROVED_DIMENSIONS_INVALID","제조사 승인 규격이 유효한 mm 값이어야 합니다.")
    revision_id=project.get("revision_id")
    if not revision_id or str(conditions.get("confirmed_revision_id"))!=str(revision_id):
        add("REVISION_CONFIRMATION_REQUIRED","출력할 불변 리비전을 검토하고 확정해 주세요.")
    output_rules=validate_output_requirements(requirements)
    issues.extend(output_rules["issues"])
    min_ppi,required_fields=output_rules["min_ppi"],output_rules["required_fields"]
    if print_engine and scene and output_rules.get("print_profile"):
        from .print_pdf import inspect_print
        from ..geometry.snapshots import canonical_hash
        frozen=project.get("print_output") or {}
        if frozen.get("mode")!="production" or frozen.get("profile_id")!=profile.get("id") or canonical_hash(frozen.get("requirements"))!=canonical_hash(output_rules["print_profile"]):
            add("PRINT_SNAPSHOT_REQUIRED","서버가 동결한 ICC·프로필 버전이 필요합니다.")
        try:
            checked=inspect_print(project,output_rules["print_profile"],asset_resolver)
            issues.extend({**issue,"scope":"production"} for issue in checked["issues"])
        except GeometryValidationError as exc:add(exc.code,exc.message,field=exc.field)
    if scene:
        expected={f["id"] for f in scene["faces"]}
        reviewed=set(conditions.get("reviewed_face_ids") or [])
        if not expected.issubset(reviewed):
            add("FACE_REVIEW_REQUIRED","모든 인쇄 면을 현재 리비전에서 확인해 주세요.",face_ids=sorted(expected-reviewed))
        for field in required_fields:
            if field not in (scene.get("confirmed_fields") or []):
                add("FIELD_CONFIRMATION_REQUIRED","법정 표시·제품 정보를 고객이 확인해야 합니다.",field=field)
        if scene.get("pouch_features") is not None:
            add("POUCH_FEATURES_PRODUCTION_UNSUPPORTED","개봉부·지퍼·뜯는 노치는 검토 PDF에서 확인할 수 있습니다. 제조사 가공 출력 규격 검증 전 제작용 출력은 지원하지 않습니다.")
        if scene.get("holes"):
            # PDF spot cut contours are not implemented; fail closed rather than erase circles and claim a cut file.
            add("HOLE_PRODUCTION_UNSUPPORTED","걸이 구멍의 제조사 칼선·가공 출력은 현재 기본 RGB 출력기에서 지원하지 않습니다.")
        for face in scene["faces"]:
            for obj in face["objects"]:
                if not obj["visible"] or not obj["print_enabled"]: continue
                details={"face_id":face["id"],"object_id":obj["id"]}
                try:
                    if obj["type"]=="text": _layout_text(obj,asset_resolver)
                    elif obj["type"]=="image":
                        _,pixels=_resolve_image(obj["asset_id"],asset_resolver)
                        quality=resolver_quality_metrics(asset_resolver,obj["asset_id"],pixels,obj)
                        ppi=quality["effective_ppi"]
                        if ppi+0.001<min_ppi:
                            add("LOW_PPI",f"이미지 유효 해상도 {ppi:.1f}ppi가 제조사 최소 {min_ppi:g}ppi 미만입니다.",effective_ppi=round(ppi,2),**details)
                        if (quality["resampled"] or quality["extended"]) and quality["original_effective_ppi"]+0.001<min_ppi:
                            add("ORIGINAL_LOW_PPI",f"확대된 이미지의 원본 디테일 {quality['original_effective_ppi']:.1f}ppi가 제조사 최소 {min_ppi:g}ppi 미만입니다. 픽셀 확대는 원본 해상도를 복원하지 않습니다.",original_effective_ppi=round(quality["original_effective_ppi"],2),**details)
                    elif obj["type"]=="barcode":
                        if obj.get("barcode_usage")=="sample":
                            add("SAMPLE_BARCODE_PRODUCTION_FORBIDDEN","샘플 바코드는 검토 전용이며 정식 상품 번호와 사용 권한 확인 전 제작용으로 출력할 수 없습니다.",**details)
                        elif obj.get("barcode_owned") is not True:
                            add("BARCODE_OWNERSHIP_REQUIRED","GS1 발급 번호와 사용 권한을 고객이 확인해야 합니다.",**details)
                except GeometryValidationError as exc:
                    add(exc.code,exc.message,"review",field=exc.field,**details)
    basic_review = None
    if scene:
        from .public_profiles import inspect_basic_review
        try:
            basic_review = inspect_basic_review(project, asset_resolver)
            issues.extend(basic_review["issues"])
        except GeometryValidationError as exc:
            if not any(i["code"] == exc.code and i["scope"] == "review" for i in issues):
                add(exc.code, exc.message, "review", field=exc.field)
    review_allowed=not any(i["scope"]=="review" and i["severity"]=="error" for i in issues)
    production_allowed=review_allowed and not any(i["severity"]=="error" for i in issues)
    effective_capabilities=deepcopy(CAPABILITIES)
    if print_engine and output_rules.get("print_profile"):
        from .print_profile import capabilities
        effective_capabilities=capabilities(output_rules["print_profile"])
    return {"schema_version":"1.0","status":"pass" if production_allowed else "blocked","review_allowed":review_allowed,"production_allowed":production_allowed,
            "issues":issues,"basic_review":basic_review,"capabilities":effective_capabilities,"geometry_hash":geometry["geometry_hash"] if geometry else None,
            "faces":[f["id"] for f in scene["faces"]] if scene else [],"revision_id":str(revision_id) if revision_id else None}
