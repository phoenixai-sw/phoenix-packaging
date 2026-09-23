"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { ApiSchema } from "@/lib/api-contract";
import { Feedback } from "./management";
import { regionPlacement, regionFromPoints, type ImageRegion } from "@editor/image-tools";
import { wandSelection } from "@editor/magic-wand";
import type { Scene, SceneObject } from "@editor/model";
import type { ApplyPreparedScene } from "./image-quality-tools";

type Quote = ApiSchema<"QuoteData">;
type Job = Pick<ApiSchema<"AIGenerationJob">, "id" | "status"> & Partial<ApiSchema<"AIGenerationJob">>;
type Shape =
  | { type: "rect"; x: number; y: number; width: number; height: number }
  | { type: "brush"; points: number[][]; radius: number }
  | { type: "polygon"; points: number[][] };
type Tool = "brush" | "rect" | "lasso" | "wand";
/** Flood filling the full asset is the point — a downscale would blur the colour edges the wand
 *  follows — but a huge upload still has to stay responsive, so cap the pixels it walks. */
const WAND_MAX_PIXELS = 12_000_000;
type Mode = "region" | "cutout";
const MAX_SHAPES = 64, MAX_POINTS = 3000;
/** Place `layer` right after `sourceId`. Painting sorts by z_index and keeps list order for ties,
 *  so the copy draws just above its source without moving any other layer. */
function insertAbove(objects: SceneObject[], sourceId: string, layer: SceneObject) {
  const at = objects.findIndex((o) => o.id === sourceId);
  return [...objects.slice(0, at + 1), layer, ...objects.slice(at + 1)];
}
const points = (shapes: Shape[]) => shapes.reduce((total, s) => total + (s.type === "rect" ? 0 : s.points.length), 0);
const terminal = ["succeeded", "partially_succeeded", "failed", "canceled", "reconciliation_required"];
const labels: Record<string, string> = {
  queued: "대기 중", running: "처리 중", waiting_provider: "AI 수정 중", validating: "결과 검사 중",
  succeeded: "완료", partially_succeeded: "일부 완료", failed: "실패", canceled: "취소됨", reconciliation_required: "사용량 확인 중",
};

/** Photoshop-style partial edits on one image layer: paint or box the area, then let AI change
 *  only that area, cut the subject out to its own transparent layer, or copy a box to a new layer for free. */
export function ImageRegionTools({ projectId, scene, object, saveCurrent, onApply, readOnly }: {
  projectId: string; scene: Scene; object: SceneObject; saveCurrent: () => Promise<number>; onApply: ApplyPreparedScene; readOnly: boolean;
}) {
  const [shapes, setShapes] = useState<Shape[]>([]);
  const [tool, setTool] = useState<Tool>("brush"), [radius, setRadius] = useState(0.04);
  const [mode, setMode] = useState<Mode>("region"), [prompt, setPrompt] = useState("");
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [quote, setQuote] = useState<Quote>(), [job, setJob] = useState<Job>();
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [snapshot, setSnapshot] = useState(""), [baseRevision, setBaseRevision] = useState(0);
  const canvasRef = useRef<HTMLCanvasElement>(null), hostRef = useRef<HTMLDivElement>(null);
  const stroke = useRef<number[][] | null>(null), rectStart = useRef<{ x: number; y: number } | null>(null);
  const [liveRect, setLiveRect] = useState<ImageRegion | null>(null);
  const [hostWidth, setHostWidth] = useState(0);
  const [tolerance, setTolerance] = useState(28);
  const sourcePixels = useRef<{ data: Uint8ClampedArray; width: number; height: number } | null>(null);
  const working = !!job && !terminal.includes(job.status);
  const locked = busy || working || readOnly || !!object.locked;
  const stale = !!snapshot && snapshot !== JSON.stringify(scene);
  const rect = useMemo(() => {
    const boxes = shapes.filter((s): s is Extract<Shape, { type: "rect" }> => s.type === "rect");
    return boxes.length === 1 && shapes.length === 1 ? boxes[0] : null;
  }, [shapes]);
  const asset = job?.result?.assets?.find((a) => a.reference_asset_id === object.asset_id && (a.edit_mode === "region" || a.edit_mode === "cutout"));

  // Draw the mask overlay in image space; the canvas is scaled to the displayed image.
  useEffect(() => {
    const canvas = canvasRef.current, host = hostRef.current;
    if (!canvas || !host) return;
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    canvas.width = w; canvas.height = h;
    const g = canvas.getContext("2d");
    if (!g) return;
    g.clearRect(0, 0, w, h);
    g.fillStyle = "rgba(47,111,237,0.35)"; g.strokeStyle = "rgba(47,111,237,0.35)"; g.lineCap = "round"; g.lineJoin = "round";
    const trace = (points: number[][]) => {
      g.beginPath();
      points.forEach(([x, y], i) => (i ? g.lineTo(x * w, y * h) : g.moveTo(x * w, y * h)));
    };
    const draw = (shape: Shape) => {
      if (shape.type === "rect") { g.fillRect(shape.x * w, shape.y * h, shape.width * w, shape.height * h); return; }
      if (shape.type === "polygon") {
        // Three points make an area; fewer is still being traced, so show the line so far.
        trace(shape.points);
        if (shape.points.length < 3) { g.lineWidth = 2; g.stroke(); return; }
        g.closePath(); g.fill();
        return;
      }
      g.lineWidth = shape.radius * Math.min(w, h) * 2;
      trace(shape.points);
      if (shape.points.length === 1) g.lineTo(shape.points[0][0] * w + 0.01, shape.points[0][1] * h);
      g.stroke();
    };
    shapes.forEach(draw);
    if (liveRect) draw({ type: "rect", ...liveRect });
  }, [shapes, liveRect, dimensions, hostWidth]);
  // The canvas is sized in device pixels, so a panel resize has to redraw it.
  useEffect(() => {
    const host = hostRef.current;
    if (!host || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setHostWidth(host.clientWidth));
    observer.observe(host);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!job || terminal.includes(job.status)) return;
    let stopped = false; let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { const value = await api<Job>(`/jobs/${job!.id}`); if (stopped) return; setJob(value); if (!terminal.includes(value.status)) timer = setTimeout(poll, 2000); }
      catch (e) { if (!stopped) { setError(errorMessage(e)); timer = setTimeout(poll, 6000); } }
    }
    timer = setTimeout(poll, 1200);
    return () => { stopped = true; clearTimeout(timer); };
  }, [job?.id, job?.status]);

  function point(e: React.PointerEvent<HTMLDivElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
  }
  function invalidate() { setQuote(undefined); setJob(undefined); setSnapshot(""); setError(""); setNotice(""); }
  /** The asset's own pixels, decoded once. Fetching as a blob keeps the canvas untainted even in
   *  production, where the asset URL redirects to signed storage on another origin. */
  async function readSource() {
    if (sourcePixels.current) return sourcePixels.current;
    const response = await fetch(`/api/v1/assets/${object.asset_id}/content`, { credentials: "same-origin" });
    if (!response.ok) throw new Error("원본 이미지를 불러오지 못했습니다.");
    const bitmap = await createImageBitmap(await response.blob());
    try {
      if (bitmap.width * bitmap.height > WAND_MAX_PIXELS)
        throw new Error("이미지가 너무 커서 자동 선택을 쓸 수 없습니다. 브러시나 올가미로 표시해 주세요.");
      const canvas = document.createElement("canvas");
      canvas.width = bitmap.width; canvas.height = bitmap.height;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) throw new Error("이 브라우저에서는 자동 선택을 쓸 수 없습니다.");
      context.drawImage(bitmap, 0, 0);
      sourcePixels.current = { data: context.getImageData(0, 0, bitmap.width, bitmap.height).data, width: bitmap.width, height: bitmap.height };
      return sourcePixels.current;
    } finally { bitmap.close(); }
  }
  async function wandAt(p: { x: number; y: number }) {
    setBusy(true); setError(""); setNotice("");
    try {
      const source = await readSource();
      const budget = Math.min(800, MAX_POINTS - points(shapes));
      const selection = wandSelection(source.data, source.width, source.height,
        p.x * source.width, p.y * source.height, { tolerance, maxPoints: budget });
      if (!selection) throw new Error("이 자리에서는 영역을 찾지 못했습니다. 허용 오차를 올리거나 올가미로 그려 주세요.");
      invalidate();
      setShapes((s) => [...s, { type: "polygon", points: selection.points }]);
      setNotice(`같은 색 영역 ${selection.points.length}점으로 표시했습니다. 덜 잡히면 허용 오차를 올리세요.`);
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  function onDown(e: React.PointerEvent<HTMLDivElement>) {
    if (locked) return;
    if (shapes.length >= MAX_SHAPES || points(shapes) >= MAX_POINTS) {
      setError("표시할 수 있는 영역을 모두 썼습니다. “되돌리기”나 “모두 지우기”로 정리한 뒤 이어 그려 주세요.");
      return;
    }
    const clicked = point(e);
    if (tool === "wand") { void wandAt(clicked); return; }
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = clicked;
    invalidate();
    if (tool === "brush") { stroke.current = [[p.x, p.y]]; setShapes((s) => [...s, { type: "brush", points: [[p.x, p.y]], radius }]); }
    else if (tool === "lasso") { stroke.current = [[p.x, p.y]]; setShapes((s) => [...s, { type: "polygon", points: [[p.x, p.y]] }]); }
    else rectStart.current = p;
  }
  function onMove(e: React.PointerEvent<HTMLDivElement>) {
    if (locked) return;
    const p = point(e);
    const tracing = tool === "brush" ? "brush" : tool === "lasso" ? "polygon" : null;
    if (tracing && stroke.current) {
      const last = stroke.current[stroke.current.length - 1];
      if (Math.hypot(p.x - last[0], p.y - last[1]) < 0.004 || stroke.current.length >= 400 || points(shapes) >= MAX_POINTS) return;
      stroke.current.push([p.x, p.y]);
      const pts = stroke.current;
      setShapes((s) => s.map((shape, i) => (i === s.length - 1 && shape.type === tracing ? { ...shape, points: [...pts] } : shape)));
    } else if (tool === "rect" && rectStart.current) setLiveRect(regionFromPoints(rectStart.current, p));
  }
  function onUp(e: React.PointerEvent<HTMLDivElement>) {
    if (tool === "rect" && rectStart.current) {
      const r = regionFromPoints(rectStart.current, point(e));
      if (r.width > 0.002 && r.height > 0.002) setShapes((s) => [...s, { type: "rect", ...r }]);
      rectStart.current = null; setLiveRect(null);
    }
    // A lasso closes itself; a trace too short to enclose anything is dropped rather than sent.
    // Read the length now: React runs the updater after `stroke.current` is cleared below.
    if (tool === "lasso" && stroke.current) {
      const traced = stroke.current.length;
      setShapes((s) => (traced < 3 && s.length && s[s.length - 1].type === "polygon" ? s.slice(0, -1) : s));
    }
    stroke.current = null;
  }
  function editShapes() {
    const round = (pts: number[][]) => pts.map(([x, y]) => [Math.round(x * 1e4) / 1e4, Math.round(y * 1e4) / 1e4]);
    return shapes.map((s) =>
      s.type === "rect" ? s
        : s.type === "polygon" ? { type: "polygon", points: round(s.points) }
          : { type: "brush", points: round(s.points), radius: Math.round(s.radius * 1e4) / 1e4 });
  }
  async function getQuote() {
    if (locked) return;
    setBusy(true); setError(""); setNotice("");
    try {
      if (!shapes.length && mode === "region") throw new Error("먼저 바꿀 부분을 브러시나 사각형으로 표시해 주세요.");
      if (mode === "region" && prompt.trim().length < 5) throw new Error("표시한 부분을 어떻게 바꿀지 5자 이상 적어 주세요.");
      const expected = JSON.stringify(scene);
      const revision = await saveCurrent();
      const result = await api<Quote>("/quotes", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId, base_revision: revision, action: "image.edit.standard", requested_units: 1,
          prompt: mode === "region" ? prompt.trim() : prompt.trim() || "주요 피사체를 배경에서 분리",
          face_id: object.face_id, reference_asset_id: object.asset_id,
          input_data: { edit_mode: mode, ...(shapes.length ? { edit_shapes: editShapes() } : {}) },
        }),
      });
      setQuote(result); setSnapshot(expected); setBaseRevision(revision); setJob(undefined);
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  async function start() {
    if (!quote || locked || stale) return;
    setBusy(true); setError("");
    try {
      const created = await api<Job>("/jobs", { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({ quote_id: quote.id }) });
      setJob(created); setQuote(undefined);
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  async function applyResult() {
    if (!asset || locked || stale) return;
    setBusy(true); setError("");
    try {
      await onApply(snapshot, baseRevision, (current) => {
        const face = current.faces.find((f) => f.objects.some((o) => o.id === object.id));
        const source = face?.objects.find((o) => o.id === object.id);
        if (!face || !source || source.type !== "image") throw new Error("원본 이미지 레이어를 다시 선택해 주세요.");
        if (asset.edit_mode === "region")
          return { ...current, faces: current.faces.map((f) => (f.id !== face.id ? f : { ...f, objects: f.objects.map((o) => (o.id === source.id ? { ...o, asset_id: asset.id } : o)) })) };
        // Cut-out: the same geometry stacked directly above its source, as a duplicate is in
        // Photoshop. Sharing the source's z and sitting next to it in the list keeps every other
        // layer — text above a background, say — exactly where it was, and keeps the stack mergeable.
        const layer: SceneObject = { ...source, id: crypto.randomUUID(), asset_id: asset.id, locked: false };
        return { ...current, faces: current.faces.map((f) => (f.id !== face.id ? f : { ...f, objects: insertAbove(f.objects, source.id, layer) })) };
      });
      setNotice(asset.edit_mode === "region"
        ? "표시한 부분만 바뀐 새 이미지로 교체했습니다. 원본은 보관함에 남아 있고 실행 취소로 되돌릴 수 있습니다."
        : "분리한 피사체를 원본 위에 새 레이어로 추가했습니다. 레이어 목록에서 원본을 숨기거나 옮겨 보세요. CMYK 제작 출력 전에는 “레이어 병합” 탭에서 합쳐 주세요.");
      setJob(undefined); setSnapshot(""); setShapes([]);
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  async function copyRectToLayer() {
    if (!rect || locked) return;
    setBusy(true); setError("");
    try {
      const expected = JSON.stringify(scene);
      const revision = await saveCurrent();
      await onApply(expected, revision, (current) => {
        const face = current.faces.find((f) => f.objects.some((o) => o.id === object.id));
        const source = face?.objects.find((o) => o.id === object.id);
        if (!face || !source || source.type !== "image") throw new Error("원본 이미지 레이어를 다시 선택해 주세요.");
        const crop = source.crop || { x: 0, y: 0, width: 1, height: 1 };
        const region = { x: crop.x + rect.x * crop.width, y: crop.y + rect.y * crop.height, width: rect.width * crop.width, height: rect.height * crop.height };
        const placement = regionPlacement(source, region);
        const layer: SceneObject = { ...source, ...placement, id: crypto.randomUUID(), crop: region, locked: false };
        return { ...current, faces: current.faces.map((f) => (f.id !== face.id ? f : { ...f, objects: insertAbove(f.objects, source.id, layer) })) };
      });
      setNotice("표시한 사각형을 잘라 새 레이어로 복사했습니다(크레딧 0). 이동·크기 조절·잠금은 레이어 목록에서 합니다.");
      setShapes([]);
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return (
    <div className="image-text-tools">
      <div className="alert alert-info">
        포토샵의 선택 영역처럼 <strong>바꿀 부분만 표시</strong>하고 AI에게 시키거나, 피사체를 <strong>투명 배경 레이어로 따내거나</strong>, 사각형을 새 레이어로 복사합니다. 표시 밖 픽셀은 그대로 보존됩니다. 브러시는 칠하듯, <strong>올가미는 테두리를 따라</strong> 그리면 안쪽이 채워지고, <strong>“같은 색 자동”은 클릭 한 번</strong>으로 이어진 같은 색 영역을 잡습니다.
      </div>
      <div className="button-row" role="toolbar" aria-label="영역 도구">
        <button className={`button button-light${tool === "brush" ? " selected" : ""}`} disabled={locked} onClick={() => setTool("brush")}>브러시</button>
        <button className={`button button-light${tool === "lasso" ? " selected" : ""}`} disabled={locked} onClick={() => setTool("lasso")}>올가미</button>
        <button className={`button button-light${tool === "wand" ? " selected" : ""}`} disabled={locked} onClick={() => setTool("wand")}>같은 색 자동</button>
        <button className={`button button-light${tool === "rect" ? " selected" : ""}`} disabled={locked} onClick={() => setTool("rect")}>사각형</button>
        <label className="field" style={{ minWidth: 140 }}>{tool === "wand" ? "색 허용 오차" : "브러시 굵기"}
          {tool === "wand"
            ? <input type="range" min={0} max={120} step={4} value={tolerance} disabled={locked} onChange={(e) => setTolerance(Number(e.target.value))} />
            : <input type="range" min={0.01} max={0.15} step={0.005} value={radius} disabled={locked || tool !== "brush"} onChange={(e) => setRadius(Number(e.target.value))} />}
        </label>
        <button className="button button-light" disabled={locked || !shapes.length} onClick={() => { setShapes((s) => s.slice(0, -1)); invalidate(); }}>되돌리기</button>
        <button className="button button-light" disabled={locked || !shapes.length} onClick={() => { setShapes([]); invalidate(); }}>모두 지우기</button>
      </div>
      <div ref={hostRef} className="image-region-selector" style={{ cursor: locked ? "default" : "crosshair" }}
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={() => { stroke.current = null; rectStart.current = null; setLiveRect(null); }}
        title={tool === "wand" ? "바꿀 색 영역을 클릭하세요" : ""}>
        <img draggable={false} src={`/api/v1/assets/${object.asset_id}/content`} alt="부분 수정할 원본. 드래그로 영역 표시"
          onLoad={(e) => setDimensions({ width: e.currentTarget.naturalWidth, height: e.currentTarget.naturalHeight })} />
        <canvas ref={canvasRef} className="image-region-mask" aria-hidden="true" />
      </div>
      <p className="field-hint">원본 {dimensions.width} × {dimensions.height} px · 표시 {shapes.length}개{object.crop ? " · 잘라낸 이미지는 원본 전체 좌표로 표시됩니다" : ""}</p>
      {/* The mask edge is hard: outside pixels are kept byte for byte, so nothing blends across it.
          Cutting through flat artwork leaves a faint seam; a boundary the eye already expects hides it. */}
      <p className="field-hint">표시는 <strong>색이 바뀌는 자리나 물체의 테두리까지 넉넉히</strong> 잡아 주세요. 무늬 한가운데를 자르면 새로 그린 부분과 원본 사이에 옅은 경계선이 보일 수 있습니다.</p>
      <div className="button-row">
        <button className="button button-light" disabled={locked || !rect} onClick={() => void copyRectToLayer()} title={rect ? "" : "사각형 하나만 표시했을 때 사용할 수 있습니다"}>사각형을 새 레이어로 복사 · 무료</button>
      </div>
      <fieldset className="stack" disabled={locked} style={{ border: 0, padding: 0, margin: 0 }}>
        <legend>AI 작업</legend>
        <label><input type="radio" name="region-mode" checked={mode === "region"} onChange={() => { setMode("region"); invalidate(); }} /> 표시한 부분만 AI로 바꾸기 (10크레딧)</label>
        <label><input type="radio" name="region-mode" checked={mode === "cutout"} onChange={() => { setMode("cutout"); invalidate(); }} /> 피사체를 투명 배경 레이어로 따기 (10크레딧)</label>
        <label className="field">{mode === "region" ? "표시한 부분을 어떻게 바꿀까요?" : "따낼 피사체 설명 (선택)"}
          <textarea rows={3} maxLength={4000} value={prompt} onChange={(e) => { setPrompt(e.target.value); invalidate(); }}
            placeholder={mode === "region" ? "예: 이 자리의 오리 그림을 웃는 강아지 그림으로 바꿔 주세요" : "예: 가운데 오리 캐릭터만"} />
        </label>
      </fieldset>
      <div className="button-row">
        <button className="button" disabled={locked} onClick={() => void getQuote()}>크레딧 견적 확인</button>
        {quote && !stale && <button className="button button-dark" disabled={locked} onClick={() => void start()}>{quote.credit_total}크레딧 사용해 시작</button>}
      </div>
      {stale && <Feedback error="디자인이 바뀌어 이전 견적·결과는 적용할 수 없습니다. 다시 견적을 확인하세요." />}
      <Feedback error={error} notice={notice} />
      {job && (
        <div role="status" className="stack">
          <p>{labels[job.status] || job.status}{job.status === "failed" && job.result?.units?.[0]?.error_code ? ` · ${job.result.units[0].error_code}` : ""}</p>
          {asset && (
            <div className="image-compare">
              <figure><img src={`/api/v1/assets/${object.asset_id}/content`} alt="원본" /><figcaption>원본</figcaption></figure>
              <figure><img src={`/api/v1/assets/${asset.id}/content`} alt="결과" style={asset.has_alpha ? { background: "repeating-conic-gradient(#ddd 0% 25%, #fff 0% 50%) 50% / 16px 16px" } : undefined} /><figcaption>{asset.edit_mode === "cutout" ? "분리한 레이어 (투명 배경)" : "표시한 부분만 바뀐 결과"}</figcaption></figure>
            </div>
          )}
          {asset && <button className="button button-dark" disabled={locked || stale} onClick={() => void applyResult()}>{asset.edit_mode === "cutout" ? "새 레이어로 추가" : "이 결과로 교체"}</button>}
        </div>
      )}
    </div>
  );
}
