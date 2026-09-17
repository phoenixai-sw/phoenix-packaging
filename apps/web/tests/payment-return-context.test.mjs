import assert from "node:assert/strict";
import test from "node:test";
import { mayConfirmPaymentReturn, paymentReturnPaths, serviceReturnId } from "../src/lib/payment-return-context.ts";

test("a status inquiry before confirmation keeps the provider result usable, while closed orders cannot confirm again", () => {
  for (const status of [undefined, "pending", "failed"])
    assert.equal(mayConfirmPaymentReturn(true, false, status), true);
  for (const status of ["paid", "refunded", "canceled", "reconciliation_required"])
    assert.equal(mayConfirmPaymentReturn(true, false, status), false);
  assert.equal(mayConfirmPaymentReturn(false, false, "pending"), false);
  assert.equal(mayConfirmPaymentReturn(true, true, "pending"), false);
});

const service = "cfd10093-af0d-43a6-87f3-5cb14ade2309";

test("service checkout success and failure preserve the linked request, without provider secrets", () => {
  const payment = paymentReturnPaths("phoenix-test-order", false, service);
  assert.equal(new URL(payment.successPath, "https://local.test").searchParams.get("service_order_id"), service);
  const fail = new URL(payment.failPath, "https://local.test");
  assert.equal(fail.searchParams.get("order_id"), "phoenix-test-order");
  assert.equal(fail.searchParams.get("failed"), "true");
  assert.equal(payment.destination, `/app/services?service_order_id=${service}`);
  assert.doesNotMatch(JSON.stringify(payment), /paymentKey|authKey/);
});

test("first-month recurring authorization returns both billing order and service context", () => {
  const paths = paymentReturnPaths("merchant order/1", true, service);
  const success = new URL(paths.successPath, "https://local.test");
  assert.equal(success.searchParams.get("order_id"), "merchant order/1");
  assert.equal(success.searchParams.get("service_order_id"), service);
  assert.equal(success.pathname, "/app/billing/return");
});

test("return context rejects arbitrary redirects and leaves existing credit checkout intact", () => {
  for (const value of [null, "", "https://external.test/path", "//external.test", "../../admin", `${service}&redirect=bad`]) {
    assert.equal(serviceReturnId(value), undefined);
    const paths = paymentReturnPaths("order", false, value ?? undefined);
    assert.equal(paths.destination, "/app/billing");
    assert.equal(paths.successPath, "/app/billing/return");
  }
});
