"use client";
import { Suspense, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { AlertCircle, CheckCircle2, Clock3, LoaderCircle } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { paymentOutcome, type PaymentOrderResult, type PaymentOutcome } from "@/lib/billing-state";
import { mayConfirmPaymentReturn, paymentReturnPaths, serviceReturnId } from "@/lib/payment-return-context";
function PaymentReturn() {
  const params = useSearchParams();
  const [serviceId] = useState(() => serviceReturnId(params.get("service_order_id")));
  const [orderId] = useState(() => params.get("order_id") || params.get("orderId") || "");
  const actionLock = useRef(false);
  const destination = paymentReturnPaths(orderId, false, serviceId).destination;
  const hasPaymentResult = Boolean(
    (params.get("authKey") &&
      params.get("customerKey") &&
      params.get("order_id")) ||
      (params.get("paymentKey") &&
        params.get("orderId") &&
        Number(params.get("amount")) > 0),
  );
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<PaymentOutcome>();
  const [paymentStatus, setPaymentStatus] = useState<string>();
  const [error, setError] = useState(
    params.get("failed")
      ? "결제가 완료되지 않았습니다. 결제 내역에서 주문 상태를 확인해 주세요."
      : "",
  );
  async function confirm() {
    if (actionLock.current || !mayConfirmPaymentReturn(hasPaymentResult, Boolean(params.get("failed")), paymentStatus)) return;
    actionLock.current = true;
    setBusy(true);
    setError("");
    try {
      let order: PaymentOrderResult;
      if (params.get("authKey"))
        order = await api<PaymentOrderResult>("/billing/billing-key/confirm", {
          method: "POST",
          body: JSON.stringify({
            order_id: params.get("order_id"),
            auth_key: params.get("authKey"),
            customer_key: params.get("customerKey"),
          }),
        });
      else
        order = await api<PaymentOrderResult>("/billing/confirm", {
          method: "POST",
          body: JSON.stringify({
            order_id: params.get("orderId"),
            payment_key: params.get("paymentKey"),
            amount: Number(params.get("amount")),
          }),
        });
      setOutcome(paymentOutcome(order));
      setPaymentStatus(order.status);
      const safeParams = new URLSearchParams();
      if (serviceId) safeParams.set("service_order_id", serviceId);
      if (orderId) safeParams.set("order_id", orderId);
      window.history.replaceState({}, "", `/app/billing/return${safeParams.size ? `?${safeParams}` : ""}`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      actionLock.current = false;
      setBusy(false);
    }
  }
  async function refreshPayment() {
    if (actionLock.current || !orderId) return;
    actionLock.current = true;
    setBusy(true);
    setError("");
    try {
      const order = await api<PaymentOrderResult>(`/billing/orders/${encodeURIComponent(orderId)}/sync`, { method: "POST" });
      setOutcome(paymentOutcome(order));
      setPaymentStatus(order.status);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      actionLock.current = false;
      setBusy(false);
    }
  }
  return (
    <div className="payment-return">
      {outcome?.state === "paid" ? <CheckCircle2 size={38} /> : outcome?.state === "failed" || error ? <AlertCircle size={38} /> : <Clock3 size={38} />}
      <h1>{outcome?.title || "결제 결과를 확인해 주세요."}</h1>
      <p>
        {outcome?.state === "paid" && serviceId
          ? "서버가 결제 승인을 확인했습니다. 연결된 서비스 업무와 구독 상태는 신청 내역에서 확인해 주세요."
          : outcome?.message || "결제 서비스에서 돌아온 결과를 서버에서 검증한 뒤 반영합니다."}
      </p>
      {error && (
        <div role="alert" className="alert alert-error">
          {error}
        </div>
      )}
      {!outcome && !params.get("failed") && !hasPaymentResult && (
        <p className="field-hint">
          확인할 결제 결과가 없습니다. 주문 내역에서 진행 상태를 확인해 주세요.
        </p>
      )}
      {mayConfirmPaymentReturn(hasPaymentResult, Boolean(params.get("failed")), paymentStatus) && (
        <button
          className="button button-dark"
          onClick={() => void confirm()}
          disabled={busy}
        >
          {busy ? <LoaderCircle className="spin" /> : "서버에서 결제 확인"}
        </button>
      )}
      {orderId && <button className="button button-light" disabled={busy} onClick={() => void refreshPayment()}>
        {busy ? "확인 중…" : "서버에서 주문 상태 재조회"}
      </button>}
      <Link className="text-link" href={destination}>
        {serviceId ? "연결된 서비스 신청으로 돌아가기" : "구독과 크레딧으로 돌아가기"}
      </Link>
    </div>
  );
}
export default function ReturnPage() {
  return (
    <Suspense
      fallback={
        <div className="loading-state">결제 결과를 불러오고 있어요.</div>
      }
    >
      <PaymentReturn />
    </Suspense>
  );
}
