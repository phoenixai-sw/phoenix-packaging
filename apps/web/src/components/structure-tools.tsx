"use client";
import { useEffect, useRef, useState } from "react";
import { Barcode, Plus, Trash2, LoaderCircle, ScanLine, Scissors } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { Feedback } from "./management";
import type { Scene, SceneObject } from "@editor/model";
import { defaultPouchFeatures, hangerPreset, type FaceStructure } from "@editor/structure";
export function StructureTools({
  scene,
  faceId,
  onCommit,
  readOnly,
  geometry,
  onGeometry,
  onFaceSelect,
  initialBarcode,
}: {
  scene: Scene;
  faceId: string;
  onCommit: (next: Scene) => void;
  readOnly: boolean;
  geometry?: { faces: FaceStructure[] };
  onGeometry: (geometry: Record<string, unknown>) => void;
  onFaceSelect: (faceId: string) => void;
  initialBarcode?: string;
}) {
  const [code, setCode] = useState(initialBarcode || "");
  const [moduleMm, setModuleMm] = useState(0.33);
  const [height, setHeight] = useState(22.85);
  const [owned, setOwned] = useState(false);
  const [usage, setUsage] = useState<"retail" | "sample">("retail");
  const [featuresEnabled, setFeaturesEnabled] = useState(!!scene.pouch_features);
  const [features, setFeatures] = useState<NonNullable<Scene["pouch_features"]>>(scene.pouch_features || defaultPouchFeatures);
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
  const box = scene.template_kind === "folding-box";
  const holePreset = hangerPreset(face.width_mm, geometry?.faces.find((item) => item.id === faceId)?.regions);
  useEffect(() => {
    setFeaturesEnabled(!!scene.pouch_features);
    setFeatures(scene.pouch_features || defaultPouchFeatures);
  }, [scene.pouch_features]);
  async function validateGeometry(next: Scene) {
    const front = next.faces.find((item) => item.id === "front")!;
    return api<Record<string, unknown>>("/geometry/validate", {
      method: "POST", body: JSON.stringify({ template_id: next.template_kind || "three-side-seal",
        width_mm: front.width_mm, height_mm: front.height_mm, bottom_mm: next.bottom_mm, depth_mm: next.depth_mm,
        unit: "mm", holes: next.holes || [], pouch_features: next.pouch_features || null }),
    });
  }
  async function applyFeatures(event: React.FormEvent) {
    event.preventDefault();
    if (readOnly || busy) return;
    setBusy(true); setError(""); setNotice("");
    const next = { ...scene, pouch_features: featuresEnabled ? features : null };
    try {
      const result = await validateGeometry(next);
      if (!active.current) return;
      if (latestScene.current !== scene) throw new Error("검증 중 디자인이 바뀌었습니다. 다시 적용해 주세요.");
      onCommit(next); onGeometry(result);
      setNotice("앞·뒷면에 같은 상단 가공 조건을 적용했습니다. 새 안전영역과 겹치는 문구는 이동한 뒤 출력 검수를 실행하세요.");
    } catch (cause) { if (active.current) setError(errorMessage(cause)); }
    finally { if (active.current) setBusy(false); }
  }
  async function generateSample() {
    if (busy || readOnly) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const data = await api<{ value: string }>("/geometry/barcode", { method: "POST", body: JSON.stringify({ barcode_usage: "sample", module_mm: moduleMm, bar_height_mm: height }) });
      if (!active.current) return;
      setCode(data.value); setUsage("sample"); setOwned(false);
      setNotice("검토 전용 샘플 번호를 준비했습니다. ‘검증하고 추가’를 누르면 SAMPLE 표기와 함께 배치됩니다. 실제 상품 번호로 사용할 수 없습니다.");
    } catch (cause) { if (active.current) setError(errorMessage(cause)); }
    finally { if (active.current) setBusy(false); }
  }
  async function addBarcode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if ((x === "") !== (y === ""))
        throw new Error("직접 배치할 때는 X와 Y 좌표를 모두 입력해 주세요.");
      const data = await api<{ value: string; width_mm: number; height_mm: number; placement: { x_mm: number; y_mm: number } }>(
        "/geometry/barcode",
        {
          method: "POST",
          body: JSON.stringify({
            value: code,
            barcode_usage: usage,
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
        barcode_value: data.value,
        module_mm: moduleMm,
        bar_height_mm: height,
        barcode_owned: usage === "sample" ? false : owned,
        barcode_usage: usage,
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
  async function addHole(e: React.FormEvent) {
    e.preventDefault();
    if (busy || readOnly) return;
    const next = {
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
    };
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await validateGeometry(next);
      if (!active.current) return;
      if (latestScene.current !== scene) throw new Error("검증 중 디자인이 바뀌었습니다. 다시 추가해 주세요.");
      onCommit(next); onGeometry(result);
      setNotice("앞·뒷면을 관통하는 원형 걸이 구멍을 추가했습니다. 두 면의 문구와 겹치지 않는지 검수하세요.");
    } catch (cause) { if (active.current) setError(errorMessage(cause)); }
    finally { if (active.current) setBusy(false); }
  }
  return (
    <div className="structure-tools">
      <Feedback error={error} notice={notice} />
      <div className="alert alert-info">
        현재 면: {face.name} · 바코드는 가로·세로를 임의로 늘리지 않습니다.
        구멍은 별도 가공 요소로 저장합니다.
      </div>
      {box ? <div className="alert alert-info">
        박스에는 앞·뒤·좌·우·윗면·바닥의 6면이 있습니다. 파우치 지퍼와 뜯는 홈은 적용하지 않습니다.
        <button type="button" className="button button-light button-sm" onClick={() => onFaceSelect("top")}>박스 윗면 편집</button>
      </div> : <form className="management-card structure-feature-form" onSubmit={applyFeatures}>
        <h3><Scissors size={18} /> 상단 개봉부 · 지퍼 · 뜯는 홈</h3>
        <label className="compact-check"><input type="checkbox" checked={featuresEnabled} disabled={readOnly || busy} onChange={(event) => setFeaturesEnabled(event.target.checked)} /> 상단 가공 설정 사용</label>
        <p className="field-hint">위쪽 모서리에서 아래로 재는 mm 값입니다. 앞·뒷면에 함께 적용되며 제조사 승인 전에는 검토용 구조입니다.</p>
        <fieldset disabled={!featuresEnabled || readOnly || busy} className="editor-properties-fieldset">
          <div className="form-two-columns">
            <label className="field">상단 개봉 여유 (mm)<input type="number" min={20} max={100} step={0.5} value={features.header_height_mm} onChange={(event) => setFeatures({ ...features, header_height_mm: Number(event.target.value) })} /></label>
            <label className="compact-check"><input type="checkbox" checked={features.zipper_enabled} onChange={(event) => setFeatures({ ...features, zipper_enabled: event.target.checked })} /> 지퍼 사용</label>
            <label className="field">지퍼 중심 Y (mm)<input type="number" min={0} step={0.5} disabled={!features.zipper_enabled} value={features.zipper_y_mm} onChange={(event) => setFeatures({ ...features, zipper_y_mm: Number(event.target.value) })} /></label>
            <label className="field">지퍼 대역 높이 (mm)<input type="number" min={1} step={0.5} disabled={!features.zipper_enabled} value={features.zipper_band_mm} onChange={(event) => setFeatures({ ...features, zipper_band_mm: Number(event.target.value) })} /></label>
            <label className="compact-check"><input type="checkbox" checked={features.tear_enabled} onChange={(event) => setFeatures({ ...features, tear_enabled: event.target.checked })} /> 뜯는 선과 좌우 홈 사용</label>
            <label className="field">뜯는 선 Y (mm)<input type="number" min={0} step={0.5} disabled={!features.tear_enabled} value={features.tear_y_mm} onChange={(event) => setFeatures({ ...features, tear_y_mm: Number(event.target.value) })} /></label>
            <label className="field">홈 깊이 (mm)<input type="number" min={0.5} step={0.5} disabled={!features.tear_enabled} value={features.notch_depth_mm} onChange={(event) => setFeatures({ ...features, notch_depth_mm: Number(event.target.value) })} /></label>
            <label className="field">홈 높이 (mm)<input type="number" min={1} step={0.5} disabled={!features.tear_enabled} value={features.notch_height_mm} onChange={(event) => setFeatures({ ...features, notch_height_mm: Number(event.target.value) })} /></label>
            <label className="field">홈 모양<select disabled={!features.tear_enabled} value={features.notch_shape || "round"} onChange={(event) => setFeatures({ ...features, notch_shape: event.target.value as "round" | "v" })}><option value="round">둥근 U자 홈</option><option value="v">삼각 V자 홈</option></select></label>
          </div>
        </fieldset>
        <div className="structure-legend"><span>보라: 지퍼 가이드</span><span>분홍 점선: 뜯는 위치</span><span>좌우 홈·구멍: 절개 영역</span></div>
        <button className="button button-dark" disabled={busy || readOnly}>{busy && <LoaderCircle className="spin" size={15} />} 가공 조건 검증하고 적용</button>
      </form>}
      <div className="form-two-columns">
        <form onSubmit={addBarcode} className="management-card">
          <h3>
            <Barcode size={18} /> EAN-13 바코드
          </h3>
          <label className="field">
            {usage === "sample" ? "검토용 샘플 번호" : "고객이 소유한 13자리 번호"}
            <input
              required
              inputMode="numeric"
              pattern="[0-9]{13}"
              maxLength={13}
              readOnly={usage === "sample"}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="선행 0도 그대로 입력"
            />
          </label>
          <button type="button" className="button button-light button-sm" disabled={busy || readOnly} onClick={() => void generateSample()}>검토용 샘플 번호 만들기</button>
          {usage === "sample" && <><p className="field-hint">SAMPLE / 검토용 · 번호 소유권을 뜻하지 않으며 제작용 출력이 차단됩니다.</p><button type="button" className="text-link" disabled={busy || readOnly} onClick={() => { setUsage("retail"); setCode(""); setOwned(false); }}>실제 상품 번호 직접 입력</button></>}
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
              disabled={usage === "sample" || readOnly}
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
        {!box && <form onSubmit={addHole} className="management-card">
          <h3>
            <ScanLine size={18} /> 원형 걸이 구멍
          </h3>
          <button type="button" className="button button-light button-sm" disabled={!holePreset || busy || readOnly} onClick={() => {
            if (holePreset) { setHoleX(holePreset.center_x_mm); setHoleY(holePreset.center_y_mm); setDiameter(holePreset.diameter_mm); }
          }}>허용 헤더 중앙값 입력 · Ø 6mm</button>
          <p className="field-hint">기본 가공값(개봉 여유 30mm)의 헤더 중앙은 X {face.width_mm / 2} / Y 15mm입니다. 앞·뒤에 각각 추가할 필요가 없습니다. 변경한 가공 조건과의 간격은 추가할 때 서버에서 검증합니다.</p>
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
              min={4}
              max={10}
              step={0.1}
              value={diameter}
              onChange={(e) => setDiameter(Number(e.target.value))}
            />
          </label>
          <button className="button button-dark full-width" disabled={readOnly || busy || !["front", "back"].includes(faceId)}>
            <Plus size={16} /> 구멍 추가
          </button>
          <div className="hole-list">
            {scene.holes
              ?.filter((h) => h.face_id === faceId || (["front", "back"].includes(faceId) && ["front", "back"].includes(h.face_id)))
              .map((h) => (
                <div key={h.id}>
                  <span>
                    Ø {h.diameter_mm} mm · X {h.center_x_mm} / Y {h.center_y_mm}
                    {h.face_id !== faceId && " · 반대면과 공유"}
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
        </form>}
      </div>
    </div>
  );
}
