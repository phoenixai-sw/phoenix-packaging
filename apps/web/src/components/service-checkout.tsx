"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { apiRequest, type ApiData, type ApiSchema } from "@/lib/api-contract";
import { errorMessage } from "@/lib/api";
import { paymentOutcome, refundOutcome } from "@/lib/billing-state";
import { openTossPayment } from "@/lib/payments";
import { recurringConsent, servicePaymentLabel, servicePaymentMatchesQuote, servicePaymentWindowOpen, serviceRefundDecision } from "@/lib/service-payment-state";
import type { ServiceOrder } from "@/lib/service-order-types";
import { Feedback } from "./management";
import styles from "./service-checkout.module.css";

const won = (value: number) => `${value.toLocaleString("ko-KR")}원`;
const date = (value: string) => new Date(value).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
type Billing = ApiData<"/v1/billing">;
type PaymentOrder = ApiSchema<"PaymentOrderData">;

export function ServiceCheckout({ order, admin, busy, onBusyChange, onRefresh }: {
  order: ServiceOrder;
  admin: boolean;
  busy: boolean;
  onBusyChange: (value: boolean) => void;
  onRefresh: () => Promise<void>;
}) {
  const [billing, setBilling] = useState<Billing>();
  const [loading, setLoading] = useState(!admin);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [refundOpen, setRefundOpen] = useState(false);
  const [reason, setReason] = useState("");
  const lock = useRef(false);
  const operation = useRef<{ fingerprint: string; key: string } | undefined>(undefined);
  const live = useRef(true);
  const current = useRef(order);
  current.current = order;
  const quote = order.quotes.find((item) => item.id === order.accepted_quote_id);
  const terms = order.checkout_terms;
  const payment = order.payment_order;
  const caps = billing?.payment_capabilities;
  const refund = serviceRefundDecision({ serviceStatus: order.status, serviceCode: order.service_code, paymentStatus: order.payment_status, allowed: order.refund_allowed, blockedReason: order.refund_blocked_reason });
  const expired = Boolean(payment && new Date(payment.expires_at).getTime() <= Date.now());
  const canPayExisting = Boolean(order.payment_retry_allowed && payment && servicePaymentWindowOpen(payment) && order.status === "accepted");
  const canPay = !admin && Boolean(quote && terms && caps?.checkout_available && (payment ? canPayExisting : order.checkout_enabled));
  const consentRequired = Boolean(terms?.automatic_renewal);
  const renewalLabel = terms?.renewal_amount_inc_vat ? won(terms.renewal_amount_inc_vat) : "금액 확인 필요";

  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => {
    let active = true;
    if (admin) return;
    setLoading(true);
    apiRequest("get", "/v1/billing", {})
      .then((value) => { if (active) setBilling(value); })
      .catch((cause) => { if (active) setError(errorMessage(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [admin, order.id]);
  useEffect(() => { setAgreed(false); }, [terms?.terms_version, order.accepted_quote_id]);

  async function perform(action: () => Promise<void>) {
    if (lock.current || busy) return;
    lock.current = true;
    onBusyChange(true);
    setError(""); setNotice("");
    try { await action(); }
    catch (cause) { setError(errorMessage(cause)); }
    finally {
      if (live.current) await onRefresh().catch((cause) => setError(errorMessage(cause)));
      lock.current = false;
      onBusyChange(false);
    }
  }

  async function pay() {
    if (!canPay || !quote || !terms || !caps) return;
    await perform(async () => {
      const consent = recurringConsent(terms, agreed);
      if (payment) {
        const latest = await apiRequest("get", "/v1/service-orders/{identity}", { path: { identity: order.id } });
        if (!latest.payment_retry_allowed || latest.revision !== order.revision || latest.accepted_quote_id !== quote.id ||
            latest.checkout_terms?.terms_version !== terms.terms_version || latest.payment_order?.order_id !== payment.order_id ||
            !servicePaymentWindowOpen(latest.payment_order))
          throw new Error("연결된 주문의 결제 조건이 변경됐거나 결제창 유효 시간이 지났습니다. 최신 내역을 확인해 주세요.");
      }
      const body = {
        kind: "service" as const,
        service_order_id: order.id,
        service_quote_id: quote.id,
        service_base_revision: order.revision,
        ...(consent ? { recurring_consent: consent } : {}),
      };
      const fingerprint = JSON.stringify(body);
      if (!operation.current || operation.current.fingerprint !== fingerprint)
        operation.current = { fingerprint, key: crypto.randomUUID() };
      const result: PaymentOrder = payment || await apiRequest("post", "/v1/billing/orders", {
        body,
        headers: { "Idempotency-Key": operation.current.key },
      });
      if (!servicePaymentMatchesQuote(result, order.id, quote.id, quote.amount_inc_vat))
        throw new Error("결제 주문과 수락한 서비스 견적이 일치하지 않습니다. 최신 내역을 확인해 주세요.");
      if (!live.current) return;
      if (current.current.id !== order.id || current.current.accepted_quote_id !== quote.id || current.current.revision !== order.revision)
        throw new Error("신청 내역이 변경됐습니다. 결제창을 열기 전에 최신 조건을 다시 확인해 주세요.");
      if (result.status === "paid") {
        setNotice("이미 서버에서 결제를 확인한 주문입니다. 새 결제를 진행하지 않았습니다.");
        return;
      }
      if (!["pending", "failed"].includes(result.status))
        throw new Error(paymentOutcome(result).message);
      if (!servicePaymentWindowOpen(result))
        throw new Error("결제창 유효 시간이 지났습니다. 주문 상태를 재조회해 주세요.");
      if (caps.mock_available) {
        const confirmed = await apiRequest("post", "/v1/billing/mock-confirm", { body: { order_id: result.order_id } });
        if (confirmed.status !== "paid") throw new Error(paymentOutcome(confirmed).message);
        setNotice("모의 결제를 확인했습니다. 실제 금액을 결제하지 않았습니다. 아래 업무·구독 상태를 확인하세요.");
      } else {
        if (!caps.client_key) throw new Error(caps.message || "결제 서비스 연결을 준비하고 있습니다.");
        await openTossPayment(caps.client_key, result, { serviceOrderId: order.id, orderName: `Phoenix Packaging ${order.catalog_snapshot.name}` });
      }
    });
  }

  async function refreshPayment() {
    await perform(async () => {
      if (payment) {
        const result = await apiRequest("post", "/v1/billing/orders/{order_id}/sync", { path: { order_id: payment.order_id } });
        setNotice(result.status === "paid" ? "서버에서 결제 승인을 확인했습니다." : paymentOutcome(result).message);
      }
      const updated = await apiRequest("get", "/v1/billing", {});
      setBilling(updated);
    });
  }

  async function requestRefund(event: React.FormEvent) {
    event.preventDefault();
    if (!payment || !refund.allowed) return;
    await perform(async () => {
      const result = await apiRequest("post", "/v1/billing/orders/{order_id}/refund", { path: { order_id: payment.order_id }, body: { reason } });
      const outcome = refundOutcome(result);
      setNotice(outcome.completed ? "서버에서 전액 환불 완료를 확인했습니다. 서비스와 구독 내역도 다시 확인해 주세요." : outcome.message);
      setRefundOpen(false); setReason("");
    });
  }

  return <section className={styles.panel} aria-label="서비스 결제와 구독 조건">
    <div className="management-section-heading"><h3>결제와 이용 상태</h3><span className="pill">{servicePaymentLabel(order.payment_status)}</span></div>
    <Feedback error={error} notice={notice} />
    <p className="field-hint">견적 수락과 결제 승인은 별개입니다. 업무 시작·결과 전달은 담당자가 확인하고, 구독·크레딧은 서버가 결제 승인을 검증한 뒤 반영합니다.</p>
    {quote && <div className={styles.terms}>
      <strong>수락한 견적 {quote.number} · {won(quote.amount_inc_vat)} <small>VAT 포함</small></strong>
      <p style={{ whiteSpace: "pre-wrap" }}>{quote.scope}</p>
      <p style={{ whiteSpace: "pre-wrap" }}>제외 범위: {quote.exclusions}</p>
      <p className="field-hint">결제 전 견적 유효 기한: {date(quote.expires_at)}</p>
      {terms?.automatic_renewal ? <>
        <p><strong>첫 달 {won(terms.amount_inc_vat)} · 이후 매월 {renewalLabel}</strong><br />모두 VAT 포함 · Pro · 월 {terms.credits.toLocaleString()}크레딧 · {terms.seats}명</p>
        <p className="field-hint">수락 당시 자동 갱신 조건: 등록한 결제 수단으로 매월 갱신하는 계약입니다. 현재 구독·갱신 중단·환불 상태는 아래 이용 상태와 구독·잔액·갱신 관리에서 확인하세요. 신청·견적 수락만으로 구독을 개통하지 않습니다.</p>
        <p className="field-hint">첫 달 중 상향 변경은 실제 첫 달 결제액을 기준으로 남은 기간 차액을 계산하며, 결제 전 변경 견적을 다시 확인합니다.</p>
      </> : terms ? <p>일회 결제 · 자동 갱신 없음 · 지급 크레딧 0</p> : <p className="alert alert-info">이전 견적에는 결제 조건이 없습니다. 조건이 포함된 새 견적을 확인해 주세요.</p>}
    </div>}
    {payment && <div className={styles.record}>
      <p>결제 주문 <code>{payment.order_id}</code><br />{won(payment.amount)} · VAT 포함 · {servicePaymentLabel(payment.status)}</p>
      {payment.error && <p className="alert alert-info">{payment.error}</p>}
      {payment.payment && <p className="field-hint">PG 상태 {payment.payment.provider_status} · {payment.payment.approved_at ? `승인 ${date(payment.payment.approved_at)}` : "승인 확인 전"} · 취소 {won(payment.payment.cancelled_amount)}</p>}
      {expired && ["pending", "failed"].includes(payment.status) && <p className="alert alert-info">결제창 유효 시간이 지났습니다. 새 주문을 반복 생성하지 말고 상태를 재조회한 뒤 담당자에게 확인해 주세요.</p>}
    </div>}
    {order.service_code === "pilot_pro_first_month" && <p className="alert alert-info">{order.subscription_active
      ? `서버에서 Pro 이용 중을 확인했습니다. 이 결제로 지급한 크레딧은 ${order.credits_granted.toLocaleString()}개이며 현재 잔액은 구독 메뉴에서 확인하세요.`
      : "현재 이 신청에 연결된 활성 구독은 확인되지 않았습니다. 결제 완료 여부와 구독 상태를 따로 확인해 주세요."}</p>}
    {!admin && <>
      {loading && <p role="status">현재 결제 가능 조건을 확인하고 있어요.</p>}
      {caps && <p className="field-hint">{caps.mock_available ? "모의 결제 환경 · 실제 금액은 결제하지 않습니다." : caps.test_mode ? "Toss 테스트 환경 · 실제 청구가 아닙니다." : caps.live_enabled ? "실제 결제 환경입니다." : "실제 결제는 아직 열려 있지 않습니다."}</p>}
      {!canPay && !["paid", "refunded"].includes(order.payment_status) && <p className="alert alert-info">{(payment ? order.payment_retry_blocked_reason : order.checkout_blocked_reason) || caps?.message || "현재 상태에서는 결제창을 열 수 없습니다."}</p>}
      {canPay && <fieldset disabled={busy} className="editor-properties-fieldset">
        {consentRequired && terms && <label className="compact-check"><input type="checkbox" checked={agreed} onChange={(event) => setAgreed(event.target.checked)} />첫 달 {won(terms.amount_inc_vat)}, 다음 달부터 매월 {renewalLabel}(VAT 포함)의 Pro 자동 갱신 및 위 제공·제외 범위에 동의합니다.</label>}
        <button className="button button-dark" disabled={loading || (consentRequired && !agreed)} onClick={() => void pay()}>{busy ? "확인 중…" : caps?.mock_available ? "모의 결제 승인 확인" : `${won(quote?.amount_inc_vat || 0)} 결제창 열기`}</button>
      </fieldset>}
      <div className="button-row"><button className="button button-light" disabled={busy} onClick={() => void refreshPayment()}>{payment ? "결제 상태 재조회" : "결제 가능 상태 새로고침"}</button><Link className="text-link" href="/app/billing">구독·잔액·갱신 관리</Link></div>
      {payment && <><p className="field-hint">{refund.message}</p>{refund.allowed && !refundOpen && <button className="button button-light" disabled={busy} onClick={() => setRefundOpen(true)}>전액 환불 조건 확인</button>}
        {refundOpen && refund.allowed && <form onSubmit={requestRefund}><fieldset disabled={busy} className="editor-properties-fieldset"><label className="field">환불 사유<textarea required minLength={3} maxLength={200} value={reason} onChange={(event) => setReason(event.target.value)} /></label><p className="field-hint">제공자는 실제 취소 결과를 검증합니다. 업무가 시작되거나 지급분을 사용한 경우 자동 환불이 거절될 수 있습니다.</p><div className="button-row"><button className="button button-dark">전액 환불 요청</button><button type="button" className="button button-light" onClick={() => setRefundOpen(false)}>닫기</button></div></fieldset></form>}</>}
    </>}
  </section>;
}
