import assert from "node:assert/strict";
import test from "node:test";
import { intakeRecordLabel, intakeStatusLabel, INTAKE_NOTICE } from "../src/lib/export-intake-state.ts";
import { canRetryExport, exportAvailabilityNotice, exportStatusLabel } from "../src/lib/export-state.ts";

test("a successful output does not establish manufacturer acceptance and technical rejection stays visible", () => {
  assert.equal(intakeStatusLabel(null), "기록 없음");
  assert.equal(intakeStatusLabel({ status: null, record_count: 0 }), "기록 없음");
  assert.equal(intakeStatusLabel({ status: "accepted", technical_rejected: false }), "입고 수락 기록");
  assert.equal(intakeStatusLabel({ status: "accepted", technical_rejected: true }), "기술 반려 이력 있음");
  assert.match(INTAKE_NOTICE, /직접 기록/);
  assert.match(INTAKE_NOTICE, /파일 생성 상태와 별개/);
});

test("only matching source and verification labels can represent self-recorded manufacturer replies", () => {
  const accepted = { status: "accepted", rejection_kind: null };
  assert.equal(intakeRecordLabel({ ...accepted, record_source: "manufacturer", verification: "self_reported" }).source, "제조사 회신 · 사용자 기록");
  assert.equal(intakeRecordLabel({ ...accepted, record_source: "test", verification: "test_record" }).source, "내부 시험 기록");
  for (const pair of [
    ["legacy", "unclassified"], ["manufacturer", "unclassified"],
    ["test", "self_reported"], ["manufacturer", "test_record"],
  ]) assert.equal(intakeRecordLabel({ ...accepted, record_source: pair[0], verification: pair[1] }).source, "출처 미확인 기록");
  assert.equal(intakeRecordLabel({ status: "rejected", rejection_kind: "aesthetic" }).status, "디자인 변경 요청");
  assert.equal(intakeRecordLabel({ status: "rejected", rejection_kind: "technical" }).status, "기술 반려");
});

test("confirmed loss, compensation and retention are distinct from transient storage uncertainty", () => {
  const original = { kind: "production_export", status: "succeeded", result: { manufacturer_intake_status: "not_submitted" } };
  const value = { ...original, availability: { status: "compensated", message: "차감 복원", credit_restored: 50, next_check_at: "later", retry_allowed: false } };
  assert.equal(exportAvailabilityNotice(value).creditRestored, 50);
  assert.equal(exportAvailabilityNotice(value).nextCheckAt, null);
  assert.equal(canRetryExport(value), false);
  assert.deepEqual(original.result, { manufacturer_intake_status: "not_submitted" });
  const free = { kind: "review_export", status: "failed", availability: { ...value.availability, status: "unavailable", credit_restored: 0, retry_allowed: true } };
  assert.equal(canRetryExport(free), true);
  assert.equal(canRetryExport({ ...free, availability: { ...free.availability, status: "deleted", retry_allowed: false } }), false);
  const uncertain = exportAvailabilityNotice({ ...original, availability: { ...value.availability, status: "temporarily_unverified", credit_restored: 0 } });
  assert.equal(uncertain.title, "저장소 연결 확인 필요");
  assert.equal(uncertain.nextCheckAt, "later");
  assert.equal(exportStatusLabel("unavailable"), "파일 사용 불가");
  assert.equal(exportAvailabilityNotice({ ...original, availability: { status: "available" } }), null);
});
