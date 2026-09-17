"use client";
import { useEffect, useRef, useState } from "react";
import {
  Check,
  ImagePlus,
  LoaderCircle,
  Sparkles,
  Square,
  RefreshCw,
} from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { Feedback } from "./management";
type Asset = {
  id: string;
  name?: string;
  source?: string;
  width_px?: number;
  height_px?: number;
  url?: string;
};
type Job = {
  id: string;
  status: string;
  cancelable?: boolean;
  credit_reserved?: number;
  credit_charged?: number;
  credit_returned?: number;
  error?: { message?: string } | string;
  result?: {
    assets?: Asset[];
    units?: Array<{ status: string; error?: string }>;
  };
};
type Quote = {
  id: string;
  credit_total: number;
  balance_before: number;
  balance_after: number;
  expires_at: string;
  provider_mode?: string;
  action: string;
  requested_units: number;
};
const terminal = [
  "succeeded",
  "partially_succeeded",
  "failed",
  "canceled",
  "reconciliation_required",
];
const statusLabels: Record<string, string> = {
  queued: "작업 대기",
  running: "시안 만드는 중",
  waiting_provider: "이미지 서비스 처리 중",
  validating: "결과 확인 중",
  succeeded: "완료",
  partially_succeeded: "일부 완료",
  failed: "실패",
  canceled: "취소됨",
  reconciliation_required: "사용량 확인 중",
};
export function AIStudio({
  projectId,
  faceId,
  referenceAssets,
  saveCurrent,
  onSelect,
  readOnly,
}: {
  projectId: string;
  faceId: string;
  referenceAssets: Asset[];
  saveCurrent: () => Promise<number>;
  onSelect: (asset: Asset) => void | Promise<void>;
  readOnly: boolean;
}) {
  const [prompt, setPrompt] = useState("");
  const [action, setAction] = useState("image.generate.standard");
  const [count, setCount] = useState(1);
  const [reference, setReference] = useState("");
  const [quote, setQuote] = useState<Quote>();
  const [job, setJob] = useState<Job>();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [provider, setProvider] = useState("");
  const [highEnabled, setHighEnabled] = useState(false);
  const [retry, setRetry] = useState(0);
  const key = useRef("");
  const edit = action === "image.edit.standard";
  useEffect(() => {
    api<{ provider: string; high: { enabled: boolean } }>("/ai/capabilities")
      .then((c) => {
        setProvider(c.provider);
        setHighEnabled(c.high.enabled);
      })
      .catch(() => {});
    api<{ items: Job[] }>(`/projects/${projectId}/generations`)
      .then((r) => {
        setJobs(r.items);
        const pending = r.items.find((j) => !terminal.includes(j.status));
        if (pending) setJob(pending);
      })
      .catch((e) => setError(errorMessage(e)));
  }, [projectId, retry]);
  useEffect(() => {
    if (!job || terminal.includes(job.status)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value = await api<Job>(`/jobs/${job!.id}`);
        if (!active) return;
        setJob(value);
        if (terminal.includes(value.status)) {
          setRetry((n) => n + 1);
          return;
        }
        timer = setTimeout(poll, 2000);
      } catch (e) {
        if (active) {
          setError(errorMessage(e));
          timer = setTimeout(poll, 6000);
        }
      }
    }
    timer = setTimeout(poll, 1500);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [job?.id, job?.status]);
  function change() {
    setQuote(undefined);
    setError("");
  }
  async function getQuote(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const base_revision = await saveCurrent();
      const result = await api<Quote>("/quotes", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          base_revision,
          action,
          requested_units: edit ? 1 : count,
          prompt,
          face_id: faceId,
          ...(edit ? { reference_asset_id: reference } : {}),
        }),
      });
      setQuote(result);
      key.current = crypto.randomUUID();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function start() {
    if (!quote) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<Job>("/jobs", {
        method: "POST",
        headers: { "Idempotency-Key": key.current },
        body: JSON.stringify({ quote_id: quote.id }),
      });
      setJob(result);
      setQuote(undefined);
      setNotice("");
      setRetry((n) => n + 1);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    if (!job) return;
    setBusy(true);
    try {
      const result = await api<Job>(`/jobs/${job.id}/cancel`, {
        method: "POST",
      });
      setJob(result);
      setRetry((n) => n + 1);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function selectAsset(asset: Asset) {
    setBusy(true);
    setError("");
    setQuote(undefined);
    try {
      await onSelect(asset);
      setNotice("현재 면에 원본 비율을 유지한 배경을 적용했습니다. 위에 있는 원본 이미지가 가린다면 레이어에서 숨겨 주세요. 텍스트와 원본 자산은 유지됩니다.");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const working = job && !terminal.includes(job.status);
  const gallery = jobs.filter((j) => j.result?.assets?.length);
  return (
    <div className="ai-studio">
      <div className="alert alert-info">
        <Sparkles size={18} />
        <span>
          {provider === "fixture"
            ? "자체 제작 예시를 사용하는 시험 모드입니다. 실제 AI 생성과 구분해 표시합니다."
            : provider === "openai"
              ? "배경과 일러스트는 AI로 만들고, 상품명과 표시사항은 별도 텍스트로 편집하세요."
              : "이미지 서비스 연결 상태는 견적 요청 시 확인합니다. 연결이 없으면 작업을 시작하지 않습니다."}
        </span>
      </div>
      <div className="ai-studio-layout">
        <form className="ai-prompt-form" onSubmit={getQuote}>
          <label className="field">
            작업 종류
            <select
              value={action}
              disabled={!!working || readOnly}
              onChange={(e) => {
                setAction(e.target.value);
                change();
              }}
            >
              <option value="image.generate.standard">표준 시안 만들기</option>
              <option value="image.generate.high" disabled={!highEnabled}>
                고해상도 시안 만들기{highEnabled ? "" : " · 제공 준비 중"}
              </option>
              <option value="image.edit.standard">기존 이미지 수정</option>
            </select>
          </label>
          {edit && (
            <label className="field">
              수정할 원본 이미지
              <select
                required
                value={reference}
                onChange={(e) => {
                  setReference(e.target.value);
                  change();
                }}
              >
                <option value="">선택하세요</option>
                {referenceAssets.map((a, i) => (
                  <option key={a.id} value={a.id}>
                    {a.name || `현재 면의 이미지 ${i + 1}`}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="field">
            원하는 분위기
            <textarea
              required
              minLength={5}
              maxLength={4000}
              rows={6}
              value={prompt}
              disabled={!!working || readOnly}
              onChange={(e) => {
                setPrompt(e.target.value);
                change();
              }}
              placeholder="예: 차분한 포레스트 그린 배경, 섬세한 찻잎 일러스트, 중앙에 상품명을 배치할 넓은 여백. 글자와 바코드는 제외."
            />
          </label>
          {!edit && (
            <label className="field">
              만들 시안 수
              <select
                value={count}
                disabled={!!working || readOnly}
                onChange={(e) => {
                  setCount(Number(e.target.value));
                  change();
                }}
              >
                {[1, 2, 3].map((n) => (
                  <option key={n} value={n}>
                    {n}장
                  </option>
                ))}
              </select>
            </label>
          )}
          <Feedback error={error} notice={notice} />
          <button
            className="button button-dark full-width"
            disabled={busy || !!working || readOnly || (edit && !reference)}
          >
            {busy ? (
              <LoaderCircle className="spin" size={17} />
            ) : (
              "크레딧 견적 확인"
            )}
          </button>
          <p className="field-hint">
            견적을 확인하고 승인하면 크레딧을 예약합니다. 성공한 결과만 차감하며
            원본 이미지는 유지합니다.
          </p>
          {quote && (
            <div className="quote-confirmation">
              <h3>
                {quote.requested_units}장 · {quote.credit_total} 크레딧
              </h3>
              <p>
                사용 가능 {quote.balance_before} → 예약 후 {quote.balance_after}
              </p>
              <p>
                견적 만료{" "}
                {new Date(quote.expires_at).toLocaleTimeString("ko-KR")}
              </p>
              <span className="pill">
                {quote.provider_mode || provider || "서버 설정"}
              </span>
              <button
                type="button"
                className="button button-orange full-width"
                onClick={() => void start()}
                disabled={
                  busy || new Date(quote.expires_at).getTime() < Date.now()
                }
              >
                확인하고 작업 시작
              </button>
            </div>
          )}
        </form>
        <div className="ai-results">
          {job && (
            <div className="ai-job-state">
              <div>
                {working ? (
                  <LoaderCircle className="spin" size={20} />
                ) : (
                  <Check size={20} />
                )}
                <strong>{statusLabels[job.status] || job.status}</strong>
              </div>
              <p>
                예약 {job.credit_reserved ?? 0} · 차감 {job.credit_charged ?? 0}{" "}
                · 반환 {job.credit_returned ?? 0} 크레딧
              </p>
              {job.error && (
                <Feedback
                  error={
                    typeof job.error === "string"
                      ? job.error
                      : job.error.message
                  }
                />
              )}
              <small>작업 번호 {job.id}</small>
              {job.result?.units && (
                <div className="ai-unit-status">
                  {job.result.units.map((unit, i) => (
                    <span className="pill" key={i}>
                      {i + 1}번 · {statusLabels[unit.status] || unit.status}
                    </span>
                  ))}
                </div>
              )}
              {job.cancelable && (
                <button
                  className="button button-light button-sm"
                  disabled={busy}
                  onClick={() => void cancel()}
                >
                  <Square size={13} /> 대기 중인 시안 취소
                </button>
              )}
            </div>
          )}
          <div className="management-section-heading">
            <h3>생성 결과</h3>
            <button
              className="icon-button"
              onClick={() => setRetry((n) => n + 1)}
              aria-label="생성 이력 새로고침"
            >
              <RefreshCw size={16} />
            </button>
          </div>
          {!gallery.length ? (
            <div className="management-empty">
              <ImagePlus size={29} />
              <p>완료된 시안이 여기에 표시됩니다.</p>
            </div>
          ) : (
            <div className="ai-result-grid">
              {gallery.flatMap((j) =>
                (j.result?.assets || []).map((asset) => (
                  <article key={asset.id}>
                    <img
                      src={`/api/v1/assets/${asset.id}/content`}
                      alt="생성된 패키지 배경 시안"
                      loading="lazy"
                    />
                    <span className="pill">
                      {asset.source === "fixture"
                        ? "자체 제작 예시"
                        : "AI 생성 이미지"}
                    </span>
                    <button
                      className="button button-light button-sm"
                      disabled={readOnly || busy}
                      onClick={() => void selectAsset(asset)}
                    >
                      이 면의 배경으로 적용
                    </button>
                  </article>
                )),
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
