"""Server-owned immutable structure snapshots; no registry/network access in rendering."""
from copy import deepcopy
from hashlib import sha256
import json
from .definitions import parse_definition
from .recipes import normalized_inputs, resolve_recipe
from .validation import GeometryValidationError

ENGINE_VERSION = "structure-v2.1"
SNAPSHOT_VERSION = "2.0"


def canonical_hash(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def compile_structure(definition, inputs, template_version_id):
    if not isinstance(template_version_id,str) or not 1<=len(template_version_id)<=100 or template_version_id.endswith("-demo-v1"):
        raise GeometryValidationError("INVALID_STRUCTURE_VERSION", "등록 구조 버전 식별자를 확인해 주세요.", "template_version_id")
    definition=parse_definition(definition);inputs=normalized_inputs(inputs)
    geometry=resolve_recipe(definition,inputs)
    definition_hash=canonical_hash(definition);input_hash=canonical_hash(inputs)
    physical_hash=canonical_hash(geometry)
    if definition.get('feature_policy')=='pouch-finishing-v1':
        from .finishing import finishing_geometry_hash
        # UI IDs and disabled slider values are not a new physical product.
        physical_hash=finishing_geometry_hash(geometry)
    digest=canonical_hash({"definition_hash":definition_hash,"engine_version":ENGINE_VERSION,"input_hash":input_hash,"geometry":geometry,"template_version_id":template_version_id})
    geometry.update(template_version_id=template_version_id,geometry_hash=digest)
    return {"snapshot_version":SNAPSHOT_VERSION,"template_version_id":template_version_id,"engine_version":ENGINE_VERSION,
            "definition":definition,"definition_hash":definition_hash,"normalized_inputs":inputs,"input_hash":input_hash,
            "geometry_hash":digest,"physical_geometry_hash":physical_hash,"geometry":geometry}


def validate_snapshot(snapshot):
    if not isinstance(snapshot,dict) or snapshot.get("snapshot_version")!=SNAPSHOT_VERSION or snapshot.get("engine_version")!=ENGINE_VERSION:
        raise GeometryValidationError("STRUCTURE_SNAPSHOT_VERSION", "지원하는 등록 구조 스냅샷이 필요합니다.", "structure_snapshot")
    required={"snapshot_version","template_version_id","engine_version","definition","definition_hash","normalized_inputs","input_hash","geometry_hash","physical_geometry_hash","geometry"}
    if set(snapshot)!=required:
        raise GeometryValidationError("INVALID_STRUCTURE_SNAPSHOT", "등록 구조 스냅샷 필드를 확인해 주세요.", "structure_snapshot")
    expected=compile_structure(snapshot["definition"],snapshot["normalized_inputs"],snapshot["template_version_id"])
    try:
        matches=canonical_hash(snapshot)==canonical_hash(expected)
    except (TypeError,ValueError):
        matches=False
    if not matches:
        raise GeometryValidationError("STRUCTURE_SNAPSHOT_MISMATCH", "동결한 등록 구조와 현재 데이터가 일치하지 않습니다.", "structure_snapshot")
    return deepcopy(expected["geometry"])


def structure_ref(snapshot):
    return {key:snapshot[key] for key in ("template_version_id","definition_hash","engine_version","geometry_hash")}


def geometry_from_snapshot(scene, snapshot):
    geometry=validate_snapshot(snapshot)
    if scene.get("structure_ref")!=structure_ref(snapshot) or scene.get("template_version_id")!=snapshot["template_version_id"] or scene.get("template_kind")!=geometry["template_id"]:
        raise GeometryValidationError("STRUCTURE_REFERENCE_MISMATCH", "장면과 등록 구조 버전이 일치하지 않습니다.", "structure_ref")
    if scene.get("geometry_hash")!=snapshot["geometry_hash"]:
        raise GeometryValidationError("STRUCTURE_HASH_MISMATCH", "장면의 기하 해시가 동결한 구조와 다릅니다.", "geometry_hash")
    if snapshot["definition"].get("feature_policy")=="pouch-finishing-v1":
        from .pouch_features import physical_pouch_features, normalize_pouch_features
        selected=normalize_pouch_features(scene.get("pouch_features"),geometry["template_id"],geometry["width_mm"],geometry["height_mm"])
        if canonical_hash(scene.get("holes") or [])!=canonical_hash(geometry.get("holes") or []) or physical_pouch_features(selected)!=physical_pouch_features(geometry.get("pouch_features")):
            raise GeometryValidationError("REGISTERED_FINISHING_MISMATCH", "장면의 가공값은 등록 구조에 동결한 값과 같아야 합니다.", "structure_ref")
    elif scene.get("holes") or scene.get("pouch_features"):
        raise GeometryValidationError("REGISTERED_FEATURES_UNSUPPORTED", "이 등록 구조 버전에는 추가 구멍·지퍼·노치 가공을 지원하지 않습니다.", "structure_ref")
    for key in ("bottom_mm","depth_mm"):
        if scene.get(key)!=snapshot["normalized_inputs"].get(key):
            raise GeometryValidationError("STRUCTURE_INPUTS_MISMATCH", "장면 치수와 동결한 구조 입력이 다릅니다.", key)
    return geometry


def project_geometry(project):
    from .structures import geometry_for_scene
    scene=(project if project.get("schema_version")=="1.0" else project["scene"]) if isinstance(project,dict) else project.scene
    snapshot=project.get("structure_snapshot") if isinstance(project,dict) else getattr(project,"structure_snapshot",None)
    return geometry_for_scene(scene,structure_snapshot=snapshot)
