import type { paths, components } from "../../../../packages/contracts/api.generated";
import { api } from "./api";

export type ApiSchema<K extends keyof components["schemas"]> = components["schemas"][K];
type Method = "get" | "post" | "put" | "patch" | "delete";
type Operation<P extends keyof paths, M extends Method> = M extends keyof paths[P] ? NonNullable<paths[P][M]> : never;
type Success<O> = O extends { responses: infer R }
  ? R[keyof R & (200 | 201 | 202)] : never;
type JsonContent<S> = S extends { content: { "application/json": infer J } } ? J : never;
export type ApiData<P extends keyof paths, M extends Method = "get"> =
  JsonContent<Success<Operation<P, M>>> extends { data: infer D } ? D : never;
type JsonPath<M extends Method> = { [P in keyof paths]:
  [Operation<P, M>] extends [never] ? never :
  [JsonContent<Success<Operation<P, M>>>] extends [never] ? never : P
}[keyof paths];
type Params<O> = O extends { parameters: infer P } ? P : never;
type PathParams<O> = Params<O> extends { path: infer P } ? { path: P } : { path?: never };
type QueryParams<O> = Params<O> extends { query?: infer Q } ? { query?: Q } : { query?: never };
type RequestContent<O> = O extends { requestBody?: { content: infer C } } ? C : never;
type BodyValue<O> = RequestContent<O> extends { "application/json": infer B } ? B :
  RequestContent<O> extends { "multipart/form-data": unknown } ? FormData : never;
type BodyOptions<O> = O extends { requestBody: unknown } ? { body: BodyValue<O> } :
  [BodyValue<O>] extends [never] ? { body?: never } : { body?: BodyValue<O> };
type Options<O> = Omit<RequestInit, "method" | "body"> & PathParams<O> & QueryParams<O> & BodyOptions<O>;

/** Existing cookie/CSRF/lease transport, with method/path/body/result inferred from OpenAPI. */
export function apiRequest<M extends Method, P extends JsonPath<M>>(
  method: M,
  path: P,
  options: Options<Operation<P, M>>,
): Promise<ApiData<P, M>> {
  const { path: parameters, query, body, ...request } = options;
  const resolved = resolveApiPath(path, parameters, query);
  return api<ApiData<P, M>>(resolved.slice("/v1".length), {
    ...request,
    method: method.toUpperCase(),
    ...(body === undefined ? {} : { body: body instanceof FormData ? body : JSON.stringify(body) }),
  });
}

/** Binary files may redirect to a short-lived private URL and never use JSON envelopes. */
type DownloadPath = { [P in keyof paths]:
  [Operation<P, "get">] extends [never] ? never :
  [JsonContent<Success<Operation<P, "get">>>] extends [never] ? P : never
}[keyof paths];
export function apiFileUrl<P extends DownloadPath>(path: P, parameters: PathParams<Operation<P, "get">>["path"]) {
  return `/api${resolveApiPath(path, parameters)}`;
}

export function resolveApiPath(path: string, parameters?: unknown, query?: unknown): string {
  const values = parameters && typeof parameters === "object" ? parameters as Record<string, unknown> : undefined;
  let resolved = path.replace(/\{([^}]+)\}/g, (_, key: string) => {
    const value = values?.[key];
    if (typeof value !== "string" && typeof value !== "number") throw new Error(`Missing API path parameter: ${key}`);
    return encodeURIComponent(String(value));
  });
  if (query && typeof query === "object") {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) continue;
      for (const item of Array.isArray(value) ? value : [value]) search.append(key, String(item));
    }
    if (search.size) resolved += `?${search}`;
  }
  return resolved;
}
