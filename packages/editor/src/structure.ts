export type StructuralRegion = { x_mm: number; y_mm: number; width_mm: number; height_mm: number };
export type StructuralLine = { x1_mm: number; y1_mm: number; x2_mm: number; y2_mm: number };
export type FaceStructure = {
  id: string;
  regions?: {
    safe?: StructuralRegion;
    cut?: StructuralRegion;
    no_print?: StructuralRegion[];
    fold?: StructuralLine[];
    hole_allowed?: StructuralRegion | null;
    header?: StructuralRegion;
    zipper?: { line: StructuralLine; band: StructuralRegion } | null;
    tear_line?: StructuralLine | null;
    tear_notches?: Array<{ side: string; shape?: "round" | "v"; points_mm: number[][] }>;
    cut_contour?: { points_mm: number[][]; closed: boolean };
  };
};
export const defaultPouchFeatures = {
  header_height_mm: 30, zipper_enabled: true, zipper_y_mm: 35, zipper_band_mm: 6,
  tear_enabled: true, tear_y_mm: 24, notch_depth_mm: 3, notch_height_mm: 4,
  notch_shape: "round" as const,
};
export function hangerPreset(faceWidth: number, regions?: FaceStructure["regions"], diameter = 6) {
  const allowed = regions?.hole_allowed;
  if (!allowed || allowed.width_mm < diameter || allowed.height_mm < diameter) return null;
  const radius = diameter / 2;
  const centerY = regions?.header
    ? regions.header.y_mm + regions.header.height_mm / 2
    : allowed.y_mm + allowed.height_mm / 2;
  return {
    center_x_mm: Math.max(allowed.x_mm + radius, Math.min(faceWidth / 2, allowed.x_mm + allowed.width_mm - radius)),
    center_y_mm: Math.max(allowed.y_mm + radius, Math.min(centerY, allowed.y_mm + allowed.height_mm - radius)),
    diameter_mm: diameter,
  };
}
export function linePoints(line: StructuralLine, scale = 1) {
  return [line.x1_mm, line.y1_mm, line.x2_mm, line.y2_mm].map((value) => value * scale);
}
export function contourPoints(points: number[][], scale = 1) {
  return points.flatMap(([x, y]) => [x * scale, y * scale]);
}
