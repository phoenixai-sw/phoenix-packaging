"""Tenant catalog, stable variant bindings, team seats and workspace isolation."""
from copy import deepcopy
from datetime import timedelta
import secrets
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import delete, select, update
from .auth import require_auth, hash_token, auth_payload
from .database import utcnow
from .errors import APIError
from .models import Asset, Job, Project, Revision, Tenant, User
from .feature_models import Brand, Product, Variant, Workspace, WorkspaceMember, Membership, Invitation, AuditEvent
from .billing.payments import entitlements
from .billing.policy import aware


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BrandBody(Body):
    name: str = Field(min_length=1, max_length=120)
    colors: list[str] = Field(default_factory=list, max_length=12)
    font_ids: list[Literal["NotoSansKR"]] = Field(default_factory=lambda:["NotoSansKR"])
    logo_asset_id: UUID | None = None


class VariantBody(Body):
    id: UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    sku: str = Field(default="",max_length=80)
    barcode: str = Field(default="",max_length=13)
    net_weight: str = Field(default="",max_length=80)
    net_quantity: float | None = Field(default=None,gt=0,le=1000000,allow_inf_nan=False)
    net_unit: Literal["g","kg","ml","l","ea"] | None = None
    ingredients: str = Field(default="",max_length=6000)
    allergens: str = Field(default="",max_length=2000)
    storage: str = Field(default="",max_length=2000)
    manufacturer: str = Field(default="",max_length=2000)


class ProductBody(Body):
    name: str = Field(min_length=1,max_length=160)
    brand_id: UUID | None = None
    description: str = Field(default="",max_length=4000)
    variants: list[VariantBody] = Field(default_factory=list,max_length=100)


class WorkspaceBody(Body):
    name: str = Field(min_length=1,max_length=120)
    description: str = Field(default="",max_length=4000)


class InviteBody(Body):
    email: EmailStr
    role: Literal["editor","viewer"]
    workspace_ids: list[UUID] = Field(default_factory=list,max_length=100)


class MemberBody(Body):
    role: Literal["editor","viewer"]
    workspace_ids: list[UUID] = Field(default_factory=list,max_length=100)
    is_active: bool = True


class TokenBody(Body):
    token: str = Field(min_length=32,max_length=200)


class SwitchBody(Body):
    tenant_id: UUID


class BindingBody(Body):
    product_variant_id: UUID
    base_revision: int | None = Field(default=None,ge=1)


class DuplicateBody(Body):
    name: str | None = Field(default=None,min_length=1,max_length=160)


def workspace_access(db, user, workspace_id):
    if workspace_id is None or user.role == "owner":
        return True
    return db.scalar(select(WorkspaceMember.id).where(WorkspaceMember.workspace_id==workspace_id,WorkspaceMember.user_id==user.id,WorkspaceMember.tenant_id==user.tenant_id)) is not None


def enforce_item_access(db, item):
    user=db.info.get("principal")
    if not user: return
    if isinstance(item,(Job,Revision)):
        item=db.get(Project,item.project_id)
    if hasattr(item,"workspace_id") and not workspace_access(db,user,item.workspace_id):
        raise APIError(404,"NOT_FOUND","요청한 항목을 찾을 수 없습니다.")


def owned_record(db, model, identity, tenant):
    record=db.scalar(select(model).where(model.id==str(identity),model.tenant_id==tenant))
    if record is None: raise APIError(404,"NOT_FOUND","요청한 항목을 찾을 수 없습니다.")
    enforce_item_access(db,record)
    return record


def validate_project_links(db,user,values):
    for key,model in (("brand_id",Brand),("product_variant_id",Variant),("workspace_id",Workspace)):
        if values.get(key): owned_record(db,model,values[key],user.tenant_id)
    if values.get("workspace_id") and not workspace_access(db,user,str(values["workspace_id"])):
        raise APIError(403,"WORKSPACE_FORBIDDEN","이 고객 작업 공간에 접근할 권한이 없습니다.")
    if values.get("brand_id") and values.get("product_variant_id"):
        variant=owned_record(db,Variant,values["product_variant_id"],user.tenant_id)
        product=db.get(Product,variant.product_id)
        if product.brand_id!=str(values["brand_id"]): raise APIError(422,"BRAND_VARIANT_MISMATCH","브랜드에 속한 상품 변형을 선택해 주세요.")


def brand_payload(row):
    return {"id":row.id,"name":row.name,"colors":row.colors,"font_ids":row.font_ids,"logo_asset_id":row.logo_asset_id}


def product_payload(db,row):
    return {"id":row.id,"name":row.name,"brand_id":row.brand_id,"description":row.description,"variants":[{"id":v.id,"name":v.name,**v.details} for v in db.scalars(select(Variant).where(Variant.product_id==row.id).order_by(Variant.name))]}


def binding_changes(db,project,variant):
    product=db.get(Product,variant.product_id)
    brand=db.get(Brand,product.brand_id) if product.brand_id else None
    values={**variant.details,"product_name":product.name,"variant_name":variant.name,"brand_name":brand.name if brand else ""}
    changes=[]
    for face in project.scene["faces"]:
        for obj in face["objects"]:
            key=obj.get("binding_key")
            if not key or key not in values: continue
            field="barcode_value" if obj["type"]=="barcode" and key=="barcode" else "text"
            if obj["type"] not in {"text","barcode"}: continue
            value=str(values[key] or "")
            if field=="barcode_value" and not value: continue
            if obj.get(field)!=value:
                changes.append({"object_id":obj["id"],"face_id":face["id"],"field":field,"binding_key":key,"before":obj.get(field,""),"after":value})
    return changes


def install_business_routes(app, db_session, project_payload, snapshot_revision):
    router=APIRouter(prefix="/v1",tags=["business"])
    def result(request,data): return {"data":data,"request_id":request.state.request_id}
    def owner(request,db,mutate=False):
        user,session=require_auth(request,db,mutate=mutate)
        if user.role!="owner": raise APIError(403,"OWNER_REQUIRED","소유자만 팀 설정을 변경할 수 있습니다.")
        return user,session
    def audit(db,user,action,identity): db.add(AuditEvent(tenant_id=user.tenant_id,actor_id=user.id,action=action,entity_id=identity))
    def workspaces_for(db,user_id,tenant): return list(db.scalars(select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id==user_id,WorkspaceMember.tenant_id==tenant)))
    def assign(db,tenant,user_id,ids):
        # Caller is the tenant owner or holds a verified single-use invitation.
        # Invitation acceptance must not use the invitee's previous tenant role
        # to authorize these server-recorded target workspace IDs.
        for identity in ids:
            if db.scalar(select(Workspace.id).where(Workspace.id==str(identity),Workspace.tenant_id==tenant)) is None:
                raise APIError(404,"NOT_FOUND","초대 작업 공간을 찾을 수 없습니다.")
        db.execute(delete(WorkspaceMember).where(WorkspaceMember.user_id==user_id,WorkspaceMember.tenant_id==tenant))
        for identity in set(map(str,ids)): db.add(WorkspaceMember(tenant_id=tenant,user_id=user_id,workspace_id=identity))
    def check_seats(db,tenant,include_pending=True,exclude_user=None):
        db.scalar(select(Tenant).where(Tenant.id==tenant).with_for_update())
        access=entitlements(db,tenant)
        count=len(list(db.scalars(select(Membership.id).where(Membership.tenant_id==tenant,Membership.is_active.is_(True),Membership.user_id!=exclude_user))))+1
        if include_pending:
            count+=len(list(db.scalars(select(Invitation.id).where(Invitation.tenant_id==tenant,Invitation.accepted_at.is_(None),Invitation.revoked_at.is_(None),Invitation.expires_at>utcnow()))))
        if not access["team_access"] or count>=access["seats"]:
            raise APIError(403,"SEAT_LIMIT","팀 좌석이 부족합니다. 요금제와 초대를 확인해 주세요.")

    @router.get("/brands")
    def brands(request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db)
        return result(request,{"items":[brand_payload(r) for r in db.scalars(select(Brand).where(Brand.tenant_id==user.tenant_id).order_by(Brand.name))]})
    def write_brand(db,user,body,row):
        import re
        if any(not re.fullmatch(r"#[0-9a-fA-F]{6}",c) for c in body.colors): raise APIError(422,"COLOR_INVALID","색상은 #RRGGBB 형식으로 입력해 주세요.")
        if body.logo_asset_id: owned_record(db,Asset,body.logo_asset_id,user.tenant_id)
        for key,value in body.model_dump(mode="json").items(): setattr(row,key,value)
        db.add(row);db.flush();audit(db,user,"brand_saved",row.id);db.commit();return brand_payload(row)
    @router.post("/brands",status_code=201)
    def create_brand(body:BrandBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True)
        return result(request,write_brand(db,user,body,Brand(tenant_id=user.tenant_id)))
    @router.patch("/brands/{identity}")
    def update_brand(identity:UUID,body:BrandBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True)
        return result(request,write_brand(db,user,body,owned_record(db,Brand,identity,user.tenant_id)))

    @router.get("/products")
    def products(request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db)
        return result(request,{"items":[product_payload(db,r) for r in db.scalars(select(Product).where(Product.tenant_id==user.tenant_id).order_by(Product.name))]})
    def write_product(db,user,body,row):
        from .geometry.barcodes import validate_ean13
        if body.brand_id: owned_record(db,Brand,body.brand_id,user.tenant_id)
        row.name=body.name;row.brand_id=str(body.brand_id) if body.brand_id else None;row.description=body.description
        db.add(row);db.flush()
        seen=set()
        for entry in body.variants:
            if entry.barcode: validate_ean13(entry.barcode)
            if bool(entry.net_quantity)!=bool(entry.net_unit): raise APIError(422,"CONTENT_UNIT_REQUIRED","내용량의 수량과 단위를 함께 입력해 주세요.")
            variant=owned_record(db,Variant,entry.id,user.tenant_id) if entry.id else Variant(tenant_id=user.tenant_id,product_id=row.id)
            if variant.product_id!=row.id or (entry.id and str(entry.id) in seen): raise APIError(422,"VARIANT_MISMATCH","상품 변형 정보가 일치하지 않습니다.")
            if entry.id: seen.add(str(entry.id))
            variant.name=entry.name;variant.details=entry.model_dump(mode="json",exclude={"id","name"});variant.updated_at=utcnow();db.add(variant)
        # Omitted variants are retained: immutable project identities must survive catalog edits.
        db.flush();audit(db,user,"product_saved",row.id);db.commit();return product_payload(db,row)
    @router.post("/products",status_code=201)
    def create_product(body:ProductBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True)
        return result(request,write_product(db,user,body,Product(tenant_id=user.tenant_id)))
    @router.patch("/products/{identity}")
    def update_product(identity:UUID,body:ProductBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True)
        return result(request,write_product(db,user,body,owned_record(db,Product,identity,user.tenant_id)))

    @router.get("/workspaces")
    def workspaces(request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db)
        rows=db.scalars(select(Workspace).where(Workspace.tenant_id==user.tenant_id).order_by(Workspace.name))
        return result(request,{"items":[{"id":r.id,"name":r.name,"description":r.description} for r in rows if workspace_access(db,user,r.id)]})
    @router.post("/workspaces",status_code=201)
    def create_workspace(body:WorkspaceBody,request:Request,db=Depends(db_session)):
        user,_=owner(request,db,True)
        if entitlements(db,user.tenant_id)["seats"]<5: raise APIError(403,"PARTNER_REQUIRED","고객 작업 공간은 Partner 요금제에서 사용할 수 있습니다.")
        row=Workspace(tenant_id=user.tenant_id,**body.model_dump());db.add(row);db.flush();audit(db,user,"workspace_created",row.id);db.commit()
        return result(request,{"id":row.id,"name":row.name,"description":row.description})

    @router.get("/team")
    def team(request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db)
        primary=list(db.scalars(select(User).where(User.tenant_id==user.tenant_id)))
        members=[{"id":u.id,"name":u.name,"email":u.email,"role":u.role,"is_active":u.is_active,"workspace_ids":workspaces_for(db,u.id,user.tenant_id)} for u in primary]
        for m in db.scalars(select(Membership).where(Membership.tenant_id==user.tenant_id)):
            u=db.get(User,m.user_id);members.append({"id":u.id,"name":u.name,"email":u.email,"role":m.role,"is_active":m.is_active,"workspace_ids":workspaces_for(db,u.id,user.tenant_id)})
        invites=list(db.scalars(select(Invitation).where(Invitation.tenant_id==user.tenant_id,Invitation.accepted_at.is_(None),Invitation.revoked_at.is_(None),Invitation.expires_at>utcnow())))
        data={"members":members,"seat_limit":entitlements(db,user.tenant_id)["seats"],"invitations":[{"id":i.id,"email":i.email,"role":i.role,"expires_at":aware(i.expires_at).isoformat()} for i in invites]};db.commit()
        return result(request,data)
    @router.post("/team/invitations",status_code=201)
    def invite(body:InviteBody,request:Request,db=Depends(db_session)):
        user,_=owner(request,db,True);settings=app.state.settings
        check_seats(db,user.tenant_id)
        email=str(body.email).lower()
        if email==user.email: raise APIError(422,"ALREADY_MEMBER","본인에게 초대할 수 없습니다.")
        pending=db.scalar(select(Invitation).where(Invitation.tenant_id==user.tenant_id,Invitation.email==email,Invitation.accepted_at.is_(None),Invitation.revoked_at.is_(None),Invitation.expires_at>utcnow()))
        if pending: raise APIError(409,"INVITATION_EXISTS","이미 유효한 초대를 보냈습니다.")
        for identity in body.workspace_ids: owned_record(db,Workspace,identity,user.tenant_id)
        token=secrets.token_urlsafe(48)
        row=Invitation(tenant_id=user.tenant_id,email=email,role=body.role,workspace_ids=list(map(str,body.workspace_ids)),invited_by=user.id,token_hash=hash_token(token),expires_at=utcnow()+timedelta(days=7));db.add(row);db.flush()
        audit(db,user,"team_invited",row.id);db.commit()
        return result(request,{"id":row.id,"email":row.email,"role":row.role,"delivery":"manual_share","invitation_url":f"{settings.app_url}/app/team?invite={token}"})
    @router.post("/team/invitations/accept")
    def accept(body:TokenBody,request:Request,db=Depends(db_session)):
        user,session=require_auth(request,db,mutate=True,authorize_write=False,enforce_membership=False)
        if not user.email_verified_at or not user.google_email_authoritative:
            raise APIError(403,"GOOGLE_TEAM_IDENTITY_REQUIRED","팀 초대는 이메일 소유권이 확인된 Gmail 또는 Google Workspace 계정으로 수락해 주세요.")
        row=db.scalar(select(Invitation).where(Invitation.token_hash==hash_token(body.token)))
        if not row or row.email!=user.email or row.accepted_at or row.revoked_at or aware(row.expires_at)<=utcnow(): raise APIError(422,"INVITATION_INVALID","초대 이메일과 유효기간을 확인해 주세요.")
        if db.get(User,user.id).tenant_id==row.tenant_id: raise APIError(409,"ALREADY_MEMBER","이미 이 팀에 속해 있습니다.")
        check_seats(db,row.tenant_id,False,exclude_user=user.id)
        changed=db.execute(update(Invitation).where(Invitation.id==row.id,Invitation.accepted_at.is_(None),Invitation.revoked_at.is_(None),Invitation.expires_at>utcnow()).values(accepted_at=utcnow()).execution_options(synchronize_session=False))
        if changed.rowcount!=1: raise APIError(409,"INVITATION_USED","이미 사용한 초대입니다.")
        membership=db.scalar(select(Membership).where(Membership.tenant_id==row.tenant_id,Membership.user_id==user.id))
        if membership is None: membership=Membership(tenant_id=row.tenant_id,user_id=user.id);db.add(membership)
        membership.role=row.role;membership.is_active=True;assign(db,row.tenant_id,user.id,row.workspace_ids)
        session.active_tenant_id=row.tenant_id;db.add(AuditEvent(tenant_id=row.tenant_id,actor_id=user.id,action="invitation_accepted",entity_id=row.id));db.commit()
        user,session=require_auth(request,db);return result(request,auth_payload(db,user,session))
    @router.patch("/team/members/{identity}")
    def change_member(identity:UUID,body:MemberBody,request:Request,db=Depends(db_session)):
        user,_=owner(request,db,True)
        member=db.scalar(select(Membership).where(Membership.user_id==str(identity),Membership.tenant_id==user.tenant_id))
        if member is None: raise APIError(422,"OWNER_IMMUTABLE","팀 소유자 권한은 이 화면에서 변경할 수 없습니다.")
        if body.is_active and not member.is_active: check_seats(db,user.tenant_id,False,exclude_user=str(identity))
        member.role=body.role;member.is_active=body.is_active;assign(db,user.tenant_id,str(identity),body.workspace_ids);audit(db,user,"member_updated",str(identity));db.commit()
        return result(request,{"updated":True})
    @router.post("/team/switch")
    def switch(body:SwitchBody,request:Request,db=Depends(db_session)):
        user,session=require_auth(request,db,mutate=True,authorize_write=False,enforce_membership=False)
        target=str(body.tenant_id);home=db.get(User,user.id)
        member=db.scalar(select(Membership).where(Membership.user_id==user.id,Membership.tenant_id==target,Membership.is_active.is_(True)))
        if target!=home.tenant_id and member is None: raise APIError(404,"NOT_FOUND","접근할 수 없는 팀입니다.")
        session.active_tenant_id=target;db.flush();user,session=require_auth(request,db);db.commit()
        return result(request,auth_payload(db,user,session))

    @router.post("/projects/{identity}/bindings/preview")
    def preview_bindings(identity:UUID,body:BindingBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True);project=owned_record(db,Project,identity,user.tenant_id);variant=owned_record(db,Variant,body.product_variant_id,user.tenant_id)
        return result(request,{"base_revision":project.base_revision,"changes":binding_changes(db,project,variant)})
    @router.post("/projects/{identity}/bindings/apply")
    def apply_bindings(identity:UUID,body:BindingBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True);project=owned_record(db,Project,identity,user.tenant_id);variant=owned_record(db,Variant,body.product_variant_id,user.tenant_id)
        if body.base_revision!=project.base_revision: raise APIError(409,"REVISION_CONFLICT","문서가 변경되었습니다. 변경 내용을 다시 확인해 주세요.")
        scene=deepcopy(project.scene);changes={c["object_id"]:c for c in binding_changes(db,project,variant)}
        for face in scene["faces"]:
            for obj in face["objects"]:
                if obj["id"] in changes:
                    change=changes[obj["id"]];obj[change["field"]]=change["after"]
        product=db.get(Product,variant.product_id);scene["product_variant_id"]=variant.id;scene["brand_id"]=product.brand_id;scene["confirmed_fields"]=[];scene["reviewed_face_ids"]=[]
        won=db.execute(update(Project).where(Project.id==project.id,Project.base_revision==body.base_revision).values(scene=scene,product_variant_id=variant.id,brand_id=product.brand_id,base_revision=body.base_revision+1,updated_at=utcnow()).execution_options(synchronize_session=False))
        if won.rowcount!=1: raise APIError(409,"REVISION_CONFLICT","문서가 변경되었습니다.")
        db.refresh(project);snapshot_revision(db,project,"bindings_applied");audit(db,user,"bindings_applied",project.id);db.commit();return result(request,project_payload(project))
    @router.post("/projects/{identity}/duplicate",status_code=201)
    def duplicate(identity:UUID,body:DuplicateBody,request:Request,db=Depends(db_session)):
        user,_=require_auth(request,db,mutate=True);source=owned_record(db,Project,identity,user.tenant_id)
        keys=("product_name","brand_name","description","width_mm","height_mm","bottom_mm","depth_mm","template_id","brand_id","product_variant_id","workspace_id","template_version_id","print_profile_version_id","material")
        row=Project(tenant_id=user.tenant_id,created_by=user.id,name=body.name or (source.name[:150]+" 복사"),scene=deepcopy(source.scene),**{key:getattr(source,key) for key in keys});db.add(row);db.flush();snapshot_revision(db,row,"duplicated");audit(db,user,"project_duplicated",row.id);db.commit()
        return result(request,project_payload(row))
    app.include_router(router)
