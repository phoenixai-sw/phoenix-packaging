/** Compiler assertions: these are intentionally not runtime HTTP requests. */
import { apiRequest, apiFileUrl } from "../src/lib/api-contract";

function verifyContract() {
  const session = apiRequest("get", "/v1/me", {});
  session.then((value) => value.user.email.toLowerCase());
  apiRequest("post", "/v1/team/switch", { body: { tenant_id: "id" } });
  apiRequest("get", "/v1/projects/{project_id}", { path: { project_id: "id" } });
  // @ts-expect-error A path identifier is mandatory.
  apiRequest("get", "/v1/projects/{project_id}", {});
  // @ts-expect-error Wrong request field cannot silently reach the server.
  apiRequest("post", "/v1/team/switch", { body: { workspace_id: "id" } });
  // @ts-expect-error This method does not exist.
  apiRequest("delete", "/v1/me", {});
  // @ts-expect-error Nonexistent endpoint is rejected at compile time.
  apiRequest("get", "/v1/invented-endpoint", {});
  // @ts-expect-error Binary downloads must use the file transport, not JSON parsing.
  apiRequest("get", "/v1/assets/{asset_id}/content", { path: { asset_id: "id" } });
  apiFileUrl("/v1/assets/{asset_id}/content", { asset_id: "id" });
}
void verifyContract;
