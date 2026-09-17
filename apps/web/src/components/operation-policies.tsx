"use client";
import { useState } from "react";
import styles from "./operation-policies.module.css";
import type { components } from "../../../../packages/contracts/api.generated";
import { ManagementPage, Feedback, Loading } from "./management";
import { useApiData, dateTime, money } from "@/lib/business";
import { api, errorMessage } from "@/lib/api";
type Overview = components["schemas"]["PolicyOverview"];
type Version = components["schemas"]["PolicyDTO"];
type Wallet = components["schemas"]["WalletDTO"];
const actionLabels: Record<string, string> = {
  "image.generate.standard": "표준 이미지 생성",
  "image.edit.standard": "표준 이미지 수정",
  "image.generate.high": "상위 품질 생성",
  "image.edit.high": "상위 품질 수정",
  "export.production.first": "첫 제작 출력",
  "export.production.repeat": "동일 제작 재출력",
  "export.review": "검토 출력",
  "editor.manual": "수동 편집",
  "preview.all_faces": "전체 면 미리보기",
};
const scopeLabel=(value:string)=>value==="paid"?"유료 작업 범위":"체험 범위";
const bucketLabel=(value:string)=>({adjustment:"관리자 정정",trial:"무료 체험",monthly:"월 지급",purchase:"추가 구매",compensation:"실패 복원"}[value]||"기존 지급");
export function OperationPolicies() {
  const query = useApiData<Overview>("/admin/policies");
  const [pricing, setPricing] = useState<Overview["pricing"]>();
  const [image, setImage] = useState<Overview["image"]>();
  const [kind, setKind] = useState<"pricing" | "image">("pricing");
  const [reason, setReason] = useState("");
  const [draft, setDraft] = useState<Version>();
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tenant, setTenant] = useState("");
  const [wallet, setWallet] = useState<Wallet>();
  const [corrections, setCorrections] = useState<
    components["schemas"]["CorrectionDTO"][]
  >([]);
  const [amount, setAmount] = useState(0);
  const [scope, setScope] = useState<"standard_only" | "paid">("standard_only");
  const [bucket, setBucket] = useState("");
  const [days, setDays] = useState(7);
  const [creditReason, setCreditReason] = useState("");
  const [creditAck, setCreditAck] = useState(false);
  const [correctionKey, setCorrectionKey] = useState("");
  async function act(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  function begin(which: "pricing" | "image", version?: Version) {
    setKind(which);
    setDraft(undefined);
    setAck(false);
    setReason("");
    if (which === "pricing")
      setPricing(
        structuredClone(
          (version?.payload || query.data!.pricing) as Overview["pricing"],
        ),
      );
    else
      setImage(
        structuredClone(
          (version?.payload || query.data!.image) as Overview["image"],
        ),
      );
  }
  async function inspectWallet() {
    const w = await api<Wallet>(
      `/admin/credit-wallets/${encodeURIComponent(tenant.trim())}`,
    );
    setWallet(w);
    setCorrections(
      (
        await api<{ items: components["schemas"]["CorrectionDTO"][] }>(
          `/admin/credit-corrections?tenant_id=${w.tenant_id}`,
        )
      ).items,
    );
    setCreditAck(false);
    setCorrectionKey("");
  }
  const data = query.data;
  if (!data)
    return (
      <ManagementPage
        eyebrow="POLICIES"
        title="요금·모델·크레딧 정책"
        description="운영 정책과 정정 이력을 관리합니다."
      >
        <Feedback error={query.error} />
        <Loading />
      </ManagementPage>
    );
  return (
    <ManagementPage
      eyebrow="POLICIES"
      title="요금·모델·크레딧 정책"
      description="변경 사유와 새 버전을 남깁니다. 기존 주문·구독의 동의 조건은 유지합니다."
    >
      <Feedback error={error || query.error} notice={notice || data.notice} />
      <section className={`management-card ${styles.card}`}>
        <h2>현재 적용 정책</h2>
        <p>요금 버전: {data.pricing_version}</p>
        <p>
          기본 이미지 모델: {data.image.default_model} · 허용 품질:{" "}
          {data.image.allowed_qualities.join(", ")}
        </p>
        <p>
          공급자 일 예산 ${data.image.daily_limit_usd} · 비용 미확인 요청당 확보
          ${data.image.unknown_request_allowance_usd}
        </p>
        <div className="management-actions">
          <button
            className="button button-light"
            disabled={busy}
            onClick={() => begin("pricing")}
          >
            요금 변경 초안
          </button>
          <button
            className="button button-light"
            disabled={busy}
            onClick={() => begin("image")}
          >
            모델 변경 초안
          </button>
        </div>
      </section>
      {(pricing || image) && (
        <section className={`management-card ${styles.card}`}>
          <h2>{kind === "pricing" ? "요금" : "모델"} 정책 초안</h2>
          {!draft && kind === "pricing" && pricing && (
            <>
              <p>
                금액은 공급가 입력 시 부가세 포함 금액을 계산합니다.
                좌석·체험·무료 편집 정책은 유지합니다.
              </p>
              {pricing.plans.map((p, index) => (
                <div className="form-grid" key={p.id}>
                  <strong>{p.name}</strong>
                  <label>
                    월 공급가
                    <input
                      aria-label={`${p.name} 월 공급가`}
                      type="number"
                      min="1"
                      value={p.monthly_ex_vat}
                      onChange={(e) => {
                        const n = Number(e.target.value);
                        setPricing({
                          ...pricing,
                          plans: pricing.plans.map((v, i) =>
                            i === index
                              ? {
                                  ...v,
                                  monthly_ex_vat: n,
                                  monthly_inc_vat: Math.round(n * 1.1),
                                }
                              : v,
                          ),
                        });
                      }}
                    />
                  </label>
                  <label>
                    월 크레딧
                    <input
                      aria-label={`${p.name} 월 크레딧`}
                      type="number"
                      min="1"
                      value={p.credits}
                      onChange={(e) =>
                        setPricing({
                          ...pricing,
                          plans: pricing.plans.map((v, i) =>
                            i === index
                              ? { ...v, credits: Number(e.target.value) }
                              : v,
                          ),
                        })
                      }
                    />
                  </label>
                  <p>
                    {money(p.monthly_inc_vat)} · {p.seats}명
                  </p>
                </div>
              ))}
              {pricing.topups.map((p, index) => (
                <label key={p.credits}>
                  {p.credits} 크레딧 충전 공급가
                  <input
                    type="number"
                    min="1"
                    value={p.ex_vat}
                    onChange={(e) => {
                      const n = Number(e.target.value);
                      setPricing({
                        ...pricing,
                        topups: pricing.topups.map((v, i) =>
                          i === index
                            ? { ...v, ex_vat: n, inc_vat: Math.round(n * 1.1) }
                            : v,
                        ),
                      });
                    }}
                  />
                </label>
              ))}
              <div className="form-grid">
                {Object.entries(pricing.actions).map(([key, value]) => (
                  <label key={key}>
                    {actionLabels[key]}
                    <input
                      type="number"
                      min={value === 0 ? 0 : 1}
                      disabled={data.pricing.actions[key] === 0}
                      value={value}
                      onChange={(e) =>
                        setPricing({
                          ...pricing,
                          actions: {
                            ...pricing.actions,
                            [key]: Number(e.target.value),
                          },
                        })
                      }
                    />
                  </label>
                ))}
              </div>
            </>
          )}
          {!draft && kind === "image" && image && (
            <>
              <label>
                기본 모델
                <select
                  value={image.default_model}
                  onChange={(e) =>
                    setImage({
                      ...image,
                      default_model: e.target
                        .value as typeof image.default_model,
                    })
                  }
                >
                  {image.allowed_models.map((m) => (
                    <option key={m}>{m}</option>
                  ))}
                </select>
              </label>
              <fieldset>
                <legend>사용 허용 모델</legend>
                {(
                  ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"] as const
                ).map((m) => (
                  <label key={m}>
                    <input
                      type="checkbox"
                      checked={image.allowed_models.includes(m)}
                      onChange={(e) =>
                        setImage({
                          ...image,
                          allowed_models: e.target.checked
                            ? [...image.allowed_models, m]
                            : image.allowed_models.filter((v) => v !== m),
                        })
                      }
                    />
                    {m}
                  </label>
                ))}
              </fieldset>
              <fieldset>
                <legend>사용 허용 품질</legend>
                {(
                  ["low", "medium", "high", "xhigh", "max", "auto"] as const
                ).map((q) => (
                  <label key={q}>
                    <input
                      type="checkbox"
                      disabled={q === "high"}
                      checked={image.allowed_qualities.includes(q)}
                      onChange={(e) =>
                        setImage({
                          ...image,
                          allowed_qualities: e.target.checked
                            ? [...image.allowed_qualities, q]
                            : image.allowed_qualities.filter((v) => v !== q),
                        })
                      }
                    />
                    {q}
                  </label>
                ))}
              </fieldset>
              <label>
                <input
                  type="checkbox"
                  checked={image.high_enabled}
                  onChange={(e) =>
                    setImage({ ...image, high_enabled: e.target.checked })
                  }
                />
                상위 품질 사용 허용 (서버 배포 설정 내에서 적용)
              </label>
              <div className="form-grid">
                {(
                  [
                    ["daily_limit_usd", "일 예산 USD"],
                    [
                      "unknown_request_allowance_usd",
                      "비용 미확인 요청 확보 USD",
                    ],
                    ["monthly_alert_usd", "월 경고 USD"],
                  ] as const
                ).map(([key, label]) => (
                  <label key={key}>
                    {label}
                    <input
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={image[key]}
                      onChange={(e) =>
                        setImage({ ...image, [key]: Number(e.target.value) })
                      }
                    />
                  </label>
                ))}
              </div>
              <p>배포한 서버 예산보다 완화하는 설정은 저장되지 않습니다.</p>
            </>
          )}
          <label>
            변경 사유
            <textarea
              value={reason}
              minLength={5}
              maxLength={1000}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          {!draft ? (
            <button
              className="button button-dark"
              disabled={busy || reason.trim().length < 5}
              onClick={() =>
                void act(async () => {
                  setDraft(
                    await api<Version>("/admin/policies", {
                      method: "POST",
                      body: JSON.stringify({
                        kind,
                        reason,
                        payload: kind === "pricing" ? pricing : image,
                      }),
                    }),
                  );
                  setAck(false);
                  setNotice(
                    "초안을 저장했습니다. 변경 내용을 확인한 뒤 게시하세요.",
                  );
                })
              }
            >
              초안 저장·검토
            </button>
          ) : (
            <>
              <h3>게시 전 변경 내용</h3>
              <PolicyDiff
                before={kind === "pricing" ? data.pricing : data.image}
                after={draft.payload}
              />
              <p>초안 버전: {draft.version}</p>
              <label>
                <input
                  type="checkbox"
                  checked={ack}
                  onChange={(e) => setAck(e.target.checked)}
                />
                새 견적·새 주문에 적용되며 기존 구독·주문과 원장은 유지됨을
                확인했습니다.
              </label>
              <button
                className="button button-dark"
                disabled={busy || !ack || reason.trim().length < 5}
                onClick={() =>
                  void act(async () => {
                    await api(`/admin/policies/${draft.id}/publish`, {
                      method: "POST",
                      body: JSON.stringify({
                        expected_active_id:
                          kind === "pricing"
                            ? data.active_pricing_id
                            : data.active_image_id,
                        reason,
                        understands_existing_subscriptions_unchanged: true,
                      }),
                    });
                    setDraft(undefined);
                    setPricing(undefined);
                    setImage(undefined);
                    query.refresh();
                    setNotice(
                      "새 정책을 게시했습니다. 실제 결제와 제조 승인 게이트는 그대로 유지됩니다.",
                    );
                  })
                }
              >
                이 버전 게시
              </button>
            </>
          )}
        </section>
      )}
      <section className={`management-card ${styles.card}`}>
        <h2>정책 변경 이력</h2>
        {data.versions.length === 0 ? (
          <p>게시한 추가 정책이 없습니다. 기본 배포 정책을 사용합니다.</p>
        ) : (
          data.versions.map((v) => (
            <details key={v.id}>
              <summary>
                {v.kind === "pricing" ? "요금" : "모델"} · {v.version} ·{" "}
                {v.active ? "현재 적용" : v.published_at ? "이전 버전" : "초안"}{" "}
                · {dateTime(v.created_at)}
              </summary>
              <p>{v.reason}</p>
              <PolicyDiff
                before={v.kind === "pricing" ? data.pricing : data.image}
                after={v.payload}
              />
              <button
                className="button button-light"
                disabled={busy}
                onClick={() => begin(v.kind, v)}
              >
                이 내용으로 새 초안
              </button>
            </details>
          ))
        )}
      </section>
      <section className={`management-card ${styles.card}`}>
        <h2>크레딧 정정</h2>
        <p>
          대상 작업 공간을 조회한 후 사유를 기록합니다. 회수는 선택 지급분의
          유효한 미사용 잔액에만 적용됩니다.
        </p>
        <label>
          작업 공간 ID
          <input
            value={tenant}
            onChange={(e) => {
              setTenant(e.target.value);
              setWallet(undefined);
              setCreditAck(false);
            }}
          />
        </label>
        <button
          className="button button-light"
          disabled={busy || !tenant.trim()}
          onClick={() => void act(inspectWallet)}
        >
          대상 잔액 조회
        </button>
        {wallet && (
          <>
            <h3>{wallet.tenant_name}</h3>
            <p>
              잔액 {wallet.summary.balance} · 예약 {wallet.summary.reserved} ·
              사용 {wallet.summary.consumed} 크레딧
            </p>
            <fieldset
              disabled={busy}
              onChange={() => {
                setCreditAck(false);
                setCorrectionKey("");
              }}
            >
              <label>
                정정 수량 (지급 + / 회수 −)
                <input
                  type="number"
                  min="-100000"
                  max="100000"
                  value={amount}
                  onChange={(e) => setAmount(Number(e.target.value))}
                />
              </label>
              <label>
                사용 범위
                <select
                  value={scope}
                  onChange={(e) => setScope(e.target.value as typeof scope)}
                >
                  <option value="standard_only">체험 범위</option>
                  <option value="paid">유료 작업 범위</option>
                </select>
              </label>
              {amount < 0 ? (
                <label>
                  회수할 지급분
                  <select
                    value={bucket}
                    onChange={(e) => {
                      setBucket(e.target.value);
                      const b = wallet.summary.buckets.find(
                        (v) => v.id === e.target.value,
                      );
                      if (b) setScope(b.scope as typeof scope);
                    }}
                  >
                    <option value="">선택하세요</option>
                    {wallet.summary.buckets
                      .filter((b) => b.available > 0)
                      .map((b) => (
                        <option key={b.id} value={b.id}>
                            {bucketLabel(b.kind)} · {b.available} · {scopeLabel(b.scope)} ·{" "}
                          {dateTime(b.expires_at)}
                        </option>
                      ))}
                  </select>
                </label>
              ) : (
                <label>
                  유효기간 (일)
                  <input
                    type="number"
                    min="1"
                    max="365"
                    value={days}
                    onChange={(e) => setDays(Number(e.target.value))}
                  />
                </label>
              )}
              <label>
                정정 사유
                <textarea
                  value={creditReason}
                  minLength={5}
                  maxLength={500}
                  onChange={(e) => setCreditReason(e.target.value)}
                />
              </label>
            </fieldset>
            <label>
              <input
                type="checkbox"
                checked={creditAck}
                onChange={(e) => setCreditAck(e.target.checked)}
              />
              {wallet.tenant_name}에 {amount > 0 ? "지급" : "회수"}{" "}
              {Math.abs(amount)} 크레딧을 기록합니다.
            </label>
            <button
              className="button button-dark"
              disabled={
                busy ||
                !creditAck ||
                !amount ||
                creditReason.trim().length < 5 ||
                (amount < 0 && !bucket)
              }
              onClick={() =>
                void act(async () => {
                  const key = correctionKey || crypto.randomUUID();
                  setCorrectionKey(key);
                  await api("/admin/credit-corrections", {
                    method: "POST",
                    headers: { "Idempotency-Key": key },
                    body: JSON.stringify({
                      tenant_id: wallet.tenant_id,
                      amount,
                      scope,
                      bucket_id: amount < 0 ? bucket : null,
                      expires_days: days,
                      reason: creditReason,
                    }),
                  });
                  await inspectWallet();
                  setAmount(0);
                  setCreditReason("");
                  setNotice("정정과 사유를 새 원장 기록으로 저장했습니다.");
                })
              }
            >
              확인한 정정 적용
            </button>
            <h3>관리자 정정 이력</h3>
            {corrections.map((c) => (
              <p key={c.id}>
                {dateTime(c.created_at)} · {c.amount > 0 ? "+" : ""}
                {c.amount} · {scopeLabel(c.scope)} · {c.reason}
              </p>
            ))}
          </>
        )}
      </section>
    </ManagementPage>
  );
}
function PolicyDiff({ before, after }: { before: unknown; after: unknown }) {
  const fieldLabels:Record<string,string>={monthly_ex_vat:"월 공급가",monthly_inc_vat:"월 결제액 (VAT 포함)",credits:"크레딧",seats:"좌석",ex_vat:"공급가",inc_vat:"결제액 (VAT 포함)",expires_months:"유효 개월",name:"표시 이름",default_model:"기본 모델",allowed_models:"허용 모델",allowed_qualities:"허용 품질",high_enabled:"상위 품질 사용",daily_limit_usd:"일 예산 USD",unknown_request_allowance_usd:"미확인 요청 확보 USD",monthly_alert_usd:"월 경고 USD"};
  const label=(key:string)=>{
    if(key.startsWith("actions."))return actionLabels[key.slice(8)]||"작업 단가";
    const parts=key.split(".");
    if(parts[0]==="plans")return `${["Starter","Pro","Partner"][Number(parts[1])]} ${fieldLabels[parts[2]]||"설정"}`;
    if(parts[0]==="topups")return `${[500,1000][Number(parts[1])]} 크레딧 충전 ${fieldLabels[parts[2]]||"설정"}`;
    return fieldLabels[parts[0]]||"정책 설정";
  };
  const flatten = (v: unknown, p = ""): Record<string, string> =>
    v !== null && typeof v === "object"
      ? Object.entries(v).reduce(
          (a, [k, x]) => ({ ...a, ...flatten(x, p ? `${p}.${k}` : k) }),
          {},
        )
      : { [p]: String(v) };
  const old = flatten(before),
    next = flatten(after),
    keys = [...new Set([...Object.keys(old), ...Object.keys(next)])].filter(
      (k) => old[k] !== next[k],
    );
  return keys.length ? (
    <table className="management-table">
      <thead>
        <tr>
          <th>항목</th>
          <th>현재</th>
          <th>초안</th>
        </tr>
      </thead>
      <tbody>
        {keys.map((k) => (
          <tr key={k}>
            <td>{label(k)}</td>
            <td>{old[k] ?? "—"}</td>
            <td>{next[k] ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  ) : (
    <p>현재 정책과 동일합니다.</p>
  );
}
