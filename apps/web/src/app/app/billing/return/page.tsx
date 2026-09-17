"use client";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { AlertCircle, CheckCircle2, Clock3, LoaderCircle } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { paymentOutcome, type PaymentOrderResult, type PaymentOutcome } from "@/lib/billing-state";
function PaymentReturn() {
  const params = useSearchParams();
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
  const [error, setError] = useState(
    params.get("failed")
      ? "결제가 완료되지 않았습니다. 결제 내역에서 주문 상태를 확인해 주세요."
      : "",
  );
  async function confirm() {
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
      window.history.replaceState({}, "", "/app/billing/return");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="payment-return">
      {outcome?.state === "paid" ? <CheckCircle2 size={38} /> : outcome?.state === "failed" || error ? <AlertCircle size={38} /> : <Clock3 size={38} />}
      <h1>{outcome?.title || "결제 결과를 확인해 주세요."}</h1>
      <p>
        {outcome?.message || "결제 서비스에서 돌아온 결과를 서버에서 검증한 뒤 지급합니다."}
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
      {!outcome && !params.get("failed") && hasPaymentResult && (
        <button
          className="button button-dark"
          onClick={() => void confirm()}
          disabled={busy}
        >
          {busy ? <LoaderCircle className="spin" /> : "서버에서 결제 확인"}
        </button>
      )}
      <Link className="text-link" href="/app/billing">
        구독과 크레딧으로 돌아가기
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
