import assert from "node:assert/strict";
import test from "node:test";
import { paymentOutcome, planSelection, refundOutcome } from "../src/lib/billing-state.ts";

test("only a server-confirmed paid order is a completed payment", () => {
  assert.equal(paymentOutcome({ status: "paid" }).state, "paid");
  for (const status of ["pending", "failed", "reconciliation_required", "refunded", "canceled", "unknown"]) {
    assert.notEqual(paymentOutcome({ status }).state, "paid", status);
  }
});

test("uncertain and rejected approvals preserve the server guidance", () => {
  const pending = paymentOutcome({ status: "reconciliation_required", error: "승인을 조회 중입니다. 중복 결제하지 마세요." });
  assert.equal(pending.state, "pending");
  assert.equal(pending.message, "승인을 조회 중입니다. 중복 결제하지 마세요.");
  const failed = paymentOutcome({ status: "failed", error: "카드 승인이 거절되었습니다." });
  assert.equal(failed.state, "failed");
  assert.equal(failed.message, "카드 승인이 거절되었습니다.");
});

test("expired or canceled subscriptions can subscribe to the same or a different plan", () => {
  for (const status of ["canceled", "past_due", "incomplete"]) {
    const previous = { plan_id: "pro", status };
    assert.equal(planSelection(previous, false, "pro"), "subscribe");
    assert.equal(planSelection(previous, false, "starter"), "subscribe");
  }
  assert.equal(planSelection(null, false, "starter"), "subscribe");
});

test("active subscriptions retain plan changes even when renewal is canceled", () => {
  const subscription = { plan_id: "pro", status: "active", cancel_at_period_end: true };
  assert.equal(planSelection(subscription, true, "pro"), "current");
  assert.equal(planSelection(subscription, true, "starter"), "change");
});

test("only refunded confirms cancellation; reconciliation remains explicitly unconfirmed", () => {
  assert.equal(refundOutcome({ status: "refunded" }).completed, true);
  for (const status of ["paid", "pending", "failed", "reconciliation_required"]) {
    const outcome = refundOutcome({ status, error: "취소 결과를 확인 중입니다." });
    assert.equal(outcome.completed, false);
    assert.match(outcome.message, /아직 확인되지 않았습니다/);
    assert.match(outcome.message, /취소 결과를 확인 중입니다/);
  }
});
