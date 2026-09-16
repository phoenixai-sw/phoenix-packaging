export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public fields?: Record<string, string>,
  ) {
    super(message);
  }
}
let csrf = "";
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (csrf && options.method && options.method !== "GET")
    headers.set("X-CSRF-Token", csrf);
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      ...options,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      "network_error",
      "연결이 끊어졌습니다. 인터넷 연결을 확인한 후 다시 시도해 주세요.",
    );
  }
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = json.error || json;
    throw new ApiError(
      res.status,
      err.code || "request_failed",
      err.message || "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
      err.field_errors,
    );
  }
  const data = json.data ?? json;
  if (data.csrf_token) csrf = data.csrf_token;
  return data as T;
}
export type Session = {
  user: {
    id: string;
    name: string;
    email: string;
    role?: "owner" | "editor" | "viewer";
    is_admin?: boolean;
    email_verified?: boolean;
  };
  role?: "owner" | "editor" | "viewer";
  tenant: { id: string; name: string };
  memberships?: Array<{
    id: string;
    name: string;
    role: "owner" | "editor" | "viewer";
  }>;
  workspace_ids?: string[];
  csrf_token: string;
};
export function errorMessage(e: unknown): string {
  return e instanceof Error
    ? e.message
    : "문제가 발생했습니다. 다시 시도해 주세요.";
}
