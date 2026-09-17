/** Device-local work in progress, scoped to the signed-in workspace and source asset. */
export type ToolDraft<T> = { version: 1; updatedAt: number; revision?: string; value: T };
const lifetime = 30 * 24 * 60 * 60 * 1000;
const queues = new Map<string, Promise<boolean>>();
const observed = new Map<string, string | null>();
export function pendingAfterStartFailure(wasPending: boolean, sent: boolean, code?: string) {
  if (!sent) return wasPending;
  return !["QUOTE_EXPIRED", "QUOTE_CHANGED", "REVISION_CONFLICT", "INSUFFICIENT_CREDITS"].includes(code || "");
}
export function toolDraftKey(userId: string, tenantId: string, projectId: string, objectId: string, assetId: string) {
  return ["image-text-v1", userId, tenantId, projectId, objectId, assetId].join(":");
}
export function decodeToolDraft<T>(raw: unknown, now = Date.now()): ToolDraft<T> | undefined {
  if (!raw || typeof raw !== "object") return;
  const row = raw as Partial<ToolDraft<T>>;
  if (row.version !== 1 || typeof row.updatedAt !== "number" || !Number.isFinite(row.updatedAt) ||
      row.updatedAt > now + 60_000 || now - row.updatedAt > lifetime || !row.value || typeof row.value !== "object") return;
  return row as ToolDraft<T>;
}
function openDatabase() {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open("phoenix-tool-drafts", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("drafts");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
export async function readToolDraft<T>(key: string): Promise<ToolDraft<T> | undefined> {
  try {
    await queues.get(key);
    const db = await openDatabase();
    return await new Promise((resolve, reject) => {
      const tx = db.transaction("drafts", "readonly");
      const request = tx.objectStore("drafts").get(key);
      request.onsuccess = () => {
        observed.set(key, request.result?.revision || null);
        resolve(decodeToolDraft<T>(request.result));
      };
      request.onerror = () => reject(request.error);
      tx.oncomplete = () => db.close();
      tx.onabort = () => { db.close(); reject(tx.error); };
    });
  } catch { return undefined; }
}
export function writeToolDraft<T>(key: string, value: T | null): Promise<boolean> {
  const previous = queues.get(key) || Promise.resolve(true);
  const next = previous.then(async () => {
    try {
      const db = await openDatabase();
      return await new Promise<boolean>((resolve) => {
        const tx = db.transaction("drafts", "readwrite");
        const store = tx.objectStore("drafts");
        const prior = store.get(key);
        let accepted = false;
        const revision = value === null ? null : crypto.randomUUID();
        prior.onsuccess = () => {
          // A second tab must read the latest draft before replacing it.
          if ((prior.result?.revision || null) !== (observed.get(key) || null)) return;
          if (value === null) store.delete(key);
          else store.put({ version: 1, updatedAt: Date.now(), revision, value }, key);
          accepted = true;
        };
        tx.oncomplete = () => { if (accepted) observed.set(key, revision); db.close(); resolve(accepted); };
        tx.onabort = tx.onerror = () => { db.close(); resolve(false); };
      });
    } catch { return false; }
  });
  queues.set(key, next);
  void next.finally(() => { if (queues.get(key) === next) queues.delete(key); });
  return next;
}
