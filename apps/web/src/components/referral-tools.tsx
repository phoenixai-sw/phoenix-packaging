"use client";
import { useState } from "react";
import { useApiData } from "@/lib/business";
import { api, errorMessage } from "@/lib/api";
import type { ApiSchema } from "@/lib/api-contract";
import { Feedback } from "./management";

type Overview = ApiSchema<"ReferralOverview">;
const statusLabel: Record<string, string> = { pending: "첫 결제 확정 대기", granted: "보너스 지급", void: "대상 아님" };

export function ReferralTools({ owner }: { owner: boolean }) {
  const { data, error, refresh } = useApiData<Overview>("/referrals");
  const [code, setCode] = useState(""), [busy, setBusy] = useState(false), [claimError, setClaimError] = useState(""), [notice, setNotice] = useState("");
  async function claim() {
    if (busy || !code.trim()) return;
    setBusy(true); setClaimError(""); setNotice("");
    try {
      await api<Overview>("/referrals/claim", { method: "POST", body: JSON.stringify({ code: code.trim() }) });
      setCode(""); setNotice("추천 코드를 등록했습니다. 첫 결제가 확정되면 추천인에게 보너스가 지급됩니다.");
      refresh();
    } catch (e) { setClaimError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  if (!data) return <section className="management-card"><h2>추천 보너스</h2><p role="status">{error || "추천 정보를 불러오는 중…"}</p></section>;
  const policy = data.policy;
  return (
    <section className="management-card" aria-label="추천 보너스">
      <h2>추천 보너스</h2>
      <p className="field-hint">
        추천한 고객의 첫 결제가 취소 가능 기간({policy.cancellation_window_days}일)을 지나면 추천인에게 {policy.bonus_credits}크레딧을 드립니다.
        지급일부터 {policy.bonus_valid_days}일 유효, 월 {policy.monthly_grant_limit}회 한도, 자기 추천 제외. 구매 크레딧과 구분해 표시합니다.
      </p>
      <div className="plan-allowance">
        <strong>내 추천 코드 · {data.code}</strong>
        <span>이번 달 지급 {data.granted_this_month}/{policy.monthly_grant_limit}</span>
      </div>
      {data.claimed_code ? (
        <p className="field-hint">등록한 추천 코드: {data.claimed_code}</p>
      ) : owner ? (
        <div className="button-row">
          <input aria-label="추천 코드 입력" value={code} maxLength={16} placeholder="추천받은 코드 8자리" disabled={busy}
            onChange={(e) => setCode(e.target.value.toUpperCase())} />
          <button className="button button-light" disabled={busy || !code.trim()} onClick={() => void claim()}>추천 코드 등록</button>
        </div>
      ) : (
        <p className="field-hint">추천 코드 등록은 소유자만 할 수 있습니다.</p>
      )}
      <p className="field-hint">가입 후 {policy.claim_window_days}일 안, 첫 결제 전에만 등록할 수 있습니다.</p>
      <Feedback error={claimError} notice={notice} />
      {data.referrals.length > 0 && (
        <ul className="image-text-lines" aria-label="추천 현황">
          {data.referrals.map((item) => (
            <li key={item.id}>
              <span>{statusLabel[item.status] || item.status}</span>
              <span className="field-hint">{new Date(item.created_at).toLocaleDateString("ko-KR")}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
