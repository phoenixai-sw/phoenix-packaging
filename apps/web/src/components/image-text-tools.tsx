"use client";
import { useEffect, useRef, useState } from "react";
import type { Worker } from "tesseract.js";
import { api, errorMessage } from "@/lib/api";
import { Feedback } from "./management";
import {
  applyImageText,
  regionFromPoints,
  validateImageRegion,
  imageRegionPixels,
  textRemovalInputKey,
  type ImageRegion,
} from "@editor/image-tools";
import type { Scene, SceneObject } from "@editor/model";
import type { ApplyPreparedScene } from "./image-quality-tools";
type Quote = {
  id: string;
  credit_total: number;
  balance_before: number;
  balance_after: number;
  expires_at: string;
  provider_mode?: string;
};
type ResultAsset = {
  id: string;
  reference_asset_id?: string;
  edit_mode?: string;
  width_px?: number;
  height_px?: number;
};
type Job = {
  id: string;
  status: string;
  cancelable?: boolean;
  credit_charged?: number;
  credit_returned?: number;
  error?: { message?: string } | string;
  result?: { assets?: ResultAsset[] };
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
  const [region, setRegion] = useState<ImageRegion>({
    x: 0.1,
    y: 0.1,
    width: 0.8,
    height: 0.2,
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
  const [zoomResult, setZoomResult] = useState(true);
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
  const working = !!job && !terminal.includes(job.status);
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
  async function cancelOCR() {
    ocrAttempt.current++;
    const running = worker.current;
    worker.current = null;
    if (running) await running.terminate();
    setOcrBusy(false);
    setOcrStatus("자동 인식을 중단했습니다. 직접 입력할 수 있습니다.");
  }
  async function getQuote() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      validateImageRegion(region);
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
    if (!quote || stale || !confirmed) return;
    setBusy(true);
    setError("");
    try {
      setJob(
        await api<Job>("/jobs", {
          method: "POST",
          headers: { "Idempotency-Key": jobKey.current },
          body: JSON.stringify({ quote_id: quote.id }),
        }),
      );
      setQuote(undefined);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function apply(useCover: boolean) {
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
      setJob(undefined);
      setQuote(undefined);
      setSnapshot("");
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
      {stale && (
        <Feedback error="디자인 또는 입력이 변경되어 이전 견적·결과는 적용할 수 없습니다. 현재 상태에서 새 견적을 확인하세요." />
      )}
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
              if (working || busy || ocrBusy) return;
              e.currentTarget.setPointerCapture(e.pointerId);
              startPoint.current = point(e);
              invalidate();
            }}
            onPointerMove={(e) => {
              if (startPoint.current)
                setRegion(regionFromPoints(startPoint.current, point(e)));
            }}
            onPointerUp={(e) => {
              if (startPoint.current) {
                const next = regionFromPoints(startPoint.current, point(e));
                if (next.width > 0.001 && next.height > 0.001) setRegion(next);
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
                    setRegion({
                      ...region,
                      [key]: Number(e.target.value) / 100,
                    });
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
          {quote && (
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
                typeof job.error === "string" ? job.error : job.error.message
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
