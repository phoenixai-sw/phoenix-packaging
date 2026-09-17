import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// Compile the real transport without changing its browser behavior or writing artifacts.
const source = await readFile(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const leaseUrl = new URL("../src/lib/editor-lease-headers.ts", import.meta.url).href;
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
  .replaceAll('"./editor-lease-headers"', JSON.stringify(leaseUrl));
const { api, ApiError, setEditorLease, clearEditorLease } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("transport keeps nested error context, retryability and server request ID", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => Response.json({ code: "EDIT_LEASE_HELD", message: "held", field_errors: { edit_session: { holder: { name: "editor" }, expires_at: "later" } }, retryable: true, request_id: "request-1" }, { status: 423 });
  try {
    await assert.rejects(api("/me"), (error) => error instanceof ApiError && error.status === 423 && error.retryable && error.requestId === "request-1" && error.fields.edit_session.holder.name === "editor");
  } finally { globalThis.fetch = original; }
});

test("typed transport still carries same-origin cookies, session CSRF and project lease", async () => {
  const original = globalThis.fetch;
  let captured;
  globalThis.fetch = async (url, options) => {
    captured = { url, ...options };
    return Response.json({ data: url.endsWith("/me") ? { user: { id: "u" }, csrf_token: "csrf-1" } : { saved: true }, request_id: "request-2" });
  };
  try {
    await api("/me");
    setEditorLease("project-1", "lease-1");
    await api("/projects/project-1/draft", { method: "PATCH", body: JSON.stringify({ base_revision: 1 }) });
    assert.equal(captured.credentials, "same-origin");
    assert.equal(captured.cache, "no-store");
    assert.equal(captured.headers.get("X-CSRF-Token"), "csrf-1");
    assert.equal(captured.headers.get("X-Editor-Lease"), "lease-1");
    assert.equal(captured.headers.get("Content-Type"), "application/json");
  } finally { clearEditorLease("project-1"); globalThis.fetch = original; }
});

test("successful malformed JSON is rejected rather than cast as a domain entity", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => Response.json({ user: {} });
  try { await assert.rejects(api("/me"), (error) => error.code === "INVALID_API_RESPONSE" && error.retryable); }
  finally { globalThis.fetch = original; }
});
