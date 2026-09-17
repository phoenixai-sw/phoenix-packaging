import assert from "node:assert/strict";
import test from "node:test";
import {
  canRetryReviewExport,
  canRetryExport,
  canRecordPrinterIntake,
  exportKindLabel,
  editableExportBody,
  isExportInProgress,
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
