import assert from "node:assert/strict";
import test from "node:test";
import { canRetryReviewExport, isExportInProgress } from "../src/lib/export-state.ts";

test("only failed review exports expose the existing retry operation", () => {
  assert.equal(canRetryReviewExport({ kind: "review_export", status: "failed" }), true);
  for (const kind of ["production_export", "ai_generation", undefined]) {
    assert.equal(canRetryReviewExport({ kind, status: "failed" }), false);
  }
  for (const status of ["queued", "running", "succeeded", "canceled", "reconciliation_required"]) {
    assert.equal(canRetryReviewExport({ kind: "review_export", status }), false);
  }
});

test("terminal or unknown export states never display an active spinner or poll", () => {
  for (const status of ["queued", "running", "validating"]) assert.equal(isExportInProgress(status), true);
  for (const status of ["canceled", "failed", "succeeded", "reconciliation_required", "unknown"]) {
    assert.equal(isExportInProgress(status), false);
  }
});
