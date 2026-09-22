"use client";
import { useState } from "react";
import dynamic from "next/dynamic";
import type { Scene } from "@editor/model";
import type { ApplyPreparedScene } from "./image-quality-tools";
import { Feedback } from "./management";
const ImageTextTools = dynamic(
  () => import("./image-text-tools").then((m) => m.ImageTextTools),
  { ssr: false },
);
const ImageRegionTools = dynamic(
  () => import("./image-region-tools").then((m) => m.ImageRegionTools),
  { ssr: false },
);
const ImageQualityTools = dynamic(
  () => import("./image-quality-tools").then((m) => m.ImageQualityTools),
  { ssr: false },
);
export function ImagePreparationTools({
  projectId,
  scene,
  faceId,
  selectedId,
  saveCurrent,
  onApply,
  readOnly,
  initialTab = "quality",
}: {
  projectId: string;
  scene: Scene;
  faceId: string;
  selectedId: string | null;
  saveCurrent: () => Promise<number>;
  onApply: ApplyPreparedScene;
  readOnly: boolean;
  initialTab?: "quality" | "text" | "region";
}) {
  const images =
    scene.faces
      .find((f) => f.id === faceId)
      ?.objects.filter((o) => o.type === "image" && o.asset_id) || [];
  const [id, setId] = useState(
    images.some((o) => o.id === selectedId) ? selectedId! : images[0]?.id || "",
  );
  const [tab, setTab] = useState(initialTab),
    [notice, setNotice] = useState("");
  const object = images.find((o) => o.id === id);
  const apply: ApplyPreparedScene = async (...args) => {
    await onApply(...args);
    setNotice(
      "변경 내용을 저장했습니다. 편집기에서 배치와 줄바꿈을 확인하고 최종 출력 검수를 실행하세요. 실행 취소로 되돌릴 수 있습니다.",
    );
  };
  return (
    <div className="preparation-tools">
      <Feedback notice={notice} />
      <label className="field">
        현재 면의 이미지 레이어
        <select
          value={id}
          onChange={(e) => {
            setId(e.target.value);
            setNotice("");
          }}
        >
          {!images.length && <option value="">이미지를 먼저 추가하세요</option>}
          {images.map((o, index) => (
            <option key={o.id} value={o.id}>
              이미지 {index + 1} · {o.width_mm.toFixed(1)} ×{" "}
              {o.height_mm.toFixed(1)} mm{o.visible === false ? " · 숨김" : ""}
            </option>
          ))}
        </select>
      </label>
      <div className="preparation-tabs" role="tablist" aria-label="이미지 도구">
        <button
          role="tab"
          aria-selected={tab === "quality"}
          onClick={() => setTab("quality")}
        >
          해상도·도련 보완
        </button>
        <button
          role="tab"
          aria-selected={tab === "text"}
          onClick={() => setTab("text")}
        >
          이미지 속 글자 편집
        </button>
        <button
          role="tab"
          aria-selected={tab === "region"}
          onClick={() => setTab("region")}
        >
          부분 수정·레이어 따기
        </button>
      </div>
      {!object ? (
        <p className="field-hint">
          이미지 업로드 또는 AI 시안 적용 후 사용할 수 있습니다. 텍스트 레이어는
          편집기에서 직접 수정하세요.
        </p>
      ) : tab === "quality" ? (
        <ImageQualityTools
          key={`${object.id}:${object.asset_id}`}
          projectId={projectId}
          scene={scene}
          object={object}
          saveCurrent={saveCurrent}
          onApply={apply}
          readOnly={readOnly || !!object.locked}
        />
      ) : tab === "region" ? (
        <ImageRegionTools
          key={`${object.id}:${object.asset_id}`}
          projectId={projectId}
          scene={scene}
          object={object}
          saveCurrent={saveCurrent}
          onApply={apply}
          readOnly={readOnly || !!object.locked}
        />
      ) : (
        <ImageTextTools
          key={`${object.id}:${object.asset_id}`}
          projectId={projectId}
          scene={scene}
          object={object}
          saveCurrent={saveCurrent}
          onApply={apply}
          readOnly={readOnly || !!object.locked}
        />
      )}
    </div>
  );
}
