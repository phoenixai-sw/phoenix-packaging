import type { Scene } from "@editor/model";
export type Recovery = {
  scene: Scene;
  base_revision: number;
  updated_at: number;
};
async function database() {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open("phoenix-editor-recovery", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("drafts");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
export async function readRecovery(key: string): Promise<Recovery | undefined> {
  try {
    const db = await database();
    return await new Promise((resolve, reject) => {
      const tx = db.transaction("drafts", "readonly");
      const request = tx.objectStore("drafts").get(key);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      tx.oncomplete = () => db.close();
    });
  } catch {
    return undefined;
  }
}
export async function writeRecovery(key: string, value: Recovery | null) {
  try {
    const db = await database();
    const tx = db.transaction("drafts", "readwrite");
    if (value) tx.objectStore("drafts").put(value, key);
    else tx.objectStore("drafts").delete(key);
    tx.oncomplete = () => db.close();
  } catch {
    /* Server save remains authoritative if private browsing disables IndexedDB. */
  }
}
