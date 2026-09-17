"""Review-only registered structures. Geometry comes from the server registry, never the client."""
from .contracts.base import Envelope, ERROR_RESPONSES
from .contracts import core as C
from .contracts import geometry as G
from copy import deepcopy
from uuid import UUID
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from .auth import require_auth
from .business import owned_record
from .database import utcnow
from .editor_sessions import enforce_edit_lease
from .errors import APIError
from .feature_models import RegistryVersion, AuditEvent
from .models import Project
from .geometry import validate_scene, GeometryValidationError
from .geometry.definitions import Dimensions
from .geometry.snapshots import compile_structure, structure_ref


class PreviewBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    template_version_id: str=Field(min_length=1,max_length=100)
    inputs: Dimensions
    project_id: UUID | None=None
    base_revision: int | None=Field(default=None,ge=1)


class ApplyBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    template_version_id: str=Field(min_length=1,max_length=100)
    inputs: Dimensions
    base_revision: int=Field(ge=1)


class ValidateBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    structure_definition: dict
    inputs: Dimensions | None=None


def _available(db,identity):
    row=db.get(RegistryVersion,identity)
    if not row or row.kind!="template" or row.status=="revoked" or row.details.get("review_available") is not True or not row.details.get("structure_definition"):
        raise APIError(404,"STRUCTURE_NOT_AVAILABLE","검토용으로 공개한 등록 구조를 찾을 수 없습니다.")
    return row


def _compile(row,inputs):
    return compile_structure(row.details["structure_definition"],inputs,row.id)


def _candidate(project,snapshot):
    geometry=snapshot["geometry"];inputs=snapshot["normalized_inputs"]
    if geometry["template_id"]!=project.template_id:
        raise APIError(422,"GEOMETRY_FAMILY_MISMATCH","현재 프로젝트와 같은 포장 종류의 구조를 선택해 주세요.")
    scene=deepcopy(project.scene);lookup={face["id"]:face for face in scene["faces"]}
    if set(lookup)!={face["id"] for face in geometry["faces"]}:
        raise APIError(422,"FACE_SET_MISMATCH","기존 편집 면을 자동 삭제하거나 새 면으로 바꾸지 않습니다.")
    for face in geometry["faces"]:
        lookup[face["id"]].update({key:face[key] for key in ("width_mm","height_mm")})
    scene.update(template_version_id=snapshot["template_version_id"],structure_ref=structure_ref(snapshot),geometry_hash=snapshot["geometry_hash"],
                 bottom_mm=inputs.get("bottom_mm"),depth_mm=inputs.get("depth_mm"),confirmed_fields=[],reviewed_face_ids=[])
    if snapshot.get("definition",{}).get("feature_policy")=="pouch-finishing-v1":
        scene["holes"]=deepcopy(geometry.get("holes",[]))
        scene["pouch_features"]=deepcopy(geometry.get("pouch_features"))
    return validate_scene(scene,structure_snapshot=snapshot)


def install_structure_routes(app,db_session,project_payload,snapshot_revision):
    router=APIRouter(prefix="/v1",tags=["registered-structures"])
    def result(request,data):return {"data":data,"request_id":request.state.request_id}

    @router.post("/admin/structures/validate", response_model=Envelope[G.StructureValidation], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def validate_definition(body:ValidateBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True,authorize_write=False,enforce_membership=False)
        if not user.is_admin:raise APIError(403,"ADMIN_REQUIRED","플랫폼 관리자 권한이 필요합니다.")
        from .geometry.definitions import parse_definition
        definition=parse_definition(body.structure_definition)
        inputs=body.inputs.model_dump(exclude_none=True) if body.inputs else definition.get("dimensions") or {"width_mm":definition["width_range_mm"]["minimum"],"height_mm":definition["height_range_mm"]["minimum"]}
        snapshot=compile_structure(definition,inputs,"registration-validation")
        value={"normalized_definition":definition,"geometry":snapshot["geometry"],"definition_hash":snapshot["definition_hash"],"review_only":True,"production_enabled":False,"approved_dimensions":snapshot["normalized_inputs"]}
        if definition.get("feature_policy")=="pouch-finishing-v1":
            from .geometry.finishing import finishing_approval_for_geometry
            value["approved_finishing"]=finishing_approval_for_geometry(snapshot["geometry"])
        return result(request,value)

    @router.get("/structures", response_model=Envelope[C.Items[G.RegisteredStructure]], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def listing(request:Request,db=Depends(db_session)):
        require_auth(request,db)
        rows=db.scalars(select(RegistryVersion).where(RegistryVersion.kind=="template",RegistryVersion.status!="revoked").order_by(RegistryVersion.created_at.desc()))
        items=[]
        for row in rows:
            definition=row.details.get("structure_definition")
            if row.details.get("review_available") is not True or not definition:continue
            items.append({"id":row.id,"name":row.name,"manufacturer":row.manufacturer,"status":row.status,"is_demo":row.is_demo,
                          "family":definition["family"],"recipe_id":definition["recipe_id"],"definition":definition,
                          "review_only":True,"production_enabled":False})
        return result(request,{"items":items})

    @router.post("/structures/preview", response_model=Envelope[G.StructurePreview], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def preview(body:PreviewBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True,authorize_write=False)
        snapshot=_compile(_available(db,body.template_version_id),body.inputs.model_dump(exclude_none=True))
        issues=[]
        if (body.project_id is None)!=(body.base_revision is None):raise APIError(422,"STRUCTURE_PROJECT_CONTEXT_REQUIRED","기존 디자인 확인에는 프로젝트와 저장번호가 함께 필요합니다.")
        if body.project_id:
            project=owned_record(db,Project,body.project_id,user.tenant_id)
            if project.base_revision!=body.base_revision:raise APIError(409,"REVISION_CONFLICT","최신 디자인을 저장한 뒤 확인해 주세요.")
            try:_candidate(project,snapshot)
            except GeometryValidationError as exc:issues.append(exc.as_dict())
            except APIError as exc:issues.append({"code":exc.code,"message":exc.message})
        return result(request,{"geometry":snapshot["geometry"],"structure_ref":structure_ref(snapshot),"review_only":True,"production_enabled":False,
                               "layout_checked":body.project_id is not None,"layout_issues":issues,"can_apply":not issues if body.project_id else None})

    @router.patch("/projects/{identity}/structure", response_model=Envelope[C.ProjectData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def apply(identity:UUID,body:ApplyBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True)
        project=owned_record(db,Project,identity,user.tenant_id)
        enforce_edit_lease(db,project,request)
        # Lock the exact catalog row until CAS commits. Revocation cannot race the application.
        row=db.scalar(select(RegistryVersion).where(RegistryVersion.id==body.template_version_id).with_for_update().execution_options(populate_existing=True))
        row=_available(db,body.template_version_id)
        if project.base_revision!=body.base_revision:
            raise APIError(409,"REVISION_CONFLICT","프로젝트가 변경되었습니다. 최신 저장본으로 다시 적용해 주세요.")
        inputs=body.inputs.model_dump(exclude_none=True)
        snapshot=_compile(row,inputs)
        # Preserve every object and its mm coordinates; incompatible layouts must be adjusted explicitly.
        scene=_candidate(project,snapshot)
        snapshot_revision(db,project,"before_structure_change")
        values={"scene":scene,"structure_snapshot":snapshot,"template_version_id":row.id,"print_profile_version_id":None,
                "width_mm":inputs["width_mm"],"height_mm":inputs["height_mm"],"bottom_mm":inputs.get("bottom_mm"),"depth_mm":inputs.get("depth_mm"),
                "base_revision":body.base_revision+1,"updated_at":utcnow()}
        won=db.execute(update(Project).where(Project.id==project.id,Project.tenant_id==user.tenant_id,Project.base_revision==body.base_revision).values(**values).execution_options(synchronize_session=False))
        if won.rowcount!=1:raise APIError(409,"REVISION_CONFLICT","다른 구조 변경과 충돌했습니다. 최신 저장본을 확인해 주세요.")
        db.refresh(project);snapshot_revision(db,project,"structure_changed")
        db.add(AuditEvent(tenant_id=user.tenant_id,actor_id=user.id,action="structure_applied",entity_id=project.id,
                         details={"template_version_id":row.id,"definition_hash":snapshot["definition_hash"],"geometry_hash":snapshot["geometry_hash"],"review_only":True}))
        db.commit();return result(request,project_payload(project))

    app.include_router(router)
