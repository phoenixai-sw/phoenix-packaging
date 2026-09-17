"use client";
import { useEffect, useRef, useState } from "react";
import { Barcode, Plus, Trash2, LoaderCircle, ScanLine } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { Feedback } from "./management";
import type { Scene, SceneObject } from "@editor/model";
export function StructureTools({
  scene,
  faceId,
  onCommit,
  readOnly,
}: {
  scene: Scene;
  faceId: string;
  onCommit: (next: Scene) => void;
  readOnly: boolean;
}) {
  const [code, setCode] = useState("");
  const [moduleMm, setModuleMm] = useState(0.33);
  const [height, setHeight] = useState(22.85);
  const [owned, setOwned] = useState(false);
  const [x, setX] = useState("");
  const [y, setY] = useState("");
  const [holeX, setHoleX] = useState(50);
  const [holeY, setHoleY] = useState(20);
  const [diameter, setDiameter] = useState(6);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const latestScene = useRef(scene);
  latestScene.current = scene;
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  const face = scene.faces.find((f) => f.id === faceId)!;
  async function addBarcode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if ((x === "") !== (y === ""))
        throw new Error("직접 배치할 때는 X와 Y 좌표를 모두 입력해 주세요.");
      const data = await api<{ width_mm: number; height_mm: number; placement: { x_mm: number; y_mm: number } }>(
        "/geometry/barcode",
        {
          method: "POST",
          body: JSON.stringify({
            value: code,
            module_mm: moduleMm,
            bar_height_mm: height,
            scene,
            face_id: faceId,
            ...(x !== "" ? { x_mm: Number(x), y_mm: Number(y) } : {}),
          }),
        },
      );
      const object: SceneObject = {
        id: crypto.randomUUID(),
        type: "barcode",
        face_id: faceId,
        x_mm: data.placement.x_mm,
        y_mm: data.placement.y_mm,
        width_mm: data.width_mm,
        height_mm: data.height_mm,
        rotation_deg: 0,
        z_index: Math.min(10000, Math.max(0, ...face.objects.map((o) => o.z_index)) + 1),
        barcode_value: code,
        module_mm: moduleMm,
        bar_height_mm: height,
        barcode_owned: owned,
        visible: true,
        print_enabled: true,
      };
      if (!active.current) return;
      if (latestScene.current !== scene)
        throw new Error("검증 중 디자인이 변경되었습니다. 현재 디자인에서 다시 추가해 주세요.");
      onCommit({
        ...scene,
        faces: scene.faces.map((f) =>
          f.id === faceId ? { ...f, objects: [...f.objects, object] } : f,
        ),
      });
      setNotice(
        `여백과 충돌을 확인하고 X ${data.placement.x_mm} / Y ${data.placement.y_mm}mm에 바코드를 추가했습니다.`,
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  function addHole(e: React.FormEvent) {
    e.preventDefault();
    onCommit({
      ...scene,
      holes: [
        ...(scene.holes || []),
        {
          id: crypto.randomUUID(),
          face_id: faceId,
          center_x_mm: holeX,
          center_y_mm: holeY,
          diameter_mm: diameter,
        },
      ],
    });
    setNotice(
      "걸이 구멍을 추가했습니다. 제조사 허용 영역과 다른 요소의 충돌을 검수해 주세요.",
    );
  }
  return (
    <div className="structure-tools">
      <Feedback error={error} notice={notice} />
      <div className="alert alert-info">
        현재 면: {face.name} · 바코드는 가로·세로를 임의로 늘리지 않습니다.
        구멍은 별도 가공 요소로 저장합니다.
      </div>
      <div className="form-two-columns">
        <form onSubmit={addBarcode} className="management-card">
          <h3>
            <Barcode size={18} /> EAN-13 바코드
          </h3>
          <label className="field">
            고객이 소유한 13자리 번호
            <input
              required
              inputMode="numeric"
              pattern="[0-9]{13}"
              maxLength={13}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="선행 0도 그대로 입력"
            />
          </label>
          <div className="form-two-columns">
            <label className="field">
              모듈 폭 (mm)
              <input
                type="number"
                min={0.264}
                max={0.66}
                step={0.001}
                value={moduleMm}
                onChange={(e) => setModuleMm(Number(e.target.value))}
              />
            </label>
            <label className="field">
              막대 높이 (mm)
              <input
                type="number"
                min={18.28}
                max={45.7}
                step={0.01}
                value={height}
                onChange={(e) => setHeight(Number(e.target.value))}
              />
            </label>
            <label className="field">
              X (mm)
              <input
                type="number"
                step={0.1}
                value={x}
                placeholder="비워 두면 자동 배치"
                onChange={(e) => setX(e.target.value)}
              />
            </label>
            <label className="field">
              Y (mm)
              <input
                type="number"
                step={0.1}
                value={y}
                placeholder="비워 두면 자동 배치"
                onChange={(e) => setY(e.target.value)}
              />
            </label>
          </div>
          <label className="compact-check">
            <input
              type="checkbox"
              checked={owned}
              onChange={(e) => setOwned(e.target.checked)}
            />{" "}
            이 상품에 사용할 수 있는 소유 번호입니다.
          </label>
          <p className="field-hint">
            X·Y를 비워 두면 문구와 가공 영역을 피해 자동으로 배치합니다.{" "}
            번호를 발급하지 않습니다. 소유 미확인 번호는 제작 출력이 제한됩니다.
          </p>
          <button
            className="button button-dark full-width"
            disabled={busy || readOnly}
          >
            {busy ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <>
                <Plus size={16} /> 검증하고 추가
              </>
            )}
          </button>
        </form>
        <form onSubmit={addHole} className="management-card">
          <h3>
            <ScanLine size={18} /> 원형 걸이 구멍
          </h3>
          <div className="form-two-columns">
            <label className="field">
              중심 X (mm)
              <input
                type="number"
                step={0.1}
                required
                value={holeX}
                onChange={(e) => setHoleX(Number(e.target.value))}
              />
            </label>
            <label className="field">
              중심 Y (mm)
              <input
                type="number"
                step={0.1}
                required
                value={holeY}
                onChange={(e) => setHoleY(Number(e.target.value))}
              />
            </label>
          </div>
          <label className="field">
            지름 (mm)
            <input
              type="number"
              required
              min={1}
              max={30}
              step={0.1}
              value={diameter}
              onChange={(e) => setDiameter(Number(e.target.value))}
            />
          </label>
          <button className="button button-dark full-width" disabled={readOnly}>
            <Plus size={16} /> 구멍 추가
          </button>
          <div className="hole-list">
            {scene.holes
              ?.filter((h) => h.face_id === faceId)
              .map((h) => (
                <div key={h.id}>
                  <span>
                    Ø {h.diameter_mm} mm · X {h.center_x_mm} / Y {h.center_y_mm}
                  </span>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="걸이 구멍 삭제"
                    disabled={readOnly}
                    onClick={() =>
                      onCommit({
                        ...scene,
                        holes: scene.holes?.filter((item) => item.id !== h.id),
                      })
                    }
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              ))}
          </div>
          <p className="field-hint">
            구멍의 위치는 제조사 허용 영역과 실제 도면으로 확인해야 합니다.
          </p>
        </form>
      </div>
    </div>
  );
}
