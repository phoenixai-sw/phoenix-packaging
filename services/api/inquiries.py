"""Public sales inquiries (landing/pricing form) and their admin follow-up list.

The form asks what the business plan's ad funnel needs: package type, monthly change
count, next order date and whether a dieline already exists. No account is required;
submissions are rate limited per signed proxy peer and never echoed back publicly.
"""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import EmailStr, Field
from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from .auth import require_auth, throttle_auth
from .auth_proxy import challenge_rate_identity
from .contracts.base import ContractModel, Envelope, ERROR_RESPONSES
from .database import Base, new_id, utcnow
from .errors import APIError

PACKAGE_TYPES = ("three_side_seal", "stand_up_pouch", "folding_box", "other")
STATUSES = ("new", "contacted", "closed")


class Inquiry(Base):
    __tablename__ = "inquiries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    package_type: Mapped[str] = mapped_column(String(30))
    monthly_changes: Mapped[int] = mapped_column()
    next_order_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    has_dieline: Mapped[bool] = mapped_column()
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="web")
    status: Mapped[str] = mapped_column(String(20), default="new")
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InquiryBody(ContractModel):
    company: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    package_type: Literal["three_side_seal", "stand_up_pouch", "folding_box", "other"]
    monthly_changes: int = Field(ge=0, le=1000)
    next_order_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    has_dieline: bool
    message: str | None = Field(default=None, max_length=4000)
    source: str = Field(default="web", max_length=40)
    consent: Literal[True]


class InquiryReceipt(ContractModel):
    id: str
    received_at: str


class InquiryData(InquiryReceipt):
    company: str
    name: str
    email: str
    phone: str | None
    package_type: str
    monthly_changes: int
    next_order_date: str | None
    has_dieline: bool
    message: str | None
    source: str
    status: Literal["new", "contacted", "closed"]
    note: str | None
    updated_at: str


class InquiryList(ContractModel):
    items: list[InquiryData]


class InquiryUpdate(ContractModel):
    status: Literal["new", "contacted", "closed"]
    note: str | None = Field(default=None, max_length=1000)


def _payload(row):
    return {"id": row.id, "received_at": row.created_at.isoformat(), "company": row.company, "name": row.name, "email": row.email,
            "phone": row.phone, "package_type": row.package_type, "monthly_changes": row.monthly_changes, "next_order_date": row.next_order_date,
            "has_dieline": row.has_dieline, "message": row.message, "source": row.source, "status": row.status, "note": row.note,
            "updated_at": row.updated_at.isoformat()}


def install_inquiry_routes(app, db_session):
    router = APIRouter(prefix="/v1", tags=["inquiries"])

    def result(request, data):
        return {"data": data, "request_id": request.state.request_id}

    def admin(request, db, mutate=False):
        user, _ = require_auth(request, db, mutate=mutate, authorize_write=False, enforce_membership=False)
        if not user.is_admin:
            raise APIError(403, "ADMIN_REQUIRED", "운영 관리자 권한이 필요합니다.")
        return user

    @router.post("/inquiries", status_code=201, response_model=Envelope[InquiryReceipt], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def submit(body: InquiryBody, request: Request, db=Depends(db_session)):
        throttle_auth(db, "inquiry:" + challenge_rate_identity(request, app.state.settings), max_attempts=10)
        row = Inquiry(**{key: value for key, value in body.model_dump().items() if key != "consent"})
        db.add(row)
        db.commit()
        return result(request, {"id": row.id, "received_at": row.created_at.isoformat()})

    @router.get("/admin/inquiries", response_model=Envelope[InquiryList], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def listing(request: Request, db=Depends(db_session)):
        admin(request, db)
        rows = db.scalars(select(Inquiry).order_by(Inquiry.created_at.desc()).limit(200)).all()
        return result(request, {"items": [_payload(row) for row in rows]})

    @router.patch("/admin/inquiries/{inquiry_id}", response_model=Envelope[InquiryData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def update(inquiry_id: str, body: InquiryUpdate, request: Request, db=Depends(db_session)):
        admin(request, db, mutate=True)
        row = db.get(Inquiry, inquiry_id)
        if row is None:
            raise APIError(404, "NOT_FOUND", "문의를 찾을 수 없습니다.")
        row.status = body.status
        row.note = body.note
        row.updated_at = utcnow()
        db.commit()
        return result(request, _payload(row))

    app.include_router(router)
