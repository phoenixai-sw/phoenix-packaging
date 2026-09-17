import type { Scene } from "./model.ts";
export function compareScenes(before: Scene, after: Scene) {
  const result: Array<{
    face_id: string;
    object_id?: string;
    label: string;
    before: string;
    after: string;
  }> = [];
  const faces = new Set([
    ...before.faces.map((f) => f.id),
    ...after.faces.map((f) => f.id),
  ]);
  for (const id of faces) {
    const a = before.faces.find((f) => f.id === id),
      b = after.faces.find((f) => f.id === id);
    if (!a || !b) {
      result.push({
        face_id: id,
        label: "면",
        before: a?.name || "없음",
        after: b?.name || "없음",
      });
      continue;
    }
    for (const key of ["background", "width_mm", "height_mm"] as const)
      if (a[key] !== b[key])
        result.push({
          face_id: id,
          label: {
            background: "배경색",
            width_mm: "면 폭",
            height_mm: "면 높이",
          }[key],
          before: String(a[key]),
          after: String(b[key]),
        });
    const ids = new Set([
      ...a.objects.map((o) => o.id),
      ...b.objects.map((o) => o.id),
    ]);
    for (const object_id of ids) {
      const x = a.objects.find((o) => o.id === object_id),
        y = b.objects.find((o) => o.id === object_id);
      if (!x || !y) {
        result.push({
          face_id: id,
          object_id,
          label: x ? "레이어 삭제" : "레이어 추가",
          before: x ? x.text || x.type : "없음",
          after: y ? y.text || y.type : "없음",
        });
        continue;
      }
      for (const key of new Set([...Object.keys(x), ...Object.keys(y)])) {
        if (key === "id" || key === "face_id") continue;
        const v = x[key as keyof typeof x],
          w = y[key as keyof typeof y];
        if (JSON.stringify(v ?? null) !== JSON.stringify(w ?? null))
          result.push({
            face_id: id,
            object_id,
            label:
              (
                {
                  text: "문구",
                  asset_id: "이미지",
                  x_mm: "X 위치",
                  y_mm: "Y 위치",
                  width_mm: "폭",
                  height_mm: "높이",
                  crop: "자르기",
                  font_weight: "굵기",
                  font_size_pt: "글자 크기",
                  letter_spacing: "자간",
                  line_height: "행간",
                  opacity: "불투명도",
                  locked: "잠금",
                  rotation_deg: "회전",
                  color: "색상",
                  z_index: "레이어 순서",
                  visible: "표시",
                  print_enabled: "출력 포함",
                  align: "문구 정렬",
                } as Record<string, string>
              )[key] || key,
            before: typeof v === "string" ? v : JSON.stringify(v ?? null),
            after: typeof w === "string" ? w : JSON.stringify(w ?? null),
          });
      }
    }
  }
  for (const key of [
    "holes",
    "pouch_features",
    "template_version_id",
    "reviewed_face_ids",
    "confirmed_fields",
  ] as const)
    if (
      JSON.stringify(before[key] ?? null) !== JSON.stringify(after[key] ?? null)
    )
      result.push({
        face_id: "전체",
        label: {
          holes: "걸이 구멍",
          pouch_features: "파우치 가공",
          template_version_id: "제조 도면",
          reviewed_face_ids: "면 확인",
          confirmed_fields: "정보 확인",
        }[key],
        before: JSON.stringify(before[key] ?? null),
        after: JSON.stringify(after[key] ?? null),
      });
  return result;
}
