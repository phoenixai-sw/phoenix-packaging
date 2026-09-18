"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Check,
  LoaderCircle,
  Sparkles,
  Square,
  RefreshCw,
} from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { uploadAsset } from "@/lib/assets";
import type { ApiSchema } from "@/lib/api-contract";
import styles from "./ai-layout-context.module.css";
import {
  DEFAULT_IMAGE_MODEL,
  DEFAULT_IMAGE_QUALITY,
  IMAGE_MODELS,
  IMAGE_QUALITIES,
  createImageQuoteGate,
  creditBalanceLabel,
  imageModelLabel,
  imageLayoutSummary,
  imageQualityLabel,
  initialImageSettings,
  imageQuoteBody,
  imageQuoteMatches,
  imageTier,
  type ImageMode,
  type ImageModel,
  type ImageQuality,
  type ImageQuoteTicket,
  type ImageSelection,
  type ImageSettings,
  type ImageLayoutContext,
} from "@/lib/ai-image-settings";
import { Feedback } from "./management";
import { Mascot } from "./brand";

type Asset = ApiSchema<"GeneratedAsset">;
type Job = ApiSchema<"AIGenerationJob">;
type Quote = ApiSchema<"QuoteData">;
type Capabilities = ApiSchema<"ImageCapabilities">;
type ConfirmedQuote = {
  value: Quote;
  ticket: ImageQuoteTicket;
  idempotencyKey: string;
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

function LayoutContextSummary({
  context,
}: {
  context?: ImageLayoutContext | null;
}) {
  const summary = imageLayoutSummary(context);
  if (!summary) return null;
  return (
    <div className={styles.context}>
      <strong>생성 지시에 반영</strong>
      <p>
        {summary.packageLabel} · {summary.faceLabel}
      </p>
      {summary.colors.length > 0 && (
        <div className={styles.palette} aria-label="자동 반영한 브랜드 색상">
          <span>브랜드 색상</span>
          {summary.colors.map((color) => (
            <span className={styles.color} key={color}>
              <i aria-hidden="true" style={{ backgroundColor: color }} />
              {color}
            </span>
          ))}
        </div>
      )}
      <p>{summary.quietMessage}</p>
      <small>AI 결과의 실제 색상과 여백은 적용 전에 확인해 주세요.</small>
    </div>
  );
}

function ImageSettingsSummary({ settings }: { settings: ImageSettings }) {
  const pixels =
    settings.output_width_px && settings.output_height_px
      ? `${settings.output_width_px.toLocaleString()} × ${settings.output_height_px.toLocaleString()} px`
      : settings.output_size;
  return (
    <>
      <dl className="ai-settings-summary">
        <div>
          <dt>모델</dt>
          <dd>GPT Image 2.5 {imageModelLabel(settings.model)}</dd>
        </div>
        <div>
          <dt>요청 품질</dt>
          <dd>{imageQualityLabel(settings.quality)}</dd>
        </div>
        {pixels && (
          <div>
            <dt>요청 크기</dt>
            <dd>{pixels}</dd>
          </div>
        )}
        {typeof settings.output_effective_ppi === "number" && (
          <div>
            <dt>면 전체 배치 시</dt>
            <dd>약 {settings.output_effective_ppi.toFixed(1)} PPI</dd>
          </div>
        )}
      </dl>
      <LayoutContextSummary context={settings.layout_context} />
    </>
  );
}

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
  referenceAssets: Array<Pick<Asset, "id"> & Partial<Pick<Asset, "name">>>;
  saveCurrent: () => Promise<number>;
  onSelect: (asset: Asset) => void | Promise<void>;
  readOnly: boolean;
}) {
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<ImageMode>("generate");
  const [model, setModel] = useState<ImageModel>(DEFAULT_IMAGE_MODEL);
  const [quality, setQuality] = useState<ImageQuality>(DEFAULT_IMAGE_QUALITY);
  const [count, setCount] = useState(1);
  const [reference, setReference] = useState("");
  // Reference photos uploaded here go to the asset library without being placed on the face.
  const [uploaded, setUploaded] = useState<Array<{ id: string; name: string }>>([]);
  const [uploading, setUploading] = useState(false);
  const [quote, setQuote] = useState<ConfirmedQuote>();
  const [job, setJob] = useState<Job>();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [capabilities, setCapabilities] = useState<Capabilities>();
  const [capabilityError, setCapabilityError] = useState("");
  const [capabilityRetry, setCapabilityRetry] = useState(0);
  const [wallet, setWallet] = useState<{ balance: number }>();
  const [walletLoading, setWalletLoading] = useState(true);
  const [walletError, setWalletError] = useState("");
  const [retry, setRetry] = useState(0);
  const [now, setNow] = useState(Date.now());
  const gate = useRef(createImageQuoteGate());
  const operation = useRef(false);
  const mounted = useRef(true);
  const settingsInitialized = useRef(false);
  const edit = mode === "edit";
  const selection: ImageSelection = {
    projectId,
    faceId,
    mode,
    model,
    quality,
    prompt,
    count,
    reference,
  };
  gate.current.update(selection);
  // Completed results can be re-edited directly, without placing them on the face first.
  const resultReferences = jobs.flatMap((item) =>
    (item.result?.assets || []).map((asset, index) => ({ id: asset.id, name: `생성 결과 · ${asset.name || `시안 ${index + 1}`}` })),
  );
  const referenceOptions = [...uploaded, ...referenceAssets, ...resultReferences].filter(
    (item, index, list) => list.findIndex((other) => other.id === item.id) === index,
  );
  const referenceAvailable =
    !edit || referenceOptions.some((asset) => asset.id === reference);
  if (readOnly || (edit && !referenceAvailable)) gate.current.invalidate();
  const modelOption = capabilities?.models.find((item) => item.id === model);
  const qualityOption = capabilities?.qualities.find(
    (item) => item.id === quality,
  );
  const settingsEnabled = !!(
    modelOption?.enabled &&
    qualityOption?.enabled &&
    qualityOption.action_tier === imageTier(quality) &&
    capabilities?.[mode]
  );
  const latest = useRef({
    selection,
    readOnly,
    referenceAvailable,
    settingsEnabled,
  });
  latest.current = { selection, readOnly, referenceAvailable, settingsEnabled };
  const working = !!job && !terminal.includes(job.status);
  const locked = busy || working || readOnly;
  const currentQuote =
    quote && gate.current.accepts(quote.ticket) ? quote.value : undefined;
  const expired =
    !!currentQuote && new Date(currentQuote.expires_at).getTime() <= now;
  const provider = capabilities?.provider;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      gate.current.invalidate();
    };
  }, []);
  useEffect(() => {
    let active = true;
    setCapabilityError("");
    api<Capabilities>("/ai/capabilities")
      .then((value) => {
        if (!active) return;
        if (!Array.isArray(value.models) || !Array.isArray(value.qualities))
          throw new Error(
            "모델·품질 설정을 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.",
          );
        if (!settingsInitialized.current) {
          const initial = initialImageSettings(value);
          setModel(initial.model);
          setQuality(initial.quality);
          settingsInitialized.current = true;
          gate.current.invalidate();
        }
        setCapabilities(value);
      })
      .catch((e) => {
        if (active) {
          setCapabilities(undefined);
          setCapabilityError(errorMessage(e));
        }
      });
    return () => {
      active = false;
    };
  }, [capabilityRetry]);
  useEffect(() => {
    let active = true;
    api<{ items: Job[] }>(`/projects/${projectId}/generations`)
      .then((result) => {
        if (!active) return;
        setJobs(result.items);
        const pending = result.items.find(
          (item) => !terminal.includes(item.status),
        );
        if (pending)
          setJob((current) => (current?.id === pending.id ? current : pending));
      })
      .catch((e) => {
        if (active) setError(errorMessage(e));
      });
    setWalletLoading(true);
    api<{ balance: number }>("/credits")
      .then((value) => {
        if (active) {
          setWallet(value);
          setWalletError("");
        }
      })
      .catch((e) => {
        if (active) {
          setWallet(undefined);
          setWalletError(errorMessage(e));
        }
      })
      .finally(() => {
        if (active) setWalletLoading(false);
      });
    return () => {
      active = false;
    };
  }, [projectId, retry]);
  useEffect(() => {
    if (!currentQuote) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [currentQuote?.id]);
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
    gate.current.invalidate();
    setQuote(undefined);
    setError("");
    setNotice("");
  }
  function beginOperation() {
    if (operation.current) return false;
    operation.current = true;
    setBusy(true);
    return true;
  }
  function finishOperation() {
    operation.current = false;
    if (mounted.current) setBusy(false);
  }
  function accepts(ticket: ImageQuoteTicket) {
    return (
      mounted.current &&
      gate.current.accepts(ticket) &&
      !latest.current.readOnly &&
      latest.current.referenceAvailable &&
      latest.current.settingsEnabled
    );
  }
  async function getQuote(event: React.FormEvent) {
    event.preventDefault();
    if (
      working ||
      readOnly ||
      !settingsEnabled ||
      !referenceAvailable ||
      !beginOperation()
    )
      return;
    const snapshot = { ...selection };
    const ticket = gate.current.begin();
    setQuote(undefined);
    setError("");
    setNotice("");
    try {
      const baseRevision = await saveCurrent();
      if (!accepts(ticket)) return;
      const result = await api<Quote>("/quotes", {
        method: "POST",
        body: JSON.stringify(imageQuoteBody(snapshot, baseRevision)),
      });
      if (!accepts(ticket)) return;
      if (!imageQuoteMatches(result, snapshot))
        throw new Error(
          "선택한 모델·품질과 견적이 일치하지 않습니다. 새 견적을 확인해 주세요.",
        );
      setNow(Date.now());
      setQuote({ value: result, ticket, idempotencyKey: crypto.randomUUID() });
    } catch (e) {
      if (accepts(ticket)) setError(errorMessage(e));
    } finally {
      finishOperation();
    }
  }
  async function start() {
    if (
      !quote ||
      working ||
      !accepts(quote.ticket) ||
      !imageQuoteMatches(quote.value, latest.current.selection)
    )
      return;
    if (new Date(quote.value.expires_at).getTime() <= Date.now()) {
      change();
      setError("견적이 만료되었습니다. 다시 확인해 주세요.");
      return;
    }
    if (!beginOperation()) return;
    setError("");
    try {
      const result = await api<Job>("/jobs", {
        method: "POST",
        headers: { "Idempotency-Key": quote.idempotencyKey },
        body: JSON.stringify({ quote_id: quote.value.id }),
      });
      if (!mounted.current) return;
      setJob(result);
      setQuote(undefined);
      gate.current.invalidate();
      setNotice("");
      setRetry((n) => n + 1);
    } catch (e) {
      if (mounted.current) setError(errorMessage(e));
    } finally {
      finishOperation();
    }
  }
  async function cancel() {
    if (!job || readOnly || !beginOperation()) return;
    setError("");
    try {
      const result = await api<Job>(`/jobs/${job.id}/cancel`, {
        method: "POST",
      });
      if (mounted.current) {
        setJob(result);
        setRetry((n) => n + 1);
      }
    } catch (e) {
      if (mounted.current) setError(errorMessage(e));
    } finally {
      finishOperation();
    }
  }
  async function selectAsset(asset: Asset) {
    if (working || readOnly || !beginOperation()) return;
    change();
    try {
      await onSelect(asset);
      if (mounted.current)
        setNotice(
          "현재 면에 원본 비율을 유지한 배경을 적용했습니다. 위에 있는 원본 이미지가 가린다면 레이어에서 숨겨 주세요. 텍스트와 원본 자산은 유지됩니다.",
        );
    } catch (e) {
      if (mounted.current) setError(errorMessage(e));
    } finally {
      finishOperation();
    }
  }
  const gallery = jobs.filter((item) => item.result?.assets?.length);
  return (
    <div className="ai-studio">
      <div className="alert alert-info">
        <Sparkles size={18} />
        <span>
          {provider === "fixture"
            ? "자체 제작 예시를 사용하는 시험 모드입니다. 실제 AI 생성과 구분해 표시합니다."
            : edit
              ? "이미지 전체를 수정합니다. 글자를 정확히 바꾸려면 ‘이미지 글자 편집’에서 영역을 지정하고 별도 텍스트로 적용하세요."
              : "배경과 일러스트는 AI로 만들고, 상품명과 표시사항은 별도 텍스트로 편집하세요."}
        </span>
      </div>
      <div className="ai-credit-wallet">
        <div>
          <span>사용 가능</span>
          <strong aria-live="polite">
            {walletLoading ? "확인 중…" : creditBalanceLabel(wallet?.balance)}
          </strong>
        </div>
        <Link href="/app/billing" target="_blank" rel="noopener noreferrer">
          크레딧·충전 / 결제 안내 ↗
        </Link>
        {!walletLoading && wallet?.balance === 0 && (
          <p>
            잔액이 0입니다. 작업을 시작하려면 크레딧이 필요합니다. 충전 가능
            여부는 결제 메뉴에서 확인하세요.
          </p>
        )}
        {walletError && (
          <p role="status">
            {walletError}{" "}
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={() => setRetry((n) => n + 1)}
            >
              잔액 다시 확인
            </button>
          </p>
        )}
      </div>
      {capabilityError && (
        <div className="ai-capability-error">
          <Feedback error={capabilityError} />
          <button
            type="button"
            className="button button-light button-sm"
            onClick={() => setCapabilityRetry((n) => n + 1)}
          >
            모델 설정 다시 확인
          </button>
        </div>
      )}
      <div className="ai-studio-layout">
        <form className="ai-prompt-form" onSubmit={getQuote}>
          <label className="field">
            작업 종류
            <select
              value={mode}
              disabled={locked}
              onChange={(e) => {
                setMode(e.target.value as ImageMode);
                change();
              }}
            >
              <option value="generate">이미지 생성</option>
              <option value="edit">이미지 수정</option>
            </select>
          </label>
          <label className="field">
            모델
            <select
              value={model}
              disabled={locked || !capabilities}
              onChange={(e) => {
                setModel(e.target.value as ImageModel);
                change();
              }}
            >
              {IMAGE_MODELS.map((id) => {
                const option = capabilities?.models.find(
                  (item) => item.id === id,
                );
                return (
                  <option key={id} value={id} disabled={!option?.enabled}>
                    {imageModelLabel(id)} ·{" "}
                    {id.endsWith("sunburst") ? "정밀 편집" : "빠른 시안"}
                    {option && !option.enabled ? " · 사용 불가" : ""}
                  </option>
                );
              })}
            </select>
            <small className="field-hint">
              GPT Image 2.5 ·{" "}
              {modelOption?.description ||
                "연결된 모델 설정을 확인하고 있습니다."}
            </small>
          </label>
          <label className="field">
            품질
            <select
              value={quality}
              disabled={locked || !capabilities}
              onChange={(e) => {
                setQuality(e.target.value as ImageQuality);
                change();
              }}
            >
              {IMAGE_QUALITIES.map((id) => {
                const option = capabilities?.qualities.find(
                  (item) => item.id === id,
                );
                return (
                  <option key={id} value={id} disabled={!option?.enabled}>
                    {imageQualityLabel(id)}
                    {option ? ` · ${option.credit_cost} 크레딧/장` : ""}
                    {option && !option.enabled ? " · 사용 불가" : ""}
                  </option>
                );
              })}
            </select>
          </label>
          <p className="field-hint ai-quality-note">
            {quality === "auto"
              ? "Auto는 선택한 모델이 품질을 결정하며 장당 20크레딧입니다. 자동 선택에 따른 비용 절감은 보장하지 않습니다."
              : "Low·Medium·High는 장당 10크레딧, XHigh·Max는 장당 20크레딧입니다."}{" "}
            체험 크레딧은 Low·Medium·High에서 사용할 수 있습니다.
          </p>
          <p className="field-hint ai-quality-note">
            품질을 높여도 300 PPI가 보장되지는 않습니다. 인쇄 밀도는 이미지 픽셀
            수와 실제 배치 크기로 결정됩니다.
          </p>
          {edit && (
            <label className="field">
              수정할 원본 이미지
              <select
                required
                value={reference}
                disabled={locked}
                onChange={(e) => {
                  setReference(e.target.value);
                  change();
                }}
              >
                <option value="">선택하세요</option>
                {referenceOptions.map((asset, index) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.name || `현재 면의 이미지 ${index + 1}`}
                  </option>
                ))}
              </select>
            </label>
          )}
          {edit && (
            <label className="field">
              참고 이미지 올리기 (면에 배치하지 않고 수정 원본으로만 사용)
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                disabled={locked || uploading}
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (!file) return;
                  setUploading(true);
                  setError("");
                  try {
                    const asset = await uploadAsset(file, projectId);
                    if (!mounted.current) return;
                    const name = asset.name || file.name;
                    setUploaded((list) => [{ id: asset.id, name }, ...list.filter((item) => item.id !== asset.id)]);
                    setReference(asset.id);
                    change();
                    setNotice(`참고 이미지 "${name}"을(를) 보관함에 올리고 수정 원본으로 선택했습니다.`);
                  } catch (err) {
                    if (mounted.current) setError(errorMessage(err));
                  } finally {
                    if (mounted.current) setUploading(false);
                  }
                }}
              />
            </label>
          )}
          <label className="field">
            {edit ? "수정할 내용" : "원하는 분위기"}
            <textarea
              required
              minLength={5}
              maxLength={4000}
              rows={5}
              value={prompt}
              disabled={locked}
              onChange={(e) => {
                setPrompt(e.target.value);
                change();
              }}
              placeholder={
                edit
                  ? "예: 나머지 디자인은 유지하고 배경의 녹색을 차분한 보라색으로 바꿔 주세요."
                  : "예: 차분한 포레스트 그린 배경, 섬세한 찻잎 일러스트, 중앙에 상품명을 배치할 넓은 여백. 글자와 바코드는 제외."
              }
            />
          </label>
          {!edit && (
            <label className="field">
              만들 시안 수
              <select
                value={count}
                disabled={locked}
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
          {!capabilities && !capabilityError && (
            <p className="field-hint" role="status">
              모델·품질 설정을 불러오는 중입니다.
            </p>
          )}
          {capabilities && !settingsEnabled && (
            <p className="field-hint" role="status">
              선택한 작업·모델·품질은 현재 사용할 수 없습니다.
            </p>
          )}
          <Feedback error={error} notice={notice} />
          <button
            className="button button-dark full-width"
            disabled={locked || !settingsEnabled || !referenceAvailable}
          >
            {busy ? (
              <>
                <LoaderCircle className="spin" size={17} /> 처리 중
              </>
            ) : (
              "크레딧 견적 확인"
            )}
          </button>
          <p className="field-hint">
            견적 확인 후 승인하면 크레딧을 예약합니다. 성공한 결과만 차감하며
            원본 이미지는 유지합니다.
          </p>
          {currentQuote && (
            <div className="quote-confirmation" aria-live="polite">
              <h3>
                {currentQuote.requested_units}장 · {currentQuote.credit_total}{" "}
                크레딧
              </h3>
              {currentQuote.image_settings && <ImageSettingsSummary settings={currentQuote.image_settings} />}
              {currentQuote.image_settings?.quality === "auto" && (
                <p>
                  자동 품질 · 장당 20크레딧 고정. 실제 품질은 결과에 제공된 경우
                  별도로 표시합니다.
                </p>
              )}
              <p>
                출력 크기와 인쇄 밀도는 요청 기준 예상값이며 결과 이미지에서
                다시 확인하세요.
              </p>
              <p>
                이 작업에 사용 가능 {currentQuote.balance_before} → 예약 후{" "}
                {currentQuote.balance_after}
              </p>
              <p>
                {expired
                  ? "견적이 만료되었습니다. 새 견적을 확인하세요."
                  : `견적 만료 ${new Date(currentQuote.expires_at).toLocaleTimeString("ko-KR")}`}
              </p>
              <span className="pill">
                {(currentQuote.provider_mode || provider) === "fixture"
                  ? "시험 이미지"
                  : "AI 이미지 작업"}
              </span>
              <button
                type="button"
                className="button button-orange full-width"
                onClick={() => void start()}
                disabled={
                  locked || expired || !settingsEnabled || !referenceAvailable
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
              {job.image_settings && (
                <ImageSettingsSummary settings={job.image_settings} />
              )}
              <p>
                예약 {job.credit_reserved ?? 0} · 차감 {job.credit_charged ?? 0}{" "}
                · 반환 {job.credit_returned ?? 0} 크레딧
              </p>
              {job.error && (
                <Feedback
                  error={
                    job.error
                  }
                />
              )}
              <small>작업 번호 {job.id}</small>
              {job.result?.units && (
                <div className="ai-unit-status">
                  {job.result.units.map((unit, index) => (
                    <span className="pill" key={index}>
                      {index + 1}번 · {statusLabels[unit.status] || unit.status}
                    </span>
                  ))}
                </div>
              )}
              {job.cancelable && (
                <button
                  className="button button-light button-sm"
                  disabled={busy || readOnly}
                  onClick={() => void cancel()}
                >
                  <Square size={13} /> 대기 중인 시안 취소
                </button>
              )}
            </div>
          )}
          <div className="management-section-heading">
            <h3>생성·수정 이력</h3>
            <button
              className="icon-button"
              disabled={busy}
              onClick={() => setRetry((n) => n + 1)}
              aria-label="생성 이력과 크레딧 새로고침"
            >
              <RefreshCw size={16} />
            </button>
          </div>
          {!gallery.length ? (
            <div className="management-empty">
              <Mascot size={110} />
              <p>완료된 시안이 여기에 표시됩니다.</p>
            </div>
          ) : (
            <div className="ai-result-grid">
              {gallery.flatMap((item) =>
                (item.result?.assets || []).map((asset) => (
                  <article key={asset.id}>
                    <img
                      src={`/api/v1/assets/${asset.id}/content`}
                      alt="생성·수정된 패키지 이미지 시안"
                      loading="lazy"
                    />
                    <span className="pill">
                      {asset.source === "fixture"
                        ? "자체 제작 예시"
                        : "AI 이미지"}
                    </span>
                    {(asset.model || item.image_settings?.model) && (
                      <div className="ai-result-metadata">
                        <strong>
                          {imageModelLabel(
                            asset.model || item.image_settings?.model,
                          )}
                        </strong>
                        <span>
                          요청{" "}
                          {imageQualityLabel(
                            asset.requested_quality ||
                              item.image_settings?.quality,
                          )}
                        </span>
                        <span>
                          실제 품질 {imageQualityLabel(asset.actual_quality)}
                          {!asset.actual_quality ? " (공급자 미반환)" : ""}
                        </span>
                        {asset.width_px && asset.height_px ? (
                          <span>
                            실제 {asset.width_px.toLocaleString()} ×{" "}
                            {asset.height_px.toLocaleString()} px
                          </span>
                        ) : asset.actual_size ? (
                          <span>실제 {asset.actual_size} px</span>
                        ) : null}
                      </div>
                    )}
                    <LayoutContextSummary
                      context={item.image_settings?.layout_context}
                    />
                    <button
                      className="button button-light button-sm"
                      disabled={locked}
                      onClick={() => void selectAsset(asset)}
                    >
                      이 면의 배경으로 적용
                    </button>
                    <button
                      className="button button-light button-sm"
                      disabled={locked}
                      onClick={() => {
                        setMode("edit");
                        setReference(asset.id);
                        change();
                        setNotice("이 결과를 수정 원본으로 선택했습니다. 바꿀 내용을 적고 견적을 확인해 주세요.");
                      }}
                    >
                      이 결과를 다시 수정
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
