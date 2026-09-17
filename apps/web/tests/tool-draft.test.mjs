import test from "node:test";
import assert from "node:assert/strict";
import { toolDraftKey, decodeToolDraft, pendingAfterStartFailure } from "../src/lib/tool-draft.ts";
import "fake-indexeddb/auto";
test("uncertain paid requests survive storage, network, login and lease failures", () => {
  assert.equal(pendingAfterStartFailure(false, false), false);
  assert.equal(pendingAfterStartFailure(true, false), true);
  for (const code of [undefined, "network_error", "AUTH_REQUIRED", "FORBIDDEN", "EDITOR_READ_ONLY", "EDIT_LEASE_HELD", "NOT_FOUND", "JOB_CONFLICT"])
    assert.equal(pendingAfterStartFailure(true, true, code), true);
  for (const code of ["QUOTE_EXPIRED", "QUOTE_CHANGED", "REVISION_CONFLICT", "INSUFFICIENT_CREDITS"])
    assert.equal(pendingAfterStartFailure(true, true, code), false);
});
test("OCR resume identity isolates users, workspaces, projects, objects and original assets", () => {
  const scope = ["user", "tenant", "project", "object", "asset"];
  const key = toolDraftKey(...scope);
  for (let i = 0; i < scope.length; i++) {
    const next = [...scope]; next[i] += "-other";
    assert.notEqual(toolDraftKey(...next), key);
  }
});
test("two tabs cannot overwrite a newer OCR draft or clear another tab's pending request", async () => {
  const a = await import("../src/lib/tool-draft.ts?tab=a");
  const b = await import("../src/lib/tool-draft.ts?tab=b");
  const key = `two-tabs-${crypto.randomUUID()}`;
  await a.readToolDraft(key); await b.readToolDraft(key);
  assert.equal(await a.writeToolDraft(key, { text: "A 최신 문구", pendingStart: true, jobKey: "original-operation" }), true);
  assert.equal(await b.writeToolDraft(key, { text: "B 오래된 문구" }), false);
  assert.equal(await b.writeToolDraft(key, null), false);
  const latest = await b.readToolDraft(key);
  assert.equal(latest.value.jobKey, "original-operation");
  assert.equal(await b.writeToolDraft(key, { ...latest.value, jobId: "accepted-job", pendingStart: false }), true);
  assert.equal(await a.writeToolDraft(key, { text: "late response with stale draft" }), false);
  assert.equal((await a.readToolDraft(key)).value.jobId, "accepted-job");
  assert.equal(await a.writeToolDraft(key, null), true);
  assert.equal(await a.readToolDraft(key), undefined);
});
test("same-tab edits serialize in order and storage rejection is reported", async () => {
  const tab = await import("../src/lib/tool-draft.ts?tab=serial");
  const key = `serial-${crypto.randomUUID()}`;
  await tab.readToolDraft(key);
  assert.deepEqual(await Promise.all([tab.writeToolDraft(key, { text: "1" }), tab.writeToolDraft(key, { text: "2" })]), [true, true]);
  assert.equal((await tab.readToolDraft(key)).value.text, "2");
  const original = globalThis.indexedDB;
  try {
    globalThis.indexedDB = { open() { throw new Error("Storage disabled"); } };
    assert.equal(await tab.writeToolDraft(key, { text: "unsaved" }), false);
  } finally { globalThis.indexedDB = original; }
});
test("draft envelope rejects old, future, malformed and unknown versions", () => {
  const now = 1_800_000_000_000;
  const valid = { version: 1, updatedAt: now, value: { sourceText: "37.59", text: "37.5g × 4개입", jobId: "existing-job" } };
  assert.deepEqual(decodeToolDraft(valid, now).value, valid.value);
  for (const invalid of [null, {}, {...valid, version: 2}, {...valid, updatedAt: now - 31 * 86400000},
    {...valid, updatedAt: now + 120000}, {...valid, updatedAt: NaN}, {...valid, value: "bad"}])
    assert.equal(decodeToolDraft(invalid, now), undefined);
});
