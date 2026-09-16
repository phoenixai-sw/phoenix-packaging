export type SceneObject = {
  id: string;
  type: "text" | "image" | "shape";
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
  base_revision: number;
  scene: Scene;
  updated_at: string;
};
export const roundMM = (value: number) => Math.round(value * 10000) / 10000;
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
): { scene: Scene; id: string } {
  const id = crypto.randomUUID();
  const face = scene.faces.find((f) => f.id === faceId)!;
  const object: SceneObject = {
    id,
    type: "text",
    face_id: faceId,
    x_mm: 20,
    y_mm: 25,
    width_mm: Math.max(20, face.width_mm - 40),
    height_mm: 25,
    rotation_deg: 0,
    z_index: Math.max(0, ...face.objects.map((o) => o.z_index)) + 1,
    text: "새로운 문구",
    font_size_pt: 24,
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
export function safeWarnings(face: Face) {
  return face.objects.filter((o) => {
    if (o.visible === false || o.print_enabled === false) return false;
    const margin = o.type === "text" ? 15 : -3;
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
        px < margin - 0.0001 ||
        py < margin - 0.0001 ||
        px > face.width_mm - margin + 0.0001 ||
        py > face.height_mm - margin + 0.0001
      );
    });
  });
}
