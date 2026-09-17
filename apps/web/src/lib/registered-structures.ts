import type { StructureDefinitionV2 } from "../../../../packages/contracts/structure-definition.generated";

export type StructureFamily =
  "three-side-seal" | "stand-up-pouch" | "folding-box";
export type StructureInputs = {
  width_mm: number;
  height_mm: number;
  bottom_mm?: number;
  depth_mm?: number;
};
export type StructureDefinition = StructureDefinitionV2 & {
  schema_version: "2.0";
  family: StructureFamily;
};
export type RegisteredStructure = {
  id: string;
  name: string;
  manufacturer?: string;
  is_demo: boolean;
  family: StructureFamily;
  recipe_id: string;
  definition: StructureDefinition;
  review_only: true;
  production_enabled: false;
};
export type StructureGeometry = {
  template_id: string;
  net_width_mm: number;
  net_height_mm: number;
  faces: Array<{
    id: string;
    name: string;
    width_mm: number;
    height_mm: number;
    net: { x_mm: number; y_mm: number; rotation_deg?: number };
    regions: {
      safe: { x_mm: number; y_mm: number; width_mm: number; height_mm: number };
    };
  }>;
  structural_parts?: Array<{
    id: string;
    x_mm: number;
    y_mm: number;
    width_mm: number;
    height_mm: number;
  }>;
  assumptions?: string[];
};
export type StructurePreview = {
  geometry: StructureGeometry;
  review_only: true;
  can_apply: boolean;
  layout_checked: boolean;
  layout_issues: Array<{
    code: string;
    message: string;
    face_id?: string;
    object_id?: string;
    field?: string;
  }>;
};

export function structureIssueLocation(
  issue: { face_id?: string; object_id?: string; field?: string },
  faces: Array<{ id: string; objects: Array<{ id: string }> }>,
) {
  const parts = issue.field?.match(/^faces\.([^.]+)(?:\.objects\.([^.]+))?/);
  const faceKey = issue.face_id || parts?.[1];
  const face =
    faces.find((item) => item.id === faceKey) ||
    (faceKey && /^\d+$/.test(faceKey) ? faces[Number(faceKey)] : undefined);
  if (!face) return null;
  const objectKey = issue.object_id || parts?.[2];
  const object =
    face.objects.find((item) => item.id === objectKey) ||
    (objectKey && /^\d+$/.test(objectKey)
      ? face.objects[Number(objectKey)]
      : undefined);
  return { faceId: face.id, objectId: object?.id };
}

export const separatedStructureExample: StructureDefinition = {
  schema_version: "2.0",
  recipe_id: "three-side-seal-separated-v1",
  family: "three-side-seal",
  dimension_semantics: { basis: "finished_outer" },
  width_range_mm: { minimum: 60, maximum: 600 },
  height_range_mm: { minimum: 80, maximum: 800 },
  seals_mm: { left: 8, right: 12, top: 6, bottom: 14 },
};
export const fixedStructureExample: StructureDefinition = {
  schema_version: "2.0",
  recipe_id: "fixed-panel-net-v1",
  family: "three-side-seal",
  dimension_semantics: { basis: "finished_outer" },
  dimensions: { width_mm: 160, height_mm: 230 },
  panels: [
    {
      id: "front",
      width_mm: 160,
      height_mm: 230,
      net: { x_mm: 0, y_mm: 0 },
      assembly: { position_mm: [0, 0, 3], rotation_deg: [0, 0, 0] },
      safe_inset_mm: { left: 15, right: 15, top: 15, bottom: 15 },
    },
    {
      id: "back",
      width_mm: 160,
      height_mm: 230,
      net: { x_mm: 175, y_mm: 0 },
      assembly: { position_mm: [0, 0, -3], rotation_deg: [0, 180, 0] },
      safe_inset_mm: { left: 15, right: 15, top: 15, bottom: 15 },
    },
  ],
};

export function parseStructureJson(text: string): StructureDefinition {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("구조 JSON 문법을 확인해 주세요.");
  }
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("구조 정의는 JSON 객체여야 합니다.");
  const data = value as Record<string, unknown>;
  if (
    data.schema_version !== "2.0" ||
    !["fixed-panel-net-v1", "three-side-seal-separated-v1"].includes(
      String(data.recipe_id),
    )
  )
    throw new Error("지원하는 구조 버전과 레시피를 확인해 주세요.");
  if (
    !["three-side-seal", "stand-up-pouch", "folding-box"].includes(
      String(data.family),
    )
  )
    throw new Error("포장 종류를 확인해 주세요.");
  return data as StructureDefinition; // Complete field/geometry validation is performed by the server.
}

export function structureInitialInputs(
  definition: StructureDefinition,
  current: StructureInputs,
): StructureInputs {
  if (definition.recipe_id === "fixed-panel-net-v1") {
    const dimensions = definition.dimensions;
    return {
      width_mm: dimensions.width_mm,
      height_mm: dimensions.height_mm,
      ...(dimensions.bottom_mm != null
        ? { bottom_mm: dimensions.bottom_mm }
        : {}),
      ...(dimensions.depth_mm != null ? { depth_mm: dimensions.depth_mm } : {}),
    };
  }
  return { width_mm: current.width_mm, height_mm: current.height_mm };
}

export function structureInputError(
  definition: StructureDefinition,
  inputs: StructureInputs,
): string | null {
  if (
    Object.values(inputs).some((value) => !Number.isFinite(value) || value <= 0)
  )
    return "치수는 0보다 큰 숫자로 입력해 주세요.";
  if (definition.recipe_id === "fixed-panel-net-v1") {
    if (
      JSON.stringify(Object.entries(inputs).sort()) !==
      JSON.stringify(
        Object.entries(structureInitialInputs(definition, inputs)).sort(),
      )
    )
      return "고정 도면은 등록한 치수 그대로만 적용할 수 있습니다.";
  } else {
    if (inputs.bottom_mm !== undefined || inputs.depth_mm !== undefined)
      return "분리 삼방 실링에는 폭과 높이만 입력합니다.";
    for (const axis of ["width", "height"] as const) {
      const value = inputs[`${axis}_mm`],
        range = definition[`${axis}_range_mm`];
      if (
        !Number.isFinite(value) ||
        value < range.minimum ||
        value > range.maximum
      )
        return `${axis === "width" ? "폭" : "높이"}는 등록 범위 ${range.minimum}–${range.maximum}mm 안에서 입력해 주세요.`;
    }
  }
  return null;
}

export function structureSelectionKey(
  id: string,
  inputs: StructureInputs,
): string {
  return JSON.stringify([
    id,
    inputs.width_mm,
    inputs.height_mm,
    inputs.bottom_mm ?? null,
    inputs.depth_mm ?? null,
  ]);
}
export function structurePreviewCurrent(
  preview:
    | { selectionKey: string; sceneKey: string; baseRevision: number }
    | undefined,
  selectionKey: string,
  sceneKey: string,
  revision?: number,
): boolean {
  return (
    !!preview &&
    preview.selectionKey === selectionKey &&
    preview.sceneKey === sceneKey &&
    (revision === undefined || preview.baseRevision === revision)
  );
}
