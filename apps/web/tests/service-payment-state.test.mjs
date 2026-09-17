import assert from "node:assert/strict";
import test from "node:test";
import { recurringConsent, servicePaymentLabel, servicePaymentMatchesQuote, servicePaymentWindowOpen, serviceRefundDecision, serviceWorkPaymentReady } from "../src/lib/service-payment-state.ts";

const terms = { terms_version: "immutable-terms-1", amount_inc_vat: 99000, renewal_amount_inc_vat: 108900, currency: "KRW", automatic_renewal: true, plan_id: "pro" };

test("service order labels never promote an inquiry, pending, or reconciliating order to paid", () => {
  assert.equal(servicePaymentLabel("paid"), "결제 승인 확인");
  for (const status of ["not_collected", "pending", "failed", "canceled", "refunded", "reconciliation_required", "unknown"])
    assert.notEqual(servicePaymentLabel(status), "결제 승인 확인", status);
});

test("recurring payment requires separate consent to the server's exact immutable first and renewal amounts", () => {
  assert.throws(() => recurringConsent(terms, false), /자동 갱신/);
  assert.deepEqual(recurringConsent(terms, true), {
    terms_version: "immutable-terms-1", first_amount_inc_vat: 99000, renewal_amount_inc_vat: 108900,
    currency: "KRW", automatic_renewal: true, plan_id: "pro",
  });
  assert.equal(recurringConsent({ ...terms, automatic_renewal: false }, false), undefined);
  assert.equal(recurringConsent({ ...terms, renewal_amount_inc_vat: 119900 }, true).renewal_amount_inc_vat, 119900);
  for (const patch of [{ renewal_amount_inc_vat: null }, { renewal_amount_inc_vat: NaN }, { amount_inc_vat: 0 }, { plan_id: "partner" }, { terms_version: "" }, { currency: "USD" }])
    assert.throws(() => recurringConsent({ ...terms, ...patch }, true), /결제 조건/);
});

test("service refund is denied after work begins and while payment remains uncertain", () => {
  for (const status of ["in_progress", "delivered", "completed"])
    assert.equal(serviceRefundDecision({ serviceStatus: status, paymentStatus: "paid", allowed: true }).allowed, false);
  for (const paymentStatus of ["pending", "reconciliation_required", "refunded", "failed"])
    assert.equal(serviceRefundDecision({ serviceStatus: "accepted", paymentStatus, allowed: true }).allowed, false);
  assert.equal(serviceRefundDecision({ serviceStatus: "accepted", paymentStatus: "paid", allowed: true }).allowed, true);
  assert.equal(serviceRefundDecision({ serviceCode: "pilot_pro_first_month", serviceStatus: "completed", paymentStatus: "paid", allowed: true }).allowed, true);
  const blocked = serviceRefundDecision({ serviceStatus: "accepted", paymentStatus: "paid", allowed: false, blockedReason: "일부 크레딧을 사용했습니다." });
  assert.equal(blocked.allowed, false);
  assert.equal(blocked.message, "일부 크레딧을 사용했습니다.");
});

test("checkout validates the returned payment order against the accepted service quote before opening Toss", () => {
  const order = { amount: 55000, currency: "KRW", service_order_id: "service-1", service_quote_id: "quote-1" };
  assert.equal(servicePaymentMatchesQuote(order, "service-1", "quote-1", 55000), true);
  for (const patch of [{ amount: 56000 }, { currency: "USD" }, { service_order_id: "service-2" }, { service_quote_id: "quote-2" }, { service_quote_id: undefined }])
    assert.equal(servicePaymentMatchesQuote({ ...order, ...patch }, "service-1", "quote-1", 55000), false);
});

test("work transitions require payment for paid offers and Pro, while zero and legacy service quotes remain usable", () => {
  const input = { serviceCode: "file_review", paymentStatus: "not_collected", hasCheckoutTerms: true, amount: 55000 };
  assert.equal(serviceWorkPaymentReady(input), false);
  assert.equal(serviceWorkPaymentReady({ ...input, paymentStatus: "paid" }), true);
  assert.equal(serviceWorkPaymentReady({ ...input, amount: 0 }), true);
  assert.equal(serviceWorkPaymentReady({ ...input, hasCheckoutTerms: false }), true);
  assert.equal(serviceWorkPaymentReady({ ...input, serviceCode: "pilot_pro_first_month", hasCheckoutTerms: false }), false);
  for (const paymentStatus of ["refunded", "pending", "reconciliation_required", "failed"])
    assert.equal(serviceWorkPaymentReady({ ...input, paymentStatus }), false);
});

test("same-order retry requires an unexpired pending or failed payment, including the moment the checkout is opened", () => {
  const now = Date.parse("2026-09-18T10:00:00Z");
  for (const status of ["pending", "failed"])
    assert.equal(servicePaymentWindowOpen({ status, expires_at: "2026-09-18T10:00:01Z" }, now), true);
  for (const expires_at of ["2026-09-18T10:00:00Z", "2026-09-18T09:59:59Z", "invalid"])
    assert.equal(servicePaymentWindowOpen({ status: "pending", expires_at }, now), false);
  for (const status of ["paid", "refunded", "canceled", "reconciliation_required"])
    assert.equal(servicePaymentWindowOpen({ status, expires_at: "2026-09-18T10:00:01Z" }, now), false);
});
