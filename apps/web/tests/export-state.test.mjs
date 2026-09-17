import assert from "node:assert/strict";
import test from "node:test";
import {
  canRetryReviewExport,
  canRetryExport,
  canRecordPrinterIntake,
  exportKindLabel,
  editableExportBody,
  isExportInProgress,
  exportApprovalNotice,
} from "../src/lib/export-state.ts";

test("only failed review exports expose the existing retry operation", () => {
  assert.equal(
    canRetryReviewExport({ kind: "review_export", status: "failed" }),
    true,
  );
  for (const kind of ["production_export", "ai_generation", undefined]) {
    assert.equal(canRetryReviewExport({ kind, status: "failed" }), false);
  }
  for (const status of [
    "queued",
    "running",
    "succeeded",
    "canceled",
    "reconciliation_required",
  ]) {
    assert.equal(
      canRetryReviewExport({ kind: "review_export", status }),
      false,
    );
  }
});

test("approval withdrawal is separate from file success and uses only the public reason", () => {
  const job = {
    kind: "production_export", status: "succeeded", download_url: "/v1/exports/old/download",
    current_approval: {
      status: "revoked", checked_at: "2026-09-18T12:00:00Z",
      versions: [{ kind: "template", id: "frozen-version", name: "원래 도면", status: "revoked",
        revoked_at: "2026-09-18T11:00:00Z", public_reason: "실링 조건이 변경되어 재확인이 필요합니다.",
        reason: "Internal audit note must never be used" }],
    },
  };
  const notice = exportApprovalNotice(job);
  assert.equal(notice.title, "해당 조건 승인 철회");
  assert.equal(notice.reasons[0].reason, job.current_approval.versions[0].public_reason);
  assert.ok(!JSON.stringify(notice).includes("Internal audit"));
  assert.equal(job.status, "succeeded");
  assert.equal(job.download_url, "/v1/exports/old/download");
  job.current_approval.versions[0].public_reason = null;
  assert.ok(!JSON.stringify(exportApprovalNotice(job)).includes("Internal audit"));
});

test("missing approval does not imply approval and review files never get a manufacturing status", () => {
  assert.equal(exportApprovalNotice({ kind: "production_export" }).warning, true);
  for (const kind of ["review_export", "editable_export", "ai_generation"])
    assert.equal(exportApprovalNotice({ kind, current_approval: { status: "approved", versions: [] } }), null);
  assert.equal(exportApprovalNotice({ kind: "production_export", current_approval: { status: "approved", versions: [] } }).warning, false);
});

test("completed CMYK engine test files are labeled ZIP without relabeling review PDFs or approved production", () => {
  assert.equal(exportKindLabel("review_export", "print_engine_zip"), "CMYK 출력 시험 ZIP");
  assert.equal(exportKindLabel("review_export"), "검토용 PDF");
  assert.equal(exportKindLabel("production_export", "print_engine_zip"), "제작용 번들");
  assert.equal(exportKindLabel("editable_export", "phoenix-editable"), "편집용 프로젝트 ZIP");
});

test("terminal or unknown export states never display an active spinner or poll", () => {
  for (const status of ["queued", "running", "validating"])
    assert.equal(isExportInProgress(status), true);
  for (const status of [
    "canceled",
    "failed",
    "succeeded",
    "reconciliation_required",
    "unknown",
  ]) {
    assert.equal(isExportInProgress(status), false);
  }
});

test("editable ZIP retry never authorizes production retry or manufacturer intake", () => {
  assert.equal(
    canRetryExport({ kind: "editable_export", status: "failed" }),
    true,
  );
  assert.equal(
    canRetryExport({ kind: "review_export", status: "failed" }),
    true,
  );
  for (const status of [
    "queued",
    "running",
    "succeeded",
    "canceled",
    "reconciliation_required",
  ])
    assert.equal(canRetryExport({ kind: "editable_export", status }), false);
  assert.equal(
    canRetryExport({ kind: "production_export", status: "failed" }),
    false,
  );
  assert.equal(
    canRecordPrinterIntake({ kind: "editable_export", status: "succeeded" }),
    false,
  );
  assert.equal(
    canRecordPrinterIntake({ kind: "production_export", status: "succeeded" }),
    true,
  );
  assert.equal(
    canRecordPrinterIntake({ kind: "review_export", status: "running" }),
    false,
  );
  assert.equal(exportKindLabel("editable_export"), "편집용 프로젝트 ZIP");
  assert.notEqual(exportKindLabel("unknown"), "검토용 PDF");
});
test("editable package submits only a saved revision and no manufacturing quote or confirmation", () => {
  assert.deepEqual(editableExportBody("p", 12), {
    project_id: "p",
    base_revision: 12,
    kind: "editable",
  });
  for (const revision of [0, -1, NaN, Infinity, 1.5])
    assert.throws(() => editableExportBody("p", revision));
  assert.throws(() => editableExportBody("", 12));
});
