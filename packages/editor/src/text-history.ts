import type { Scene, SceneObject } from "./model.ts";

export type TextRevision = {
  id: string;
  number: number;
  reason?: string;
  created_at: string;
  scene: Scene;
};
export type TextChange = {
  key: string;
  face_id: string;
  face_name: string;
  object_id: string;
  kind: "added" | "removed" | "changed";
  before?: string;
  after?: string;
  properties: string[];
};
const fields: Array<[keyof SceneObject, string]> = [
  ["letter_spacing", "자간"],
  ["line_height", "행간"],
  ["opacity", "불투명도"],
  ["locked", "잠금"],
  ["font_size_pt", "글자 크기"],
  ["font_weight", "글자 굵기"],
  ["color", "색상"],
  ["align", "정렬"],
  ["x_mm", "X 위치"],
  ["y_mm", "Y 위치"],
  ["width_mm", "상자 폭"],
  ["height_mm", "상자 높이"],
  ["rotation_deg", "회전"],
  ["visible", "표시 여부"],
  ["print_enabled", "출력 여부"],
  ["binding_key", "상품 연결"],
];
function textObjects(scene: Scene) {
  return new Map(
    scene.faces.flatMap((face) =>
      face.objects
        .filter((object) => object.type === "text")
        .map(
          (object) => [`${face.id}:${object.id}`, { face, object }] as const,
        ),
    ),
  );
}
export function compareTextScenes(before: Scene, after: Scene): TextChange[] {
  const previous = textObjects(before),
    next = textObjects(after);
  return [...new Set([...previous.keys(), ...next.keys()])].flatMap((key) => {
    const old = previous.get(key),
      current = next.get(key),
      entry = current || old!;
    const properties =
      old && current
        ? fields
            .filter(
              ([field]) =>
                (field === "font_weight"
                  ? (old.object[field] ?? 400)
                  : old.object[field]) !==
                (field === "font_weight"
                  ? (current.object[field] ?? 400)
                  : current.object[field]),
            )
            .map(([, label]) => label)
        : [];
    if (
      old &&
      current &&
      old.object.text === current.object.text &&
      !properties.length
    )
      return [];
    return [
      {
        key,
        face_id: entry.face.id,
        face_name: entry.face.name,
        object_id: entry.object.id,
        kind: !old ? "added" : !current ? "removed" : "changed",
        before: old?.object.text,
        after: current?.object.text,
        properties,
      } as TextChange,
    ];
  });
}
export function textRevisionHistory(revisions: TextRevision[]) {
  const ordered = [...revisions].sort((a, b) => a.number - b.number);
  return ordered
    .slice(1)
    .map((revision, index) => ({
      revision,
      previous_number: ordered[index].number,
      has_gap: revision.number - ordered[index].number > 1,
      changes: compareTextScenes(ordered[index].scene, revision.scene),
    }))
    .filter((entry) => entry.changes.length)
    .reverse();
}
