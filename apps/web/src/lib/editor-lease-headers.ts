const leases = new Map<string, string | null>();
export function setEditorLease(projectId: string, token: string | null) {
  leases.set(projectId, token);
}
export function clearEditorLease(projectId: string) {
  leases.delete(projectId);
}
export function editorLeaseForRequest(
  path: string,
  method?: string,
  body?: BodyInit | null,
): string | null | undefined {
  if (
    !method ||
    method === "GET" ||
    method === "HEAD" ||
    path.includes("/edit-session")
  )
    return;
  let projectId = path.match(/^\/projects\/([^/?]+)/)?.[1];
  if (!projectId && typeof body === "string") {
    try {
      const value = JSON.parse(body);
      if (typeof value.project_id === "string") projectId = value.project_id;
    } catch {}
  }
  if (projectId) return leases.get(projectId);
  if (/^\/(jobs|quotes|exports)(\/|$)/.test(path) && leases.size === 1)
    return [...leases.values()][0];
}
