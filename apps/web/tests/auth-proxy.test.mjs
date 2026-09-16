import assert from "node:assert/strict";
import test from "node:test";
import { authRateHeaders } from "../src/lib/auth-proxy.ts";

const environment = { VERCEL: "1", WORKER_SECRET: "authentication-proxy-test-secret" };
const now = 1700000000000;

test("Vercel identity matches the API signature protocol and ignores caller overrides", () => {
  const result = authRateHeaders(new Headers({
    "x-vercel-forwarded-for": "203.0.113.10",
    "x-forwarded-for": "198.51.100.99",
    "x-phoenix-auth-rate-key": "caller-selected-key",
    "x-phoenix-auth-rate-signature": "caller-selected-signature",
  }), environment, now);
  assert.deepEqual(result, {
    "x-phoenix-auth-rate-key": "b628faa20c8b03286c5f24e60eea14a4dac0d39ba4d570191881640fe024db51",
    "x-phoenix-auth-rate-timestamp": "1700000000",
    "x-phoenix-auth-rate-signature": "ffe2dedd9dcfdeedafea7ea371c73f71cef37ba616875e330a87c8d0d88ae58d",
  });
  assert.ok(!JSON.stringify(result).includes("203.0.113.10"));
});

test("different clients have separate identities and equivalent IPv6 addresses share one", () => {
  const sign = (ip) => authRateHeaders(new Headers({ "x-vercel-forwarded-for": ip }), environment, now);
  assert.notEqual(sign("203.0.113.10")["x-phoenix-auth-rate-key"], sign("203.0.113.11")["x-phoenix-auth-rate-key"]);
  assert.deepEqual(sign("2001:0db8:0:0:0:0:0:1"), sign("2001:db8::1"));
});

test("missing platform address, forwarding lists and missing secret fail closed", () => {
  for (const address of ["", "not-an-ip", "203.0.113.10, 198.51.100.11"]) {
    assert.throws(() => authRateHeaders(new Headers({ "x-vercel-forwarded-for": address }), environment, now));
  }
  assert.throws(() => authRateHeaders(new Headers({ "x-forwarded-for": "203.0.113.10" }), environment, now));
  assert.throws(() => authRateHeaders(new Headers({ "x-vercel-forwarded-for": "203.0.113.10" }), { VERCEL: "1" }, now));
});

test("local development never trusts headers claiming to come from Vercel", () => {
  assert.equal(authRateHeaders(new Headers({ "x-vercel-forwarded-for": "203.0.113.10" }), { WORKER_SECRET: environment.WORKER_SECRET }, now), null);
});
