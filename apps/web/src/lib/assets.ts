import { api } from "./api";
export type UploadedAsset = {
  id: string;
  width_px?: number;
  height_px?: number;
  name?: string;
  source?: string;
};
export async function uploadAsset(
  file: File,
  projectId?: string,
): Promise<UploadedAsset> {
  const config = await api<{
    direct_upload?: boolean;
    upload_max_bytes: number;
    supported_upload_types: string[];
  }>("/config");
  if (file.size > config.upload_max_bytes)
    throw new Error(
      `이미지는 ${(config.upload_max_bytes / 1024 / 1024).toLocaleString("ko-KR")} MiB 이하로 올려 주세요.`,
    );
  if (!config.supported_upload_types.includes(file.type))
    throw new Error("PNG · JPG · WebP 이미지를 선택해 주세요.");
  if (config.direct_upload) {
    const upload = await api<{
      id: string;
      upload_url: string;
      method: "PUT";
      headers: Record<string, string>;
    }>("/assets/uploads", {
      method: "POST",
      body: JSON.stringify({
        name: file.name,
        content_type: file.type,
        byte_size: file.size,
        ...(projectId ? { project_id: projectId } : {}),
      }),
    });
    const result = await fetch(upload.upload_url, {
      method: upload.method,
      headers: upload.headers,
      body: file,
      credentials: "omit",
    });
    if (!result.ok)
      throw new Error(
        "이미지 전송이 완료되지 않았습니다. 연결을 확인하고 다시 올려 주세요.",
      );
    return api<UploadedAsset>(`/assets/uploads/${upload.id}/complete`, {
      method: "POST",
    });
  }
  const body = new FormData();
  body.set("file", file);
  if (projectId) body.set("project_id", projectId);
  return api<UploadedAsset>("/assets", { method: "POST", body });
}
