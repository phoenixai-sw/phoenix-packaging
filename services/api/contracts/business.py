from typing import Literal
from .base import ContractModel


class BarcodeRegistration(ContractModel):
    barcode: str
    holder_name: str
    registration_reference: str
    source_url: str | None
    confirmed_by: str
    confirmed_at: str
    verification: Literal['user_attested']


class BrandData(ContractModel):
    id: str
    name: str
    colors: list[str]
    font_ids: list[str]
    font_asset_ids: list[str]
    logo_asset_id: str | None


class VariantData(ContractModel):
    id: str
    name: str
    sku: str = ''
    barcode: str = ''
    net_weight: str = ''
    net_quantity: float | None = None
    net_unit: Literal['g', 'kg', 'ml', 'l', 'ea'] | None = None
    ingredients: str = ''
    allergens: str = ''
    storage: str = ''
    manufacturer: str = ''
    barcode_registration: BarcodeRegistration | None = None


class ProductData(ContractModel):
    id: str
    name: str
    brand_id: str | None
    description: str
    variants: list[VariantData]


class WorkspaceData(ContractModel):
    id: str
    name: str
    description: str


class TeamMember(ContractModel):
    id: str
    name: str
    email: str
    role: Literal['owner', 'editor', 'viewer']
    is_active: bool
    workspace_ids: list[str]


class TeamInvitation(ContractModel):
    id: str
    email: str
    role: Literal['editor', 'viewer']
    status: Literal['pending']
    expires_at: str


class TeamData(ContractModel):
    members: list[TeamMember]
    seat_limit: int
    invitations: list[TeamInvitation]


class CreatedInvitation(ContractModel):
    id: str
    email: str
    role: Literal['editor', 'viewer']
    delivery: Literal['manual_share']
    invitation_url: str


class RevokedInvitation(ContractModel):
    id: str
    status: Literal['revoked']


class AcceptedInvitation(ContractModel):
    accepted: bool
    tenant_id: str


class MemberUpdated(ContractModel):
    updated: bool


class BindingChange(ContractModel):
    object_id: str
    face_id: str
    field: Literal['text', 'barcode_value']
    binding_key: str
    before: str
    after: str


class BindingPreview(ContractModel):
    base_revision: int
    changes: list[BindingChange]


class VerificationLinks(ContractModel):
    koreannet: str
    verified_by_gs1: str


class VariantReference(ContractModel):
    id: str
    name: str


class VariantBarcodeData(ContractModel):
    variant_id: str
    name: str
    barcode: str
    updated_at: str
    registration: BarcodeRegistration | None
    duplicate_variants: list[VariantReference]
    verification_links: VerificationLinks
    notice: str


class ComposedBarcode(ContractModel):
    value: str
    check_digit: str
    notice: str
    issued: bool
