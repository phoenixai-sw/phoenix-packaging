"use client";
import { useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { Feedback } from "./management";
import { updateObject, type Scene, type SceneObject } from "@editor/model";
export type ApplyPreparedScene = (
  expectedScene: string,
  expectedRevision: number,
  transform: (scene: Scene) => Scene,
) => Promise<void>;
type Quality = {
  source: { id: string; sha256: string; width_px: number; height_px: number };
  base_revision: number;
  face_id: string;
  object_id: string;
  effective_ppi: number;
  original_effective_ppi: number;
  bleed_missing_mm: {
    left: number;
    right: number;
    top: number;
    bottom: number;
  } | null;
  required_pixels: { width: number; height: number };
  warnings: Array<string | { message: string }>;
};
type Preview = {
  asset: { id: string; width_px: number; height_px: number; url: string };
  patch: Pick<
    SceneObject,
    "asset_id" | "x_mm" | "y_mm" | "width_mm" | "height_mm" | "crop"
  >;
  quality: Quality;
  base_revision: number;
  face_id: string;
  object_id: string;
};
export function ImageQualityTools({
  projectId,
  scene,
  object,
  saveCurrent,
  onApply,
  readOnly,
}: {
  projectId: string;
  scene: Scene;
  object: SceneObject;
  saveCurrent: () => Promise<number>;
  onApply: ApplyPreparedScene;
  readOnly: boolean;
}) {
  const [target, setTarget] = useState(300),
    [resample, setResample] = useState(true),
    [bleed, setBleed] = useState<"none" | "edge" | "mirror">("edge");
  const [quality, setQuality] = useState<Quality>(),
    [preview, setPreview] = useState<Preview>(),
    [snapshot, setSnapshot] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const stale = !!snapshot && snapshot !== JSON.stringify(scene);
  async function inspect() {
    setBusy(true);
    setError("");
    setPreview(undefined);
    setNotice("");
    try {
      const expected = JSON.stringify(scene);
      const base_revision = await saveCurrent();
      const result = await api<Quality>("/image-quality/inspect", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          base_revision,
          face_id: object.face_id,
          object_id: object.id,
          target_ppi: target,
        }),
      });
      setQuality(result);
      setSnapshot(expected);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function prepare() {
    if (!quality) return;
    setBusy(true);
    setError("");
    setReviewed(false);
    try {
      const result = await api<Preview>("/image-quality/preview", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          base_revision: quality.base_revision,
          face_id: object.face_id,
          object_id: object.id,
          target_ppi: target,
          source_sha256: quality.source.sha256,
          operation_key: crypto.randomUUID(),
          resample,
          bleed_mode: bleed,
        }),
      });
      setPreview(result);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function apply() {
    if (!preview || !quality) return;
    setBusy(true);
    setError("");
    try {
      if (
        preview.object_id !== object.id ||
        preview.face_id !== object.face_id ||
        preview.base_revision !== quality.base_revision
      )
        throw new Error(
          "보완 결과의 원본 정보가 일치하지 않습니다. 다시 진단해 주세요.",
        );
      await onApply(snapshot, quality.base_revision, (current) =>
        updateObject(current, object.id, preview.patch),
      );
      setPreview(undefined);
      setQuality(undefined);
      setSnapshot("");
      setNotice(
        "보완 이미지를 적용하고 저장했습니다. 원본 이미지는 보존됩니다. 최종 출력 검수를 다시 실행하세요.",
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="preparation-tools">
      <p className="field-hint">
        확대는 픽셀 수를 늘리는 보간 처리이며, 흐린 원본의 디테일을 복원하거나
        실제 촬영 해상도를 높이지 않습니다. 도련 연장은 가장자리 복제 또는
        반사로 새 영역을 채웁니다.
      </p>
      <Feedback error={error} notice={notice} />
      {stale && (
        <Feedback error="디자인이 바뀌었습니다. 다시 진단한 뒤 준비해 주세요." />
      )}
      <div className="form-row">
        <label className="field">
          목표 픽셀 밀도
          <select
            value={target}
            disabled={busy}
            onChange={(e) => {
              setTarget(Number(e.target.value));
              setQuality(undefined);
              setPreview(undefined);
            }}
          >
            <option value={300}>300 PPI</option>
            <option value={600}>600 PPI</option>
          </select>
        </label>
        <button
          className="button button-dark"
          disabled={busy || readOnly}
          onClick={() => void inspect()}
        >
          {busy ? "확인 중…" : "현재 이미지 진단"}
        </button>
      </div>
      {quality && (
        <>
          <div className="quality-metrics">
            <div>
              <small>현재 배치</small>
              <strong>{quality.effective_ppi.toFixed(1)} PPI</strong>
            </div>
            <div>
              <small>원본 기준</small>
              <strong>{quality.original_effective_ppi.toFixed(1)} PPI</strong>
            </div>
            <div>
              <small>원본 픽셀</small>
              <strong>
                {quality.source.width_px} × {quality.source.height_px}
              </strong>
            </div>
            <div>
              <small>목표에 필요한 픽셀</small>
              <strong>
                {quality.required_pixels.width} ×{" "}
                {quality.required_pixels.height}
              </strong>
            </div>
          </div>
          <p className="field-hint">
            {quality.bleed_missing_mm
              ? `부족한 도련 · 왼쪽 ${quality.bleed_missing_mm.left.toFixed(2)} / 오른쪽 ${quality.bleed_missing_mm.right.toFixed(2)} / 위 ${quality.bleed_missing_mm.top.toFixed(2)} / 아래 ${quality.bleed_missing_mm.bottom.toFixed(2)} mm`
              : "회전된 이미지: 도련 부족량은 회전을 해제한 뒤 확인하세요."}
          </p>
          {quality.warnings.map((w, i) => (
            <Feedback key={i} notice={typeof w === "string" ? w : w.message} />
          ))}
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={resample}
              disabled={busy || readOnly}
              onChange={(e) => {
                setResample(e.target.checked);
                setPreview(undefined);
              }}
            />{" "}
            목표 픽셀 수로 확대 보간
          </label>
          <label className="field">
            도련 채우기
            <select
              value={bleed}
              disabled={busy || readOnly}
              onChange={(e) => {
                setBleed(e.target.value as typeof bleed);
                setPreview(undefined);
              }}
            >
              <option value="none">사용하지 않음</option>
              <option value="edge" disabled={object.rotation_deg !== 0}>
                가장자리 색 늘리기
              </option>
              <option value="mirror" disabled={object.rotation_deg !== 0}>
                가장자리 반사
              </option>
            </select>
          </label>
          {object.rotation_deg !== 0 && (
            <p className="field-hint">
              회전된 이미지의 도련 채우기는 지원하지 않습니다. ‘사용하지 않음’을
              선택하거나 편집기에서 회전을 조정하세요.
            </p>
          )}
          <button
            className="button button-light"
            disabled={
              busy ||
              readOnly ||
              stale ||
              (!resample && bleed === "none") ||
              (object.rotation_deg !== 0 && bleed !== "none")
            }
            onClick={() => void prepare()}
          >
            무료 보완 이미지 준비 · 원본 유지
          </button>
        </>
      )}
      {preview && (
        <>
          <div className="image-compare">
            <figure>
              <img
                src={`/api/v1/assets/${object.asset_id}/content`}
                alt="보완 전 원본"
              />
              <figcaption>
                {object.crop ? "자르기 전 전체 원본" : "원본"}
              </figcaption>
            </figure>
            <figure>
              <img
                src={`/api/v1/assets/${preview.asset.id}/content`}
                alt="확대 또는 도련 보완 결과"
              />
              <figcaption>
                보완 결과 · {preview.asset.width_px} × {preview.asset.height_px}
              </figcaption>
            </figure>
          </div>
          <p className="field-hint">
            보완 후 {preview.quality.effective_ppi.toFixed(1)} PPI · 원본 기준{" "}
            {preview.quality.original_effective_ppi.toFixed(1)} PPI. 보간 후에도
            원본 해상도 경고는 유지됩니다.
          </p>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={reviewed}
              onChange={(e) => setReviewed(e.target.checked)}
            />{" "}
            가장자리 반복·이음새·문구 변형 여부를 직접 확인했습니다.
          </label>
          <button
            className="button button-orange"
            disabled={!reviewed || busy || readOnly || stale}
            onClick={() => void apply()}
          >
            선택한 이미지에 적용하고 저장
          </button>
        </>
      )}
    </div>
  );
}
