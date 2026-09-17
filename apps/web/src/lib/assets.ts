import { api } from "./api";
export type UploadedAsset = {
  id: string;
  width_px?: number;
  height_px?: number;
  name?: string;
  source?: string;
};
export function assetImageDimensions(assetId: string): Promise<{ width_px: number; height_px: number }> {
  return new Promise((resolve, reject) => {
    const image = new window.Image();
    const finish = () => {
      clearTimeout(timer);
      image.onload = null;
      image.onerror = null;
    };
    const timer = setTimeout(() => {
      finish();
      reject(new Error("이미지 크기를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요."));
    }, 20000);
    image.crossOrigin = "anonymous";
    image.onload = () => {
      finish();
      if (image.naturalWidth > 0 && image.naturalHeight > 0)
        resolve({ width_px: image.naturalWidth, height_px: image.naturalHeight });
      else reject(new Error("이미지의 원본 크기를 확인할 수 없습니다."));
    };
    image.onerror = () => {
      finish();
      reject(new Error("이미지를 불러올 수 없어 맞춤을 적용하지 않았습니다."));
    };
    image.src = `/api/v1/assets/${assetId}/content`;
  });
}
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
    throw new Error("PNG · JPG · WebP 또는 정적 윤곽선 SVG를 선택해 주세요.");
  if (file.type === "image/svg+xml" && file.size > 1024 * 1024)
    throw new Error("SVG 원본은 1MiB 이하로 올려 주세요.");
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
