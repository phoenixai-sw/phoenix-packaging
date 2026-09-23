"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Worker } from "tesseract.js";
import { api, ApiError, errorMessage } from "@/lib/api";
import type { ApiSchema } from "@/lib/api-contract";
import { useSession } from "./workspace";
import { readToolDraft, writeToolDraft, toolDraftKey, pendingAfterStartFailure } from "@/lib/tool-draft";
import { Feedback } from "./management";
import {
  applyImageText,
  regionFromPoints,
  validateImageRegion,
  imageRegionPixels,
  textRemovalInputKey,
  regionPlacement,
  detectedLineRegion,
  estimateFontSizePt,
  sampleTextColors,
  type ImageRegion,
  type DetectedTextLine,
} from "@editor/image-tools";
import type { Scene, SceneObject } from "@editor/model";
import type { ApplyPreparedScene } from "./image-quality-tools";
type Quote = ApiSchema<"QuoteData">;
type ResultAsset = ApiSchema<"GeneratedAsset">;
// A reopened local draft initially knows only the ID until the server is read.
type Job = Pick<ApiSchema<"AIGenerationJob">, "id" | "status"> & Partial<ApiSchema<"AIGenerationJob">>;
type TextDraft = {
  region: ImageRegion; sourceText: string; text: string; confirmed: boolean;
  fontSize: number; weight: 400 | 700; color: string; cover: string; confidence?: number;
  snapshot: string; baseRevision: number; preparedInput: string;
  jobId?: string; quote?: Quote; jobKey: string; pendingStart: boolean;
};
const terminal = [
  "succeeded",
  "partially_succeeded",
  "failed",
  "canceled",
  "reconciliation_required",
];
const labels: Record<string, string> = {
  queued: "대기 중",
  running: "처리 중",
  waiting_provider: "이미지 수정 중",
  validating: "결과 검사 중",
  succeeded: "완료",
  partially_succeeded: "일부 완료",
  failed: "실패",
  canceled: "취소됨",
  reconciliation_required: "사용량 확인 중",
};
export function ImageTextTools({
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
  const session = useSession();
  const draftKey = toolDraftKey(session!.user.id, session!.tenant.id, projectId, object.id, object.asset_id!);
  const [draftReady, setDraftReady] = useState(false), [resumeNotice, setResumeNotice] = useState("");
  const [pendingStart, setPendingStart] = useState(false), [draftWarning, setDraftWarning] = useState("");
  const [draftReload, setDraftReload] = useState(0);
  const hydrated = useRef(false);
  const [region, setRegion] = useState<ImageRegion>({
    x: (object.crop?.x || 0) + (object.crop?.width || 1) * 0.1,
    y: (object.crop?.y || 0) + (object.crop?.height || 1) * 0.1,
    width: (object.crop?.width || 1) * 0.8,
    height: (object.crop?.height || 1) * 0.2,
  });
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [sourceText, setSourceText] = useState(""),
    [text, setText] = useState("");
  const [confirmed, setConfirmed] = useState(false),
    [reviewed, setReviewed] = useState(false);
  const [fontSize, setFontSize] = useState(18),
    [weight, setWeight] = useState<400 | 700>(400),
    [color, setColor] = useState("#172d26"),
    [cover, setCover] = useState("#fff3de");
  /** The last values we filled in ourselves. A field still holding one is ours to refresh when the
   *  area changes; anything else the customer chose on purpose and we leave alone. */
  const autoFilled = useRef({ fontSize: 18, color: "#172d26", cover: "#fff3de" });
  const [zoomResult, setZoomResult] = useState(true);
  // Lines found over the whole (cropped) image; clicking one prefills region, text, size and colours.
  const [detected, setDetected] = useState<DetectedTextLine[]>([]),
    [selectedLine, setSelectedLine] = useState("");
  const sourceBlob = useRef<Blob | null>(null);
  const [confidence, setConfidence] = useState<number>(),
    [ocrStatus, setOcrStatus] = useState(""),
    [ocrBusy, setOcrBusy] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [quote, setQuote] = useState<Quote>(),
    [job, setJob] = useState<Job>(),
    [snapshot, setSnapshot] = useState(""),
    [baseRevision, setBaseRevision] = useState(0);
  const [preparedInput, setPreparedInput] = useState("");
  const worker = useRef<Worker | null>(null),
    active = useRef(true),
    ocrAttempt = useRef(0),
    startPoint = useRef<{ x: number; y: number } | null>(null),
    jobKey = useRef("");
  const working = pendingStart || (!!job && !terminal.includes(job.status));
  const draft = useMemo<TextDraft>(() => ({ region, sourceText, text, confirmed, fontSize, weight, color, cover,
    confidence, snapshot, baseRevision, preparedInput, jobId: job?.id, quote, jobKey: jobKey.current, pendingStart }),
    [region, sourceText, text, confirmed, fontSize, weight, color, cover, confidence, snapshot, baseRevision, preparedInput, job?.id, quote, pendingStart]);
  const latestDraft = useRef(draft);
  const completedDraft = useRef(false);
  latestDraft.current = draft;
  useEffect(() => {
    if (readOnly && hydrated.current) return;
    hydrated.current = false;
    setDraftReady(false);
    startPoint.current = null;
    ocrAttempt.current++;
    void worker.current?.terminate();
    worker.current = null;
    setOcrBusy(false);
    let stopped = false;
    void readToolDraft<TextDraft>(draftKey).then((saved) => {
      if (stopped) return;
      try {
        const value = saved?.value;
        // Rehydration replaces optional fields too; a completed/deleted draft must not resurrect.
        setJob(undefined); setQuote(undefined); setPendingStart(false); setReviewed(false);
        completedDraft.current = false;
        setResumeNotice(""); setOcrStatus(""); setConfidence(undefined);
        setRegion({ x: (object.crop?.x || 0) + (object.crop?.width || 1) * 0.1,
          y: (object.crop?.y || 0) + (object.crop?.height || 1) * 0.1,
          width: (object.crop?.width || 1) * 0.8, height: (object.crop?.height || 1) * 0.2 });
        setSourceText(""); setText(""); setConfirmed(false); setFontSize(18); setWeight(400);
        setColor("#172d26"); setCover("#fff3de"); setSnapshot(""); setBaseRevision(0); setPreparedInput("");
        jobKey.current = crypto.randomUUID();
        if (value && typeof value.sourceText === "string" && typeof value.text === "string" && typeof value.snapshot === "string") {
          validateImageRegion(value.region);
          setRegion(value.region); setSourceText(value.sourceText); setText(value.text);
          setConfirmed(!!value.confirmed); setFontSize(value.fontSize); setWeight(value.weight);
          setColor(value.color); setCover(value.cover); setConfidence(value.confidence);
          setSnapshot(value.snapshot); setBaseRevision(value.baseRevision); setPreparedInput(value.preparedInput);
          jobKey.current = value.jobKey || crypto.randomUUID();
          // Fetch the authoritative job, including completed jobs. Never start a paid request on reopening.
          if (value.jobId) setJob({ id: value.jobId, status: "queued" });
          if (value.pendingStart && value.quote && value.jobKey) {
            setPendingStart(true); setQuote(value.quote);
          }
          setResumeNotice(`이 기기에 보관한 ${new Date(saved!.updatedAt).toLocaleString("ko-KR")} 작업을 이어갑니다. 제거 결과는 다시 확인해야 합니다.`);
        }
      } catch { setDraftWarning("이전 중간 작업을 읽지 못했습니다. 서버 디자인과 AI 작업 이력은 보존됩니다."); }
      hydrated.current = true;
      setDraftReady(true);
    });
    return () => { stopped = true; };
  }, [draftKey, readOnly, draftReload]);
  useEffect(() => {
    if (!draftReady || !hydrated.current || readOnly || object.locked || completedDraft.current) return;
    void writeToolDraft(draftKey, draft).then((ok) => {
      if (active.current && !ok) setDraftWarning("중간 작업을 보관하지 못했습니다. 다른 창의 변경 또는 저장소 오류입니다. 현재 문구를 따로 보관한 뒤 최신 중간 작업을 다시 불러오세요.");
    });
  }, [draftKey, draftReady, draft, readOnly, object.locked]);
  const inputKey = textRemovalInputKey({
    assetId: object.asset_id!,
    region,
    sourceText,
  });
  const stale =
    !!snapshot &&
    (snapshot !== JSON.stringify(scene) || preparedInput !== inputKey);
  const asset = job?.result?.assets?.find(
    (a) =>
      a.edit_mode === "remove_text" && a.reference_asset_id === object.asset_id,
  );
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      ocrAttempt.current++;
      void worker.current?.terminate();
      worker.current = null;
    };
  }, []);
  useEffect(() => {
    if (!job || terminal.includes(job.status)) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value = await api<Job>(`/jobs/${job!.id}`);
        if (stopped) return;
        setJob(value);
        if (!terminal.includes(value.status)) timer = setTimeout(poll, 2000);
      } catch (e) {
        if (!stopped) {
          setError(errorMessage(e));
          timer = setTimeout(poll, 6000);
        }
      }
    }
    timer = setTimeout(poll, 1200);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [job?.id, job?.status]);
  function invalidate() {
    completedDraft.current = false;
    setQuote(undefined);
    setConfirmed(false);
    setReviewed(false);
    setConfidence(undefined);
    setNotice("");
  }
  function point(e: React.PointerEvent<HTMLDivElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    return {
      x: (e.clientX - r.left) / r.width,
      y: (e.clientY - r.top) / r.height,
    };
  }
  async function recognize() {
    if (!dimensions.width || ocrBusy) return;
    setError("");
    setOcrBusy(true);
    setOcrStatus("한국어·영어 인식 모델을 준비합니다…");
    const attempt = ++ocrAttempt.current;
    const selected = { ...region };
    let ownedWorker: Worker | null = null;
    try {
      validateImageRegion(selected);
      const response = await fetch(
        `/api/v1/assets/${object.asset_id}/content`,
        { credentials: "same-origin" },
      );
      if (!response.ok)
        throw new Error(
          "원본 이미지를 불러오지 못했습니다. 직접 문구를 입력할 수 있습니다.",
        );
      const blob = await response.blob();
      const { createWorker } = await import("tesseract.js");
      if (!active.current || attempt !== ocrAttempt.current) return;
      const created = await createWorker(["kor", "eng"], 1, {
        workerPath: "/ocr/worker.min.js",
        corePath: "/ocr",
        langPath: "/ocr",
        workerBlobURL: false,
        logger: (m) => {
          if (active.current && attempt === ocrAttempt.current)
            setOcrStatus(
              m.status === "recognizing text"
                ? `글자 읽는 중 ${Math.round(m.progress * 100)}%`
                : "한국어·영어 모델 불러오는 중…",
            );
        },
      });
      if (!active.current || attempt !== ocrAttempt.current) {
        await created.terminate();
        return;
      }
      worker.current = created;
      ownedWorker = created;
      const { data } = await created.recognize(blob, {
        rectangle: imageRegionPixels(
          selected,
          dimensions.width,
          dimensions.height,
        ),
      });
      if (active.current && attempt === ocrAttempt.current) {
        setSourceText(data.text.trim());
        setText(data.text.trim());
        setConfidence(data.confidence);
        setConfirmed(false);
        setQuote(undefined);
        setOcrStatus(
          data.text.trim()
            ? "인식 완료 · 원문과 대조하고 직접 고쳐 주세요."
            : "인식된 글자가 없습니다. 원문을 직접 입력해 주세요.",
        );
      }
    } catch (e) {
      if (active.current && attempt === ocrAttempt.current) {
        setError(
          `글자를 자동으로 읽지 못했습니다. 원문과 영역을 직접 입력하면 계속할 수 있습니다. ${errorMessage(e)}`,
        );
        setOcrStatus("");
      }
    } finally {
      if (ownedWorker) {
        await ownedWorker.terminate();
        if (worker.current === ownedWorker) worker.current = null;
      }
      if (active.current && attempt === ocrAttempt.current) setOcrBusy(false);
    }
  }
  async function loadSourceBlob() {
    if (sourceBlob.current) return sourceBlob.current;
    const response = await fetch(`/api/v1/assets/${object.asset_id}/content`, { credentials: "same-origin" });
    if (!response.ok) throw new Error("원본 이미지를 불러오지 못했습니다.");
    sourceBlob.current = await response.blob();
    return sourceBlob.current;
  }
  async function detectAll() {
    if (!dimensions.width || ocrBusy) return;
    setError("");
    setOcrBusy(true);
    setOcrStatus("이미지 전체에서 글자를 찾습니다…");
    const attempt = ++ocrAttempt.current;
    const bounds = object.crop || { x: 0, y: 0, width: 1, height: 1 };
    let ownedWorker: Worker | null = null;
    try {
      const blob = await loadSourceBlob();
      const { createWorker } = await import("tesseract.js");
      if (!active.current || attempt !== ocrAttempt.current) return;
      const created = await createWorker(["kor", "eng"], 1, {
        workerPath: "/ocr/worker.min.js", corePath: "/ocr", langPath: "/ocr", workerBlobURL: false,
        logger: (m) => {
          if (active.current && attempt === ocrAttempt.current)
            setOcrStatus(m.status === "recognizing text" ? `글자 찾는 중 ${Math.round(m.progress * 100)}%` : "한국어·영어 모델 불러오는 중…");
        },
      });
      if (!active.current || attempt !== ocrAttempt.current) { await created.terminate(); return; }
      worker.current = created;
      ownedWorker = created;
      const { data } = await created.recognize(blob, { rectangle: imageRegionPixels(bounds, dimensions.width, dimensions.height) }, { blocks: true });
      if (!active.current || attempt !== ocrAttempt.current) return;
      const lines: DetectedTextLine[] = [];
      for (const block of data.blocks || [])
        for (const paragraph of block.paragraphs || [])
          for (const line of paragraph.lines || []) {
            const text = line.text.trim();
            // Skip empty, low-confidence and symbol-only lines (edge artefacts such as "=" or "|").
            if (!text || line.confidence < 25 || !/[\p{L}\p{N}]/u.test(text)) continue;
            try {
              lines.push({ id: crypto.randomUUID(), text, confidence: line.confidence,
                region: detectedLineRegion(line.bbox, dimensions.width, dimensions.height, bounds) });
            } catch {
              // A degenerate box outside the crop is skipped.
            }
          }
      setDetected(lines);
      setSelectedLine("");
      setOcrStatus(lines.length ? `${lines.length}줄을 찾았습니다. 바꿀 글자를 클릭하세요.` : "글자를 찾지 못했습니다. 영역을 직접 드래그해 주세요.");
    } catch (e) {
      if (active.current && attempt === ocrAttempt.current) {
        setError(`글자를 자동으로 찾지 못했습니다. 영역을 직접 선택할 수 있습니다. ${errorMessage(e)}`);
        setOcrStatus("");
      }
    } finally {
      if (ownedWorker) { await ownedWorker.terminate(); if (worker.current === ownedWorker) worker.current = null; }
      if (active.current && attempt === ocrAttempt.current) setOcrBusy(false);
    }
  }
  /** Size and colours belong to the marked area, so they are re-derived whenever it moves.
   *  Sampling a stale area is what put a cream cover over a yellow band and a title-sized font in a
   *  line-sized box; both looked right in the form and wrong on the artwork. */
  async function fillFromRegion(next: ImageRegion, force = false) {
    try {
      const placement = regionPlacement(object, next);
      const size = estimateFontSizePt(placement.height_mm);
      if (force || fontSize === autoFilled.current.fontSize) { setFontSize(size); autoFilled.current.fontSize = size; }
    } catch {
      // Keep the current size when the region falls outside the crop.
    }
    try {
      const bitmap = await createImageBitmap(await loadSourceBlob());
      const box = imageRegionPixels(next, bitmap.width, bitmap.height);
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, box.width); canvas.height = Math.max(1, box.height);
      const context = canvas.getContext("2d");
      if (context) {
        context.drawImage(bitmap, box.left, box.top, box.width, box.height, 0, 0, canvas.width, canvas.height);
        const colors = sampleTextColors(context.getImageData(0, 0, canvas.width, canvas.height).data);
        if (force || color === autoFilled.current.color) { setColor(colors.text); autoFilled.current.color = colors.text; }
        if (force || cover === autoFilled.current.cover) { setCover(colors.cover); autoFilled.current.cover = colors.cover; }
      }
      bitmap.close();
    } catch {
      // Colour sampling is a convenience; the pickers stay editable.
    }
  }
  async function chooseLine(line: DetectedTextLine) {
    if (readOnly || object.locked || !draftReady || working || busy) return;
    invalidate();
    setSelectedLine(line.id);
    setRegion(line.region);
    setSourceText(line.text);
    setText(line.text);
    setConfidence(line.confidence);
    await fillFromRegion(line.region, true);
    setOcrStatus("원문·영역·크기·색을 채웠습니다. 새 문구를 적고 확인한 뒤 적용하세요.");
  }
  async function cancelOCR() {
    ocrAttempt.current++;
    const running = worker.current;
    worker.current = null;
    if (running) await running.terminate();
    setOcrBusy(false);
    setOcrStatus("자동 인식을 중단했습니다. 직접 입력할 수 있습니다.");
  }
  async function getQuote() {
    if (readOnly || object.locked || !draftReady || working) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      validateImageRegion(region);
      regionPlacement(object, region);
      if (!confirmed || !sourceText.trim())
        throw new Error("지울 원문을 입력하고 확인해 주세요.");
      const expected = JSON.stringify(scene),
        input = inputKey;
      const revision = await saveCurrent();
      const result = await api<Quote>("/quotes", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          base_revision: revision,
          action: "image.edit.standard",
          requested_units: 1,
          prompt:
            "선택 영역의 글자를 지우고 기존 배경을 자연스럽게 복원해 주세요.",
          face_id: object.face_id,
          reference_asset_id: object.asset_id,
          input_data: {
            edit_mode: "remove_text",
            edit_region: region,
            confirmed_source_text: sourceText,
          },
        }),
      });
      setQuote(result);
      setSnapshot(expected);
      setBaseRevision(revision);
      setPreparedInput(input);
      setJob(undefined);
      jobKey.current = crypto.randomUUID();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function start() {
    if (!quote || readOnly || object.locked || (!pendingStart && (stale || !confirmed))) return;
    setBusy(true);
    setError("");
    const submitted = { ...latestDraft.current, pendingStart: true, quote, jobKey: jobKey.current };
    let sent = false;
    try {
      setPendingStart(true);
      if (!await writeToolDraft(draftKey, submitted))
        throw new Error("요청 번호를 보관하지 못해 유료 작업을 시작하지 않았습니다. 문구를 보관하고 중간 작업을 다시 불러오거나 브라우저 저장소를 확인하세요.");
      sent = true;
      const created = await api<Job>("/jobs", {
          method: "POST",
          headers: { "Idempotency-Key": jobKey.current },
          body: JSON.stringify({ quote_id: quote.id }),
        });
      // Persist even if the modal closed while the request was in flight.
      await writeToolDraft(draftKey, { ...submitted, pendingStart: false, quote: undefined, jobId: created.id });
      setJob(created);
      setPendingStart(false);
      setQuote(undefined);
    } catch (e) {
      const remainsPending = pendingAfterStartFailure(pendingStart, sent, e instanceof ApiError ? e.code : undefined);
      setPendingStart(remainsPending);
      if (sent && !remainsPending) {
        setPendingStart(false); setQuote(undefined);
        await writeToolDraft(draftKey, { ...submitted, pendingStart: false, quote: undefined });
      }
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function apply(useCover: boolean) {
    if (readOnly || object.locked || !draftReady || working) return;
    setBusy(true);
    setError("");
    try {
      validateImageRegion(region);
      if (!confirmed) throw new Error("원문과 선택 영역을 먼저 확인해 주세요.");
      if (!useCover && (!asset || !reviewed || stale))
        throw new Error(
          "제거 결과를 확인하고 현재 디자인과 일치하는지 확인해 주세요.",
        );
      const expected = useCover ? JSON.stringify(scene) : snapshot;
      const revision = useCover ? await saveCurrent() : baseRevision;
      await onApply(expected, revision, (current) =>
        applyImageText(
          current,
          object.id,
          region,
          { text, font_size_pt: fontSize, font_weight: weight, color },
          useCover ? { coverColor: cover } : { assetId: asset!.id },
          { text: crypto.randomUUID(), cover: crypto.randomUUID() },
        ),
      );
      completedDraft.current = true;
      setJob(undefined);
      setQuote(undefined);
      setSnapshot("");
      await writeToolDraft(draftKey, null);
      setResumeNotice("");
      setNotice(
        useCover
          ? "단색 덮개와 편집 가능한 문구를 한 번에 저장했습니다. 원본 픽셀은 그대로이며 덮개 레이어를 삭제하면 다시 보입니다."
          : "제거 결과와 편집 가능한 문구를 한 번에 저장했습니다. 문구 크기·줄바꿈·안전영역을 확인하세요.",
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="image-text-tools">
      <div className="alert alert-info">
        이미지 속 글자는 직접 타이핑해서 바뀌지 않습니다. 영역의 글자를 지운
        결과와 새 텍스트 레이어를 함께 적용합니다. AI가 지운 부분의 질감과 문구
        정확성은 직접 확인해야 합니다.
      </div>
      <Feedback error={error} notice={notice} />
      <Feedback notice={resumeNotice} error={draftWarning} />
      {draftWarning && <button className="button button-light" disabled={busy || working} onClick={() => { setDraftWarning(""); setDraftReload(n => n + 1); }}>보관된 최신 중간 작업 불러오기</button>}
      <p className="field-hint">영역·OCR 교정·새 문구·작업 번호는 이 기기에 30일간 보관됩니다. 다른 기기에는 자동으로 옮겨지지 않습니다.</p>
      {object.crop && <p className="field-hint">잘라낸 이미지입니다. 아래는 원본 좌표이며, 현재 자르기 영역 안의 글자만 선택하세요.</p>}
      {pendingStart && <div className="alert alert-info" role="status">이전 시작 요청의 응답을 확인하지 못했습니다. 같은 요청 번호로 상태를 확인하면 중복 생성·중복 차감하지 않습니다.
        <button className="button button-light" disabled={busy || readOnly || !draftReady} onClick={() => void start()}>이전 요청 이어서 확인</button>
      </div>}
      {stale && (
        <Feedback error="디자인 또는 입력이 변경되어 이전 견적·결과는 적용할 수 없습니다. 현재 상태에서 새 견적을 확인하세요." />
      )}
      <fieldset disabled={!draftReady || readOnly || !!object.locked} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
      <div className="image-tools-columns">
        <div>
          <h3>1. 지울 글자 영역 선택</h3>
          <p className="field-hint">
            원본 위를 드래그하거나 아래 좌표를 입력하세요. 숫자는 원본 이미지의
            비율(%)이며 편집기 회전과 별개입니다.
          </p>
          <div
            className="image-region-selector"
            onPointerDown={(e) => {
              if (readOnly || object.locked || !draftReady || working || busy || ocrBusy) return;
              e.currentTarget.setPointerCapture(e.pointerId);
              startPoint.current = point(e);
              invalidate();
            }}
            onPointerMove={(e) => {
              if (readOnly || object.locked || !draftReady) { startPoint.current = null; return; }
              if (startPoint.current)
                setRegion(regionFromPoints(startPoint.current, point(e)));
            }}
            onPointerUp={(e) => {
              if (readOnly || object.locked || !draftReady) { startPoint.current = null; return; }
              if (startPoint.current) {
                const next = regionFromPoints(startPoint.current, point(e));
                if (next.width > 0.001 && next.height > 0.001) { setRegion(next); void fillFromRegion(next); }
                startPoint.current = null;
              }
            }}
            onPointerCancel={() => {
              startPoint.current = null;
            }}
          >
            <img
              draggable={false}
              src={`/api/v1/assets/${object.asset_id}/content`}
              alt="글자를 지울 원본. 드래그로 영역 선택"
              onLoad={(e) =>
                setDimensions({
                  width: e.currentTarget.naturalWidth,
                  height: e.currentTarget.naturalHeight,
                })
              }
            />
            {detected.map((line) => (
              <button
                key={line.id}
                type="button"
                className={`image-text-hit${selectedLine === line.id ? " selected" : ""}`}
                title={line.text}
                aria-label={`글자 선택: ${line.text}`}
                disabled={busy || working || ocrBusy}
                style={{
                  left: `${line.region.x * 100}%`,
                  top: `${line.region.y * 100}%`,
                  width: `${line.region.width * 100}%`,
                  height: `${line.region.height * 100}%`,
                }}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => { e.stopPropagation(); void chooseLine(line); }}
              />
            ))}
            <div
              className="image-region-box"
              style={{
                left: `${region.x * 100}%`,
                top: `${region.y * 100}%`,
                width: `${region.width * 100}%`,
                height: `${region.height * 100}%`,
              }}
            />
          </div>
          <div className="button-row">
            <button
              className="button"
              disabled={ocrBusy || busy || working || !dimensions.width}
              onClick={() => void detectAll()}
            >
              이미지 속 글자 모두 찾기 · 무료
            </button>
          </div>
          {detected.length > 0 && (
            <ul className="image-text-lines" aria-label="찾은 글자 줄">
              {detected.map((line) => (
                <li key={line.id}>
                  <button
                    type="button"
                    className={`text-link${selectedLine === line.id ? " selected" : ""}`}
                    disabled={busy || working || ocrBusy}
                    onClick={() => void chooseLine(line)}
                  >
                    {line.text}
                  </button>
                  <span className="field-hint">{line.confidence.toFixed(0)}%</span>
                </li>
              ))}
            </ul>
          )}
          <div className="region-fields">
            {(
              [
                ["x", "왼쪽"],
                ["y", "위쪽"],
                ["width", "가로"],
                ["height", "세로"],
              ] as const
            ).map(([key, label]) => (
              <label className="field" key={key}>
                {label} %
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={0.1}
                  value={Math.round(region[key] * 10000) / 100}
                  disabled={busy || working || ocrBusy}
                  onChange={(e) => {
                    const next = { ...region, [key]: Number(e.target.value) / 100 };
                    setRegion(next);
                    void fillFromRegion(next);
                    invalidate();
                  }}
                />
              </label>
            ))}
          </div>
          <p className="field-hint">
            원본 {dimensions.width} × {dimensions.height} px · 선택 영역 약{" "}
            {Math.round(region.width * dimensions.width)} ×{" "}
            {Math.round(region.height * dimensions.height)} px
          </p>
          <button
            className="button button-light"
            disabled={ocrBusy || busy || working || !dimensions.width}
            onClick={() => void recognize()}
          >
            선택 영역 글자 읽기 · 무료 OCR
          </button>
          {ocrBusy && (
            <button className="text-link" onClick={() => void cancelOCR()}>
              자동 인식 중단
            </button>
          )}
          <p className="field-hint" role="status">
            {ocrStatus}
            {confidence !== undefined &&
              ` · 인식 신뢰도 ${confidence.toFixed(0)}% (정확성 보증 아님)`}
          </p>
          <p className="field-hint">
            자동 인식은 이 브라우저에서 실행됩니다. 이미지를 외부 OCR 서버에
            보내지 않습니다. 인식 도구를 불러오지 못해도 직접 입력할 수
            있습니다.
          </p>
        </div>
        <div>
          <h3>2. 원문 확인과 새 문구</h3>
          <label className="field">
            지울 원문 · OCR 결과를 수정할 수 있습니다
            <textarea
              rows={3}
              maxLength={4000}
              value={sourceText}
              disabled={busy || working || ocrBusy}
              onChange={(e) => {
                setSourceText(e.target.value);
                invalidate();
              }}
              placeholder="OCR 없이 원문을 직접 입력해도 됩니다."
            />
          </label>
          <label className="field">
            새 텍스트 레이어 내용
            <textarea
              rows={3}
              maxLength={4000}
              value={text}
              disabled={busy || working}
              onChange={(e) => {
                setText(e.target.value);
                setReviewed(false);
              }}
              placeholder="비워 두면 글자 제거만 적용합니다."
            />
          </label>
          <div className="form-row">
            <label className="field">
              글자 크기 pt
              <input
                type="number"
                min={4}
                max={200}
                value={fontSize}
                onChange={(e) => setFontSize(Number(e.target.value))}
              />
            </label>
            <label className="field">
              굵기
              <select
                value={weight}
                onChange={(e) => setWeight(Number(e.target.value) as 400 | 700)}
              >
                <option value={400}>보통</option>
                <option value={700}>굵게</option>
              </select>
            </label>
            <label className="field">
              글자색
              <input
                type="color"
                value={color}
                onChange={(e) => setColor(e.target.value)}
              />
            </label>
          </div>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
              disabled={!sourceText.trim() || working}
            />{" "}
            지울 원문과 영역을 실제 이미지와 대조했습니다.
          </label>
          <button
            className="button button-dark"
            disabled={!confirmed || busy || working || readOnly}
            onClick={() => void getQuote()}
          >
            AI 글자 제거 크레딧 견적
          </button>
          {quote && !pendingStart && (
            <div className="quote-confirmation">
              <strong>{quote.credit_total} 크레딧 · 글자 제거 1장</strong>
              <p>
                사용 가능 {quote.balance_before} → 예약 후 {quote.balance_after}
              </p>
              <p className="field-hint">
                {quote.provider_mode === "fixture"
                  ? "예시 모드 · 실제 AI 제거가 아닙니다."
                  : "선택 영역 밖은 서버에서 원본 픽셀을 보존합니다. 영역 안의 배경 복원 품질은 보장하지 않습니다."}{" "}
                견적 만료{" "}
                {new Date(quote.expires_at).toLocaleTimeString("ko-KR")}
              </p>
              <button
                className="button button-orange"
                disabled={
                  busy ||
                  readOnly ||
                  stale ||
                  !confirmed ||
                  Date.parse(quote.expires_at) < Date.now()
                }
                onClick={() => void start()}
              >
                견적 확인하고 제거 시작
              </button>
            </div>
          )}
          <details className="production-requirements">
            <summary>무료 대안 · 단색으로 덮고 문구 재배치</summary>
            <p className="field-hint">
              단색 배경에 적합합니다. 그림·무늬를 복원하지 않으며 원본 위에
              사각형 레이어를 덮습니다. 경계가 보일 수 있습니다.
            </p>
            <label className="field">
              덮개 배경색
              <input
                type="color"
                value={cover}
                onChange={(e) => setCover(e.target.value)}
              />
            </label>
            <button
              className="button button-light"
              disabled={busy || working || readOnly || !confirmed}
              onClick={() => void apply(true)}
            >
              단색 덮개와 새 문구 적용 · 무료
            </button>
          </details>
        </div>
      </div>
      {job && (
        <div className="ai-job-state">
          <strong>{labels[job.status] || job.status}</strong>
          <p>
            차감 {job.credit_charged ?? 0} · 반환 {job.credit_returned ?? 0}{" "}
            크레딧
          </p>
          {job.error && (
            <Feedback
              error={
                job.error
              }
            />
          )}
          <small>작업 번호 {job.id}</small>
          {job.cancelable && (
            <button
              className="button button-light"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void api<Job>(`/jobs/${job.id}/cancel`, {
                  method: "POST",
                  body: "{}",
                })
                  .then(setJob)
                  .catch((e) => setError(errorMessage(e)))
                  .finally(() => setBusy(false));
              }}
            >
              대기 작업 취소
            </button>
          )}
        </div>
      )}
      {asset && (
        <>
          <h3>3. 제거 결과를 확인하고 적용</h3>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={zoomResult}
              onChange={(e) => setZoomResult(e.target.checked)}
            />{" "}
            선택한 영역 확대 비교
          </label>
          <div className="image-compare">
            <figure>
              <ComparisonImage
                assetId={object.asset_id!}
                alt="글자 제거 전 원본"
                region={zoomResult ? region : undefined}
                dimensions={dimensions}
              />
              <figcaption>원본</figcaption>
            </figure>
            <figure>
              <ComparisonImage
                assetId={asset.id}
                alt="글자 제거 결과. 새 텍스트는 적용 후 별도 레이어로 추가"
                region={zoomResult ? region : undefined}
                dimensions={dimensions}
              />
              <figcaption>글자 제거 결과 · 새 문구는 적용할 때 추가</figcaption>
            </figure>
          </div>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={reviewed}
              onChange={(e) => setReviewed(e.target.checked)}
            />{" "}
            지울 글자가 남지 않았고 영역 안의 배경이 적절한지 확인했습니다.
          </label>
          <button
            className="button button-orange"
            disabled={busy || readOnly || stale || !reviewed || !confirmed}
            onClick={() => void apply(false)}
          >
            제거 이미지 + 편집 가능한 문구를 함께 저장
          </button>
          <p className="field-hint">
            원본은 보존됩니다. 적용은 실행 취소와 텍스트 변경 기록으로 확인할 수
            있습니다. 이전 AI 작업은 ‘AI 디자인 시안’의 생성 이력에서도 볼 수
            있습니다.
          </p>
        </>
      )}
      </fieldset>
    </div>
  );
}

function ComparisonImage({
  assetId,
  alt,
  region,
  dimensions,
}: {
  assetId: string;
  alt: string;
  region?: ImageRegion;
  dimensions: { width: number; height: number };
}) {
  if (!region || !region.width || !region.height || !dimensions.width)
    return <img src={`/api/v1/assets/${assetId}/content`} alt={alt} />;
  return (
    <div
      className="image-region-detail"
      style={{
        aspectRatio:
          (region.width * dimensions.width) /
          (region.height * dimensions.height),
      }}
    >
      <img
        src={`/api/v1/assets/${assetId}/content`}
        alt={alt}
        style={{
          width: `${100 / region.width}%`,
          left: `${(-region.x / region.width) * 100}%`,
          top: `${(-region.y / region.height) * 100}%`,
        }}
      />
    </div>
  );
}
