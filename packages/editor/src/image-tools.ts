import type { Scene, SceneObject } from "./model";

export type ImageRegion = {
  x: number;
  y: number;
  width: number;
  height: number;
};
/** Only source-removal inputs affect a paid cleanup result; replacement text is a later scene edit. */
export function textRemovalInputKey(input: {
  assetId: string;
  region: ImageRegion;
  sourceText: string;
}) {
  return JSON.stringify({
    assetId: input.assetId,
    region: input.region,
    sourceText: input.sourceText,
  });
}
export function validateImageRegion(region: ImageRegion): ImageRegion {
  if (
    !Object.values(region).every(Number.isFinite) ||
    region.x < 0 ||
    region.y < 0 ||
    region.width <= 0 ||
    region.height <= 0 ||
    region.x + region.width > 1.00000001 ||
    region.y + region.height > 1.00000001
  )
    throw new Error(
      "영역은 원본 이미지 안에 있어야 하며, 가로와 세로가 0보다 커야 합니다.",
    );
  return region;
}
export function regionFromPoints(
  a: { x: number; y: number },
  b: { x: number; y: number },
): ImageRegion {
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  return {
    x: Math.min(clamp(a.x), clamp(b.x)),
    y: Math.min(clamp(a.y), clamp(b.y)),
    width: Math.abs(clamp(a.x) - clamp(b.x)),
    height: Math.abs(clamp(a.y) - clamp(b.y)),
  };
}
export function regionPlacement(object: SceneObject, region: ImageRegion) {
  validateImageRegion(region);
  const crop = object.crop || { x: 0, y: 0, width: 1, height: 1 };
  validateImageRegion(crop);
  if (region.x < crop.x - 1e-8 || region.y < crop.y - 1e-8 ||
      region.x + region.width > crop.x + crop.width + 1e-8 ||
      region.y + region.height > crop.y + crop.height + 1e-8)
    throw new Error("글자 영역은 현재 잘라서 표시한 이미지 안에 있어야 합니다. 영역을 줄이거나 자르기를 해제하세요.");
  const angle = (object.rotation_deg * Math.PI) / 180;
  const dx = object.width_mm * (region.x - crop.x) / crop.width,
    dy = object.height_mm * (region.y - crop.y) / crop.height;
  return {
    x_mm: object.x_mm + dx * Math.cos(angle) - dy * Math.sin(angle),
    y_mm: object.y_mm + dx * Math.sin(angle) + dy * Math.cos(angle),
    width_mm: object.width_mm * region.width / crop.width,
    height_mm: object.height_mm * region.height / crop.height,
    rotation_deg: object.rotation_deg,
  };
}
export function imageRegionPixels(
  region: ImageRegion,
  width: number,
  height: number,
) {
  validateImageRegion(region);
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    Math.min(width, height) < 1
  )
    throw new Error("원본 이미지 크기를 확인해 주세요.");
  const left = Math.floor(region.x * width),
    top = Math.floor(region.y * height);
  const right = Math.min(width, Math.ceil((region.x + region.width) * width));
  const bottom = Math.min(
    height,
    Math.ceil((region.y + region.height) * height),
  );
  return { left, top, width: right - left, height: bottom - top };
}
export function assertImageSnapshot(
  scene: Scene,
  expectedScene: string,
  revision: number,
  expectedRevision: number,
) {
  if (revision !== expectedRevision || JSON.stringify(scene) !== expectedScene)
    throw new Error(
      "준비한 뒤 디자인이 변경되었습니다. 원본을 다시 확인하고 새로 준비해 주세요. 결과 이미지는 원본과 별도로 보존됩니다.",
    );
}
export type TextReplacement = {
  text: string;
  font_size_pt: number;
  font_weight: 400 | 700;
  color: string;
};
/** One immutable scene change replaces the asset and adds editable text. No source asset is modified. */
export function applyImageText(
  scene: Scene,
  objectId: string,
  region: ImageRegion,
  replacement: TextReplacement,
  mode: { assetId: string } | { coverColor: string },
  ids: { text: string; cover: string },
): Scene {
  if (
    !Number.isFinite(replacement.font_size_pt) ||
    replacement.font_size_pt < 4 ||
    replacement.font_size_pt > 400 ||
    ![400, 700].includes(replacement.font_weight) ||
    !/^#[\da-f]{6}$/i.test(replacement.color)
  )
    throw new Error("새 문구의 크기·굵기·색상을 확인해 주세요.");
  const face = scene.faces.find((item) =>
    item.objects.some((o) => o.id === objectId),
  );
  const source = face?.objects.find((o) => o.id === objectId);
  if (!face || !source || source.type !== "image" || !source.asset_id)
    throw new Error("원본 이미지 레이어를 다시 선택해 주세요.");
  if (source.locked) throw new Error("잠긴 이미지입니다. 잠금을 해제한 뒤 문구를 수정하세요.");
  const placement = regionPlacement(source, region);
  if (Math.min(placement.width_mm, placement.height_mm) < 0.1)
    throw new Error("선택 영역이 너무 작습니다.");
  const nextObjects = face.objects.map((o) =>
    o.id === source.id && "assetId" in mode
      ? { ...o, asset_id: mode.assetId }
      : { ...o },
  );
  // Normalize order to retain room for the added cover/text without exceeding the schema's z limit.
  nextObjects
    .sort((a, b) => a.z_index - b.z_index)
    .forEach((o, index) => {
      o.z_index = index;
    });
  const base = {
    ...placement,
    face_id: face.id,
    visible: true,
    print_enabled: true,
  };
  if ("coverColor" in mode)
    nextObjects.push({
      ...base,
      id: ids.cover,
      type: "shape",
      shape: "rect",
      fill: mode.coverColor,
      color: mode.coverColor,
      z_index: nextObjects.length,
    });
  if (replacement.text)
    nextObjects.push({
      ...base,
      id: ids.text,
      type: "text",
      text: replacement.text,
      font_id: "NotoSansKR",
      font_size_pt: replacement.font_size_pt,
      font_weight: replacement.font_weight,
      color: replacement.color,
      align: "left",
      line_height: 1.2,
      z_index: nextObjects.length,
    });
  return {
    ...scene,
    faces: scene.faces.map((item) =>
      item.id === face.id ? { ...item, objects: nextObjects } : item,
    ),
  };
}
