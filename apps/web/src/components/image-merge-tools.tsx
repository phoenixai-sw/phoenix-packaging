"use client";
import { useMemo, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { ApiSchema } from "@/lib/api-contract";
import { Feedback } from "./management";
import type { Scene, SceneObject } from "@editor/model";
import type { ApplyPreparedScene } from "./image-quality-tools";

type Merged = ApiSchema<"ImageMergeResult">;

/** Flatten stacked image layers into one opaque image. A cut-out layer keeps an alpha channel,
 *  and the CMYK production output rejects it; merging is what makes the artwork printable. */
export function ImageMergeTools({ projectId, scene, faceId, selectedId, saveCurrent, onApply, readOnly }: {
  projectId: string; scene: Scene; faceId: string; selectedId: string | null;
  saveCurrent: () => Promise<number>; onApply: ApplyPreparedScene; readOnly: boolean;
}) {
  const layers = useMemo(() => {
    const face = scene.faces.find((f) => f.id === faceId);
    return (face?.objects || [])
      .map((object, index) => ({ object, index }))
      .filter(({ object }) => object.type === "image" && object.asset_id)
      .sort((a, b) => b.object.z_index - a.object.z_index || b.index - a.index)
      .map(({ object }) => object);
  }, [scene, faceId]);
  const [picked, setPicked] = useState<string[]>(() => (selectedId && layers.some((o) => o.id === selectedId) ? [selectedId] : []));
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const chosen = picked.filter((id) => layers.some((o) => o.id === id));
  const blocked = layers.filter((o) => chosen.includes(o.id) && (o.locked || o.visible === false || o.print_enabled === false));
  const locked = busy || readOnly;

  function toggle(object: SceneObject) {
    setError(""); setNotice("");
    setPicked((current) => (current.includes(object.id) ? current.filter((id) => id !== object.id) : [...current, object.id]));
  }
  async function merge() {
    if (locked || chosen.length < 2) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const expected = JSON.stringify(scene);
      const base_revision = await saveCurrent();
      const result = await api<Merged>("/image-quality/merge", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, base_revision, face_id: faceId, object_ids: chosen, operation_key: crypto.randomUUID(), target_ppi: 300 }),
      });
      await onApply(expected, base_revision, (current) => ({
        ...current,
        faces: current.faces.map((face) => (face.id !== faceId ? face : {
          ...face,
          objects: face.objects
            .filter((object) => !result.remove_object_ids.includes(object.id))
            .map((object) => (object.id === result.keep_object_id ? { ...object, ...result.patch, crop: null } : object)),
        })),
      }));
      setPicked([result.keep_object_id]);
      setNotice(`레이어 ${result.object_ids.length}개를 불투명한 이미지 한 장(${result.output_pixels[0]} × ${result.output_pixels[1]} px · ${Math.round(result.effective_ppi)}ppi)으로 합쳤습니다.`
        + (result.background_filled ? " 비어 있던 부분은 면 바탕색으로 채웠습니다." : "")
        + " 크레딧 0. 실행 취소로 되돌릴 수 있습니다.");
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return (
    <div className="image-text-tools">
      <div className="alert alert-info">
        따낸 레이어는 배경이 투명해서 <strong>CMYK 제작 출력에서 거부됩니다</strong>. 겹쳐 있는 이미지 레이어를 골라 <strong>불투명한 한 장</strong>으로 합치면 제작 출력에 쓸 수 있습니다. 합친 뒤에는 각 레이어를 따로 옮길 수 없습니다.
      </div>
      <fieldset className="stack" disabled={locked} style={{ border: 0, padding: 0, margin: 0 }}>
        <legend>합칠 레이어 (위 → 아래)</legend>
        {!layers.length && <p className="field-hint">이 면에는 이미지 레이어가 없습니다.</p>}
        <ul className="image-text-lines">
          {layers.map((object, index) => (
            <li key={object.id}>
              <label className="checkbox-label" style={{ flex: 1, minWidth: 0 }}>
                <input type="checkbox" checked={chosen.includes(object.id)} onChange={() => toggle(object)} />
                <span>
                  이미지 {layers.length - index} · {object.width_mm.toFixed(1)} × {object.height_mm.toFixed(1)} mm
                  {object.locked ? " · 잠김" : ""}{object.visible === false ? " · 숨김" : ""}{object.print_enabled === false ? " · 인쇄 제외" : ""}
                </span>
              </label>
            </li>
          ))}
        </ul>
      </fieldset>
      <p className="field-hint">
        {chosen.length < 2 ? "겹쳐 있는 레이어를 2개 이상 고르세요. 맨 아래 레이어가 합친 이미지의 자리를 이어받습니다." : `${chosen.length}개 선택됨`}
        {blocked.length ? " · 잠김·숨김·인쇄 제외 레이어는 먼저 풀어야 합니다." : ""}
      </p>
      <div className="button-row">
        <button className="button button-dark" disabled={locked || chosen.length < 2 || !!blocked.length} onClick={() => void merge()}>
          선택한 레이어 병합 · 무료
        </button>
      </div>
      <Feedback error={error} notice={notice} />
    </div>
  );
}
