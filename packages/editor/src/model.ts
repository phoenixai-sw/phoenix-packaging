export type SceneObject = {
  id: string;
  type: "text" | "image" | "shape" | "barcode";
  face_id: string;
  x_mm: number;
  y_mm: number;
  width_mm: number;
  height_mm: number;
  rotation_deg: number;
  z_index: number;
  text?: string;
  font_size_pt?: number;
  font_id?: string;
  color?: string;
  align?: "left" | "center" | "right";
  asset_id?: string;
  visible?: boolean;
  print_enabled?: boolean;
  line_height?: number;
  letter_spacing?: number;
  opacity?: number;
  locked?: boolean;
  fill?: string;
  stroke?: string;
  stroke_width_mm?: number;
  shape?: "rect" | "ellipse" | "circle";
  barcode_value?: string;
  module_mm?: number;
  bar_height_mm?: number;
  barcode_owned?: boolean;
  binding_key?: string;
};
export type Face = {
  id: string;
  name: string;
  width_mm: number;
  height_mm: number;
  background: string;
  objects: SceneObject[];
};
export type Scene = {
  schema_version: string;
  active_face_id: string;
  faces: Face[];
  holes?: Array<{
    id: string;
    face_id: string;
    center_x_mm: number;
    center_y_mm: number;
    diameter_mm: number;
  }>;
  reviewed_face_ids?: string[];
  confirmed_fields?: string[];
  print_profile_version_id?: string;
  [key: string]: unknown;
};
export type Project = {
  id: string;
  name: string;
  product_name: string;
  brand_name: string;
  width_mm: number;
  height_mm: number;
  template_id: string;
  brand_id?: string;
  product_variant_id?: string;
  workspace_id?: string;
  bottom_mm?: number;
  depth_mm?: number;
  geometry?: Record<string, unknown>;
  template_version_id?: string;
  print_profile_version_id?: string;
  material?: string;
  base_revision: number;
  scene: Scene;
  updated_at: string;
};
export const roundMM = (value: number) => Math.round(value * 10000) / 10000;
export type PlacementRegion = Pick<SceneObject, "x_mm" | "y_mm" | "width_mm" | "height_mm">;

export function faceSafeRegion(scene: Scene, face: Face, supplied?: PlacementRegion): PlacementRegion {
  if (supplied) return supplied;
  const margin = scene.template_kind === "folding-box" ? 5 : face.id === "bottom" ? 10 : 15;
  return { x_mm: margin, y_mm: margin, width_mm: face.width_mm - margin * 2, height_mm: face.height_mm - margin * 2 };
}

/** Store only ordinary mm geometry, so canvas, PDF and 3D use the same result. */
export function containImage(region: PlacementRegion, pixelWidth: number, pixelHeight: number) {
  if (![region.x_mm, region.y_mm, region.width_mm, region.height_mm, pixelWidth, pixelHeight].every(Number.isFinite)
      || Math.min(region.width_mm, region.height_mm, pixelWidth, pixelHeight) <= 0)
    throw new Error("이미지 크기와 배치할 면의 크기를 확인해 주세요.");
  const scale = Math.min(region.width_mm / pixelWidth, region.height_mm / pixelHeight);
  const width_mm = roundMM(pixelWidth * scale);
  const height_mm = roundMM(pixelHeight * scale);
  if (Math.min(width_mm, height_mm) < 0.1)
    throw new Error("이미지 비율이 이 면에 배치하기에는 너무 가늘거나 깁니다.");
  return {
    x_mm: roundMM(region.x_mm + (region.width_mm - width_mm) / 2),
    y_mm: roundMM(region.y_mm + (region.height_mm - height_mm) / 2),
    width_mm, height_mm, rotation_deg: 0,
  };
}

export function initialImagePlacement(region: PlacementRegion, pixelWidth: number, pixelHeight: number) {
  const width_mm = Math.min(90, region.width_mm);
  return containImage({ ...region, x_mm: region.x_mm + (region.width_mm - width_mm) / 2, width_mm }, pixelWidth, pixelHeight);
}

export function applyImageBackground(scene: Scene, faceId: string, asset: { id: string; width_px: number; height_px: number }): Scene {
  return {
    ...scene,
    faces: scene.faces.map((face) => {
      if (face.id !== faceId) return face;
      const id = `ai-background-${faceId}`;
      const retained = face.objects.filter((object) => object.id !== id && !(
        object.type === "image" && object.locked && object.x_mm === 0 && object.y_mm === 0 &&
        object.width_mm === face.width_mm && object.height_mm === face.height_mm
      ));
      const layer: SceneObject = {
        id, type: "image", face_id: faceId, asset_id: asset.id,
        ...containImage({ x_mm: 0, y_mm: 0, width_mm: face.width_mm, height_mm: face.height_mm }, asset.width_px, asset.height_px),
        z_index: Math.max(-10000, Math.min(0, ...retained.map((object) => object.z_index)) - 1),
        visible: true, print_enabled: true, locked: true,
      };
      return { ...face, objects: [layer, ...retained] };
    }),
  };
}
export function updateObject(
  scene: Scene,
  id: string,
  changes: Partial<SceneObject>,
): Scene {
  return {
    ...scene,
    faces: scene.faces.map((face) => ({
      ...face,
      objects: face.objects.map((o) =>
        o.id === id ? { ...o, ...changes } : o,
      ),
    })),
  };
}
export function removeObject(scene: Scene, id: string): Scene {
  return {
    ...scene,
    faces: scene.faces.map((face) => ({
      ...face,
      objects: face.objects.filter((o) => o.id !== id),
    })),
  };
}
export function addText(
  scene: Scene,
  faceId: string,
  safeRegion?: PlacementRegion,
): { scene: Scene; id: string } {
  const id = crypto.randomUUID();
  const face = scene.faces.find((f) => f.id === faceId)!;
  const safe = faceSafeRegion(scene, face, safeRegion);
  const insetX = Math.min(5, safe.width_mm * 0.1);
  const insetY = Math.min(10, safe.height_mm * 0.1);
  const width = roundMM(safe.width_mm - insetX * 2);
  const height = roundMM(Math.min(25, safe.height_mm - insetY * 2));
  // Bundled NotoSansKR: this fixed starter text measures 4.824 × font size.
  const fontSize = Math.floor(Math.min(24, width * 72 / 25.4 / 4.824 * 0.95, height * 72 / 25.4 / 1.2 * 0.95) * 2) / 2;
  if (fontSize < 4) throw new Error("이 면의 안전영역에는 기본 문구를 놓을 공간이 부족합니다.");
  const object: SceneObject = {
    id,
    type: "text",
    face_id: faceId,
    x_mm: roundMM(safe.x_mm + insetX),
    y_mm: roundMM(safe.y_mm + insetY),
    width_mm: width,
    height_mm: height,
    rotation_deg: 0,
    z_index: Math.min(10000, Math.max(0, ...face.objects.map((o) => o.z_index)) + 1),
    text: "새로운 문구",
    font_size_pt: fontSize,
    font_id: "NotoSansKR",
    color: "#243829",
    align: "left",
    visible: true,
    print_enabled: true,
    line_height: 1.2,
  };
  return {
    id,
    scene: {
      ...scene,
      faces: scene.faces.map((f) =>
        f.id === faceId ? { ...f, objects: [...f.objects, object] } : f,
      ),
    },
  };
}
export function sendLayerToBack(scene: Scene, id: string): Scene {
  return {
    ...scene,
    faces: scene.faces.map((face) => {
      const sorted = [...face.objects].sort((a, b) => a.z_index - b.z_index);
      const index = sorted.findIndex((object) => object.id === id);
      if (index <= 0) return face;
      const [target] = sorted.splice(index, 1);
      return { ...face, objects: [target, ...sorted].map((object, z_index) => ({ ...object, z_index })) };
    }),
  };
}
export function moveLayer(scene: Scene, id: string, direction: -1 | 1): Scene {
  return {
    ...scene,
    faces: scene.faces.map((face) => {
      const sorted = [...face.objects].sort((a, b) => a.z_index - b.z_index);
      const index = sorted.findIndex((o) => o.id === id);
      if (
        index < 0 ||
        index + direction < 0 ||
        index + direction >= sorted.length
      )
        return face;
      [sorted[index], sorted[index + direction]] = [
        sorted[index + direction],
        sorted[index],
      ];
      return { ...face, objects: sorted.map((o, i) => ({ ...o, z_index: i })) };
    }),
  };
}
export function safeWarnings(
  face: Face,
  safeRegion?: {
    x_mm: number;
    y_mm: number;
    width_mm: number;
    height_mm: number;
  },
) {
  return face.objects.filter((o) => {
    if (o.visible === false || o.print_enabled === false) return false;
    const important = o.type === "text" || o.type === "barcode";
    const margin = important ? 15 : -3;
    const region =
      important && safeRegion
        ? safeRegion
        : {
            x_mm: margin,
            y_mm: margin,
            width_mm: face.width_mm - margin * 2,
            height_mm: face.height_mm - margin * 2,
          };
    const angle = (o.rotation_deg * Math.PI) / 180;
    return [
      [0, 0],
      [o.width_mm, 0],
      [0, o.height_mm],
      [o.width_mm, o.height_mm],
    ].some(([x, y]) => {
      const px = o.x_mm + x * Math.cos(angle) - y * Math.sin(angle);
      const py = o.y_mm + x * Math.sin(angle) + y * Math.cos(angle);
      return (
        px < region.x_mm - 0.0001 ||
        py < region.y_mm - 0.0001 ||
        px > region.x_mm + region.width_mm + 0.0001 ||
        py > region.y_mm + region.height_mm + 0.0001
      );
    });
  });
}
