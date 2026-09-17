"use client";
import { useState } from "react";
import type { SceneObject } from "@editor/model";
import { validateCrop } from "@editor/layout";
import { Feedback } from "./management";
const full = { x: 0, y: 0, width: 1, height: 1 };
export function ImageCropTools({
  object,
  onApply,
  readOnly,
}: {
  object: SceneObject;
  onApply: (patch: Partial<SceneObject>) => void;
  readOnly: boolean;
}) {
  const [crop, setCrop] = useState(object.crop || full),
    [start, setStart] = useState<{ x: number; y: number } | null>(null);
  const [pixels, setPixels] = useState({ width: 0, height: 0 }),
    [preserve, setPreserve] = useState(true),
    [error, setError] = useState("");
  function point(event: React.PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
      y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
    };
  }
  function apply(value: typeof crop | null) {
    try {
      if (readOnly || object.locked) return;
      if (value) validateCrop(value);
      const c = value || full;
      onApply({
        crop: value,
        ...(preserve && pixels.width && pixels.height
          ? {
              height_mm:
                Math.round(
                  ((object.width_mm * pixels.height * c.height) /
                    (pixels.width * c.width)) *
                    10000,
                ) / 10000,
            }
          : {}),
      });
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "자르기 영역을 확인해 주세요.",
      );
    }
  }
  return (
    <div className="crop-tools">
      <p className="panel-hint">
        원본 위에서 드래그하거나 비율 좌표를 입력하세요. 원본 파일은 보존하며
        자르기는 언제든 해제할 수 있습니다.
      </p>
      <div
        className="crop-source"
        style={{
          maxWidth: pixels.height
            ? `${(440 * pixels.width) / pixels.height}px`
            : undefined,
        }}
        onPointerDown={(event) => {
          if (readOnly || object.locked) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          setStart(point(event));
        }}
        onPointerMove={(event) => {
          if (!start) return;
          const p = point(event);
          setCrop({
            x: Math.min(start.x, p.x),
            y: Math.min(start.y, p.y),
            width: Math.max(0.001, Math.abs(p.x - start.x)),
            height: Math.max(0.001, Math.abs(p.y - start.y)),
          });
        }}
        onPointerUp={() => setStart(null)}
        onPointerCancel={() => setStart(null)}
      >
        <img
          style={{ height: "auto", maxHeight: "none" }}
          src={`/api/v1/assets/${object.asset_id}/content`}
          alt="자를 원본 이미지"
          draggable={false}
          onLoad={(e) =>
            setPixels({
              width: e.currentTarget.naturalWidth,
              height: e.currentTarget.naturalHeight,
            })
          }
        />
        <div
          className="crop-selection"
          style={{
            left: `${crop.x * 100}%`,
            top: `${crop.y * 100}%`,
            width: `${crop.width * 100}%`,
            height: `${crop.height * 100}%`,
          }}
        />
      </div>
      <div className="form-two-columns">
        {(
          [
            ["x", "왼쪽"],
            ["y", "위쪽"],
            ["width", "가로"],
            ["height", "세로"],
          ] as const
        ).map(([key, label]) => (
          <label className="field" key={key}>
            {label} (%)
            <input
              type="number"
              step="0.1"
              min={key === "x" || key === "y" ? 0 : 0.1}
              max={100}
              disabled={readOnly || object.locked}
              value={Math.round(crop[key] * 10000) / 100}
              onChange={(event) =>
                setCrop({ ...crop, [key]: Number(event.target.value) / 100 })
              }
            />
          </label>
        ))}
      </div>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={preserve}
          disabled={readOnly || object.locked}
          onChange={(e) => setPreserve(e.target.checked)}
        />
        자른 영역의 비율에 맞춰 배치 높이 조정
      </label>
      <p className="field-hint">
        해제하면 기존 배치 폭·높이를 유지합니다. 자른 뒤 이미지의 유효 해상도와
        안전영역을 다시 확인하세요.
      </p>
      <Feedback error={error} />
      <div className="inline-actions">
        <button
          className="button button-dark"
          disabled={readOnly || object.locked || !pixels.width}
          onClick={() => apply(crop)}
        >
          자르기 적용
        </button>
        <button
          className="button button-light"
          disabled={readOnly || object.locked || !pixels.width}
          onClick={() => apply(null)}
        >
          전체 원본으로 해제
        </button>
      </div>
    </div>
  );
}
