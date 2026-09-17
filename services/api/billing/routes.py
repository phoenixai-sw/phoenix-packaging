from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from ..auth import require_auth
from ..errors import APIError
from .models import BillingAccount, PaymentOrder, Subscription
from .payments import BillingSettings, ProviderError, billing_account, bind_and_charge, build_provider, cancel_renewal, change_plan, confirm_order, create_order, entitlements, order_payload, reconcile_webhook, refund_order, subscription_payload, sync_order
from .policy import pricing
from .service import create_quote, quote_payload, wallet_summary
from .sync import payment_snapshot


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuoteBody(Body):
    action: str = Field(max_length=80)
    units: int | None = Field(default=None, ge=1, le=10)
    requested_units: int | None = Field(default=None, ge=1, le=10)
    project_id: UUID | None = None
    base_revision: int | None = Field(default=None, ge=1)
    input_data: dict = Field(default_factory=dict)
    prompt: str | None = Field(default=None, max_length=4000)
    face_id: str | None = Field(default=None, max_length=30)
    reference_asset_id: UUID | None = None

    @model_validator(mode="after")
    def normalize_units(self):
        if self.units is None and self.requested_units is None:
            raise ValueError("작업 수량이 필요합니다.")
        if self.units is not None and self.requested_units is not None and self.units != self.requested_units:
            raise ValueError("작업 수량이 일치하지 않습니다.")
        self.units = self.units if self.units is not None else self.requested_units
        return self


class OrderBody(Body):
    kind: Literal["subscription", "topup"]
    plan_id: Literal["starter", "pro", "partner"] | None = None
    credits: Literal[500, 1000] | None = None


class ConfirmBody(Body):
    order_id: str = Field(min_length=6, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    payment_key: str = Field(min_length=1, max_length=200)
    amount: int = Field(ge=0, strict=True)


class BillingKeyBody(Body):
    order_id: str = Field(min_length=6, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    auth_key: str = Field(min_length=1, max_length=300)
    customer_key: str = Field(min_length=2, max_length=300)


class MockBody(Body):
    order_id: str = Field(min_length=6, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ChangePlanBody(Body):
    plan_id: Literal["starter", "pro", "partner"]


class RefundBody(Body):
    reason: str = Field(min_length=3, max_length=200)


def install_billing_routes(app, db_session, *, settings=None, provider=None):
    """Install once, replacing the P1 GET /credits placeholder in main.py."""
    settings = settings or BillingSettings(environment=app.state.settings.environment)
    app.state.billing_settings = settings
    router = APIRouter(prefix="/v1", tags=["billing"])
    current_provider = provider

    def get_provider():
        nonlocal current_provider
        settings.validate()
        if current_provider is None:
            current_provider = build_provider(settings)
        return current_provider

    def result(request, data):
        return {"data": data, "request_id": request.state.request_id}

    def summary(db, order, account=None):
        return {**order_payload(order, account, settings), "payment": payment_snapshot(db, order)}

    def actor(request, db, owner=False, mutate=False):
        user, _ = require_auth(request, db, mutate=mutate)
        if owner and user.role != "owner":
            raise APIError(403, "OWNER_REQUIRED", "결제와 구독은 소유자만 관리할 수 있습니다.")
        return user

    def operation(request):
        supplied = request.headers.get("idempotency-key")
        if not supplied or len(supplied) > 160:
            raise APIError(422, "IDEMPOTENCY_KEY_REQUIRED", "중복 결제 방지를 위한 요청 식별자가 필요합니다.")
        return supplied

    @router.get("/credits")
    def credits(request: Request, db=Depends(db_session)):
        user = actor(request, db)
        summary = wallet_summary(db, user.tenant_id)
        capabilities = settings.capabilities()
        db.commit()
        return result(request, {**summary, "mode": settings.provider, "payments_enabled": capabilities["checkout_available"], "review_export_cost": 0})

    @router.get("/billing")
    def billing(request: Request, db=Depends(db_session)):
        user = actor(request, db, owner=True)
        summary = wallet_summary(db, user.tenant_id)
        access = entitlements(db, user.tenant_id)
        subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == user.tenant_id))
        orders = db.scalars(select(PaymentOrder).where(PaymentOrder.tenant_id == user.tenant_id).order_by(PaymentOrder.created_at.desc()).limit(50)).all()
        account = db.get(BillingAccount, user.tenant_id)
        capabilities = settings.capabilities()
        data = {"policy": pricing(), "summary": summary, "subscription": subscription_payload(subscription), "orders": [{**order_payload(order, account if capabilities["checkout_available"] else None, settings), "payment": payment_snapshot(db, order)} for order in orders], "entitlements": access, "payment_capabilities": capabilities}
        db.commit()
        return result(request, data)

    @router.post("/quotes", status_code=201)
    def quote(body: QuoteBody, request: Request, db=Depends(db_session)):
        user = actor(request, db, mutate=True)
        prepare = getattr(app.state, "prepare_quote", None)
        if prepare is not None:
            values = prepare(db, user, body.model_dump(mode="json", exclude_none=True))
        else:
            if body.action.startswith("export.production"):
                raise APIError(503, "PRODUCTION_PREFLIGHT_UNAVAILABLE", "제작 검수 연결이 준비되지 않았습니다.")
            values = {"action": body.action, "units": body.units, "project_id": str(body.project_id) if body.project_id else None, "base_revision": body.base_revision, "input_data": body.input_data}
        quote = create_quote(db, user.tenant_id, **values)
        db.commit()
        return result(request, quote_payload(quote))

    @router.post("/billing/orders", status_code=201)
    def order(body: OrderBody, request: Request, db=Depends(db_session)):
        user = actor(request, db, owner=True, mutate=True)
        created = create_order(db, user.tenant_id, body.kind, operation(request), plan_id=body.plan_id, credits=body.credits, settings=settings)
        account = billing_account(db, user.tenant_id, settings)
        db.commit()
        return result(request, order_payload(created, account, settings))

    @router.post("/billing/confirm")
    def confirm(body: ConfirmBody, request: Request, db=Depends(db_session)):
        user = actor(request, db, owner=True, mutate=True)
        payment = confirm_order(db, user.tenant_id, body.order_id, body.payment_key, body.amount, provider=get_provider(), settings=settings)
        db.commit()
        return result(request, summary(db, payment))

    @router.post("/billing/billing-key/confirm")
    def confirm_key(body: BillingKeyBody, request: Request, db=Depends(db_session)):
        user = actor(request, db, owner=True, mutate=True)
        try:
            paid = bind_and_charge(db, user.tenant_id, body.order_id, body.auth_key, body.customer_key, provider=get_provider(), settings=settings)
        except ProviderError:
            raise APIError(503, "BILLING_AUTH_FAILED", "결제 수단 등록을 완료하지 못했습니다. 인증부터 다시 진행해 주세요.", retryable=True) from None
        db.commit()
        return result(request, summary(db, paid))

    @router.post("/billing/mock-confirm")
    def mock_confirm(body: MockBody, request: Request, db=Depends(db_session)):
        if not settings.capabilities()["mock_available"]:
            raise APIError(404, "NOT_FOUND", "요청한 항목을 찾을 수 없습니다.")
        user = actor(request, db, owner=True, mutate=True)
        order = db.scalar(select(PaymentOrder).where(PaymentOrder.order_id == body.order_id, PaymentOrder.tenant_id == user.tenant_id))
        if order is None:
            raise APIError(404, "ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.")
        if order.kind == "subscription":
            account = billing_account(db, user.tenant_id, settings)
            paid = bind_and_charge(db, user.tenant_id, order.order_id, "mock-auth", settings.decrypt(account.customer_key_encrypted), provider=get_provider(), settings=settings)
        else:
            paid = confirm_order(db, user.tenant_id, order.order_id, "mock", order.amount, provider=get_provider(), settings=settings)
        db.commit()
        return result(request, summary(db, paid))

    @router.post("/billing/change-plan")
    def switch_plan(body: ChangePlanBody, request: Request, db=Depends(db_session)):
        user = actor(request, db, owner=True, mutate=True)
        change = change_plan(db, user.tenant_id, body.plan_id, operation(request), settings=settings)
        db.commit()
        return result(request, change)

    @router.post("/billing/cancel-renewal")
    def cancel(request: Request, db=Depends(db_session)):
        user = actor(request, db, owner=True, mutate=True)
        subscription = cancel_renewal(db, user.tenant_id)
        db.commit()
        return result(request, subscription_payload(subscription))

    @router.post("/billing/orders/{order_id}/refund")
    def refund(order_id: str, body: RefundBody, request: Request, db=Depends(db_session)):
        user = actor(request, db, owner=True, mutate=True)
        try:
            refunded = refund_order(db, user.tenant_id, order_id, body.reason, provider=get_provider(), settings=settings)
        except ProviderError:
            raise APIError(503, "REFUND_QUERY_UNAVAILABLE", "환불 결과를 확인하고 있습니다. 중복 요청하지 마세요.", retryable=True) from None
        db.commit()
        return result(request, summary(db, refunded))

    @router.post("/billing/orders/{order_id}/sync")
    def sync(order_id: str, request: Request, db=Depends(db_session)):
        user = actor(request, db, owner=True, mutate=True)
        checked = sync_order(db, user.tenant_id, order_id, provider=get_provider(), settings=settings)
        db.commit()
        return result(request, summary(db, checked))

    @router.post("/billing/webhooks/toss")
    async def webhook(request: Request, db=Depends(db_session)):
        # Payload/header data only locates an existing order; it never proves paid.
        if settings.provider not in {"toss_test", "toss_live"}:
            raise APIError(404, "NOT_FOUND", "요청한 항목을 찾을 수 없습니다.")
        body = await request.body()
        if len(body) > 65536:
            raise APIError(413, "WEBHOOK_TOO_LARGE", "결제 알림 크기를 확인해 주세요.")
        try:
            payload = await request.json()
        except ValueError:
            raise APIError(422, "WEBHOOK_INVALID", "결제 알림 형식을 확인해 주세요.") from None
        checked = reconcile_webhook(db, payload, provider=get_provider(), settings=settings, transmission_id=request.headers.get("tosspayments-webhook-transmission-id"))
        db.commit()
        return result(request, {"received": True, "ignored": checked is None, "status": checked.status if checked else None})

    app.include_router(router)
