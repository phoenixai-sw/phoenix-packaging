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
  // Keep existing z values (locked layers must stay byte-identical); renormalize only when the
  // schema's z limit would be exceeded by the two added layers.
  const topZ = Math.max(0, ...nextObjects.map((o) => o.z_index));
  let nextZ = topZ + 1;
  if (topZ + 2 > 10000) {
    nextObjects
      .sort((a, b) => a.z_index - b.z_index)
      .forEach((o, index) => {
        o.z_index = index;
      });
    nextZ = nextObjects.length;
  }
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
      z_index: nextZ++,
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
      z_index: nextZ++,
    });
  return {
    ...scene,
    faces: scene.faces.map((item) =>
      item.id === face.id ? { ...item, objects: nextObjects } : item,
    ),
  };
}

/** A text line found by browser OCR, in source-normalized coordinates like ImageRegion. */
export type DetectedTextLine = {
  id: string;
  text: string;
  confidence: number;
  region: ImageRegion;
};
/** Convert an OCR pixel box to a padded source-normalized region clamped to `bounds` (the crop or whole image). */
export function detectedLineRegion(
  box: { x0: number; y0: number; x1: number; y1: number },
  width: number,
  height: number,
  bounds: ImageRegion = { x: 0, y: 0, width: 1, height: 1 },
  paddingRatio = 0.25,
): ImageRegion {
  if (!(width > 0) || !(height > 0)) throw new Error("원본 이미지 크기를 확인하지 못했습니다.");
  const boxHeight = Math.max(1, box.y1 - box.y0);
  const pad = boxHeight * paddingRatio;
  const left = Math.max(bounds.x, (box.x0 - pad) / width);
  const top = Math.max(bounds.y, (box.y0 - pad) / height);
  const right = Math.min(bounds.x + bounds.width, (box.x1 + pad) / width);
  const bottom = Math.min(bounds.y + bounds.height, (box.y1 + pad) / height);
  const round = (v: number) => Math.round(v * 1e6) / 1e6;
  return validateImageRegion({
    x: round(left),
    y: round(top),
    width: round(Math.max(right - left, 1 / width)),
    height: round(Math.max(bottom - top, 1 / height)),
  });
}
/** Font size that roughly refills a line box: Korean glyphs fill about 90% of the em box, and the box carries padding. */
export function estimateFontSizePt(lineHeightMm: number, paddingRatio = 0.25): number {
  const glyphMm = lineHeightMm / (1 + 2 * paddingRatio);
  const pt = (glyphMm / 0.9) / 0.3528;
  return Math.min(400, Math.max(4, Math.round(pt * 2) / 2));
}
/** Shrink the height-based estimate until the wording fits the box's width too.
 *
 *  `estimateFontSizePt` only knows how tall the replaced line was. Replacement wording is often
 *  longer than the original, and the editor's font is not the one baked into the picture, so the
 *  height-only size wraps the new text onto a second line inside a one-line box. `measureWidthMm`
 *  is injected so this stays a pure function: the browser passes a canvas measurement of the real
 *  font, tests pass a stub. */
export function fitFontSizePt(
  text: string,
  widthMm: number,
  heightMm: number,
  measureWidthMm: (text: string, sizePt: number) => number,
  { minPt = 4, paddingRatio = 0.25 }: { minPt?: number; paddingRatio?: number } = {},
): number {
  const round = (pt: number) => Math.min(400, Math.max(minPt, Math.round(pt * 2) / 2));
  let size = estimateFontSizePt(heightMm, paddingRatio);
  const line = text.trim();
  if (!line || !(widthMm > 0)) return size;
  // Width scales with size, so one ratio lands close; a few passes absorb the rounding and any
  // font whose advances do not scale perfectly linearly.
  for (let pass = 0; pass < 6; pass += 1) {
    const width = measureWidthMm(line, size);
    if (!(width > widthMm)) return size;
    const next = round(size * (widthMm / width));
    if (next >= size) { size = round(size - 0.5); } else { size = next; }
    if (size <= minPt) return minPt;
  }
  return size;
}
/** Split region pixels into dark/light clusters: the darker cluster is the letter colour, the lighter the background cover. */
export function sampleTextColors(rgba: ArrayLike<number>): { text: string; cover: string } {
  const pixels: Array<[number, number, number, number]> = [];
  for (let i = 0; i + 3 < rgba.length; i += 4) {
    if (rgba[i + 3] < 128) continue;
    const r = rgba[i], g = rgba[i + 1], b = rgba[i + 2];
    pixels.push([r, g, b, 0.2126 * r + 0.7152 * g + 0.0722 * b]);
  }
  if (!pixels.length) return { text: "#172d26", cover: "#fff3de" };
  pixels.sort((a, b) => a[3] - b[3]);
  const mean = (slice: Array<[number, number, number, number]>) => {
    const sum = slice.reduce((acc, p) => [acc[0] + p[0], acc[1] + p[1], acc[2] + p[2]], [0, 0, 0]);
    return sum.map((v) => Math.round(v / slice.length));
  };
  const hex = (c: number[]) => "#" + c.map((v) => v.toString(16).padStart(2, "0")).join("");
  // Two-tone split at the luminance midpoint; the majority side is the background cover.
  const threshold = (pixels[0][3] + pixels[pixels.length - 1][3]) / 2;
  const split = pixels.findIndex((p) => p[3] > threshold);
  const darkSlice = pixels.slice(0, split <= 0 ? Math.max(1, Math.round(pixels.length * 0.15)) : split);
  const lightSlice = pixels.slice(split <= 0 ? pixels.length - Math.max(1, Math.round(pixels.length * 0.4)) : split);
  const dark = mean(darkSlice), light = mean(lightSlice.length ? lightSlice : darkSlice);
  const darkMajority = darkSlice.length > lightSlice.length;
  return darkMajority ? { text: hex(light), cover: hex(dark) } : { text: hex(dark), cover: hex(light) };
}
