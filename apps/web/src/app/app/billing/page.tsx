"use client";
import { useState } from "react";
import { Check, Coins, LoaderCircle, Plus, RefreshCw } from "lucide-react";
import {
  ManagementPage,
  Feedback,
  Loading,
  Dialog,
} from "@/components/management";
import { useApiData, roleOf, money, dateTime } from "@/lib/business";
import { api, errorMessage } from "@/lib/api";
import { useSession } from "@/components/workspace";
import { openTossPayment } from "@/lib/payments";
const creditLabels: Record<string, string> = {
  trial: "무료 체험",
  subscription: "월 지급",
  topup: "추가 구매",
  GRANT: "지급",
  RESERVE: "작업 예약",
  CAPTURE: "사용 확정",
  RELEASE: "예약 반환",
  EXPIRE: "만료",
  REFUND: "환불",
  ADJUSTMENT: "조정",
};
type Order = {
  order_id: string;
  id?: string;
  amount: number;
  kind: string;
  plan_id?: string;
  credits: number;
  status: string;
  created_at?: string;
  customer_key: string;
  checkout_kind?: string;
  error?: string;
};
type Billing = {
  policy: {
    version: string;
    plans: Array<{
      id: string;
      name: string;
      monthly_inc_vat: number;
      credits: number;
      seats: number;
    }>;
    topups: Array<{ credits: number; inc_vat: number; expires_months: number }>;
  };
  subscription: null | {
    plan_id: string;
    status: string;
    current_period_end?: string;
    cancel_at_period_end?: boolean;
    next_plan_id?: string;
  };
  summary: {
    balance: number;
    reserved: number;
    consumed: number;
    expired: number;
    mode: string;
    buckets: Array<{
      id: string;
      kind: string;
      available: number;
      reserved: number;
      expires_at: string;
    }>;
    ledger: Array<{
      id: string;
      event: string;
      amount: number;
      reason: string;
      created_at: string;
    }>;
  };
  orders: Order[];
  payment_capabilities: {
    provider: string;
    test_mode: boolean;
    checkout_available: boolean;
    live_enabled: boolean;
    mock_available: boolean;
    client_key?: string;
    billing_auth_available: boolean;
  };
  payment_errors?: string[];
};
export default function BillingPage() {
  const session = useSession();
  const owner = roleOf(session) === "owner";
  const { data, loading, error, refresh } = useApiData<Billing>("/billing");
  const [order, setOrder] = useState<Order>();
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  const [cancelDialog, setCancelDialog] = useState(false);
  const [refund, setRefund] = useState<Order>();
  const [reason, setReason] = useState("");
  const [operationKey, setOperationKey] = useState<string>();
  async function createOrder(payload: {
    kind: "subscription" | "topup";
    plan_id?: string;
    credits?: number;
  }) {
    setBusy(true);
    setFormError("");
    try {
      const key = crypto.randomUUID();
      setOperationKey(key);
      const result = await api<Order>("/billing/orders", {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify(payload),
      });
      setOrder(result);
      refresh();
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function pay() {
    if (!order || !data) return;
    setBusy(true);
    setFormError("");
    try {
      if (data.payment_capabilities.mock_available) {
        await api("/billing/mock-confirm", {
          method: "POST",
          body: JSON.stringify({ order_id: order.order_id }),
        });
        setNotice(
          "모의 결제가 확인되었습니다. 실제 금액은 결제되지 않았습니다.",
        );
        setOrder(undefined);
        refresh();
      } else if (data.payment_capabilities.client_key) {
        await openTossPayment(data.payment_capabilities.client_key, order);
      } else throw new Error("결제 서비스 연결을 준비하고 있습니다.");
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function changePlan(plan_id: string) {
    setBusy(true);
    setFormError("");
    try {
      const result = await api<{
        order?: Order;
        scheduled?: boolean;
        subscription?: unknown;
      }>("/billing/change-plan", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ plan_id }),
      });
      if (result.order) setOrder(result.order);
      else
        setNotice(
          "요금제 변경 요청을 저장했습니다. 적용 시점은 현재 구독 정보를 확인하세요.",
        );
      refresh();
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    setBusy(true);
    try {
      await api("/billing/cancel-renewal", { method: "POST", body: "{}" });
      setNotice("다음 갱신을 중단했습니다. 현재 이용 기간은 유지됩니다.");
      setCancelDialog(false);
      refresh();
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function submitRefund(e: React.FormEvent) {
    e.preventDefault();
    if (!refund) return;
    setBusy(true);
    try {
      await api(`/billing/orders/${refund.order_id || refund.id}/refund`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
      setRefund(undefined);
      setReason("");
      setNotice("환불 처리를 확인했습니다.");
      refresh();
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <ManagementPage
      eyebrow="CLEAR PRICING, CLEAR RECORDS"
      title="구독과 크레딧"
      description="잔액부터 예약·차감·복원까지, 팀의 작업 내역을 확인하세요."
      actions={
        <button className="button button-light" onClick={refresh}>
          <RefreshCw size={16} /> 새로고침
        </button>
      }
    >
      <Feedback error={error || formError} notice={notice} />
      {loading && !data ? (
        <Loading />
      ) : (
        data && (
          <>
            <div className="alert alert-info">
              <Coins size={18} />
              <span>
                {data.payment_capabilities.mock_available
                  ? "모의 결제 환경입니다. 실제 금액이 결제되지 않습니다."
                  : data.payment_capabilities.test_mode
                    ? "결제 테스트 환경입니다. 운영 결제와 구분하여 검증합니다."
                    : data.payment_capabilities.live_enabled
                      ? "서버에서 확인한 결제 결과에 따라 크레딧이 지급됩니다."
                      : "결제 연동과 운영 정책 확인 전입니다. 현재 유료 결제는 열리지 않습니다."}
              </span>
            </div>
            {data.payment_errors?.length ? (
              <Feedback notice={data.payment_errors.join(" · ")} />
            ) : null}
            <div className="wallet-stats">
              {[
                ["사용 가능", data.summary.balance],
                ["작업 예약", data.summary.reserved],
                ["누적 사용", data.summary.consumed],
                ["만료", data.summary.expired],
              ].map(([label, value]) => (
                <div key={String(label)}>
                  <span>{label}</span>
                  <strong>
                    {Number(value).toLocaleString("ko-KR")}
                    <small> 크레딧</small>
                  </strong>
                </div>
              ))}
            </div>
            <section className="management-card">
              <div className="management-section-heading">
                <h2>현재 구독</h2>
                <span className="pill">
                  {data.subscription?.status || "무료 체험"}
                </span>
              </div>
              <p className="subscription-summary">
                {data.subscription
                  ? `${data.policy.plans.find((p) => p.id === data.subscription?.plan_id)?.name || data.subscription.plan_id} · 이용 기간 ${dateTime(data.subscription.current_period_end)}까지`
                  : "계정의 체험 크레딧과 유효기간은 아래 내역에서 확인하세요."}
              </p>
              {data.subscription?.cancel_at_period_end && (
                <p className="field-hint">기간 종료 후 갱신되지 않습니다.</p>
              )}
              {data.subscription?.next_plan_id && (
                <p className="field-hint">
                  다음 주기 변경 예정: {data.subscription.next_plan_id}
                </p>
              )}
              {owner &&
                data.subscription &&
                !data.subscription.cancel_at_period_end && (
                  <button
                    className="button button-light button-sm"
                    onClick={() => setCancelDialog(true)}
                  >
                    다음 갱신 중단
                  </button>
                )}
            </section>
            <div className="billing-plan-grid">
              {data.policy.plans.map((plan) => (
                <article key={plan.id} className="management-card">
                  <span className="eyebrow">{plan.name}</span>
                  <h2>
                    {money(plan.monthly_inc_vat)}
                    <small> / 월, 부가세 포함</small>
                  </h2>
                  <p>
                    {plan.credits.toLocaleString()} 크레딧 · {plan.seats}명 공용
                  </p>
                  <button
                    className="button button-dark full-width"
                    disabled={
                      !owner ||
                      busy ||
                      data.subscription?.plan_id === plan.id ||
                      !(
                        data.payment_capabilities.checkout_available ||
                        data.payment_capabilities.mock_available
                      )
                    }
                    onClick={() =>
                      data.subscription
                        ? void changePlan(plan.id)
                        : void createOrder({
                            kind: "subscription",
                            plan_id: plan.id,
                          })
                    }
                  >
                    {data.subscription?.plan_id === plan.id
                      ? "이용 중"
                      : data.subscription
                        ? "변경 조건 확인"
                        : "구독 주문 확인"}
                  </button>
                </article>
              ))}
            </div>
            <section className="management-card">
              <div className="management-section-heading">
                <h2>필요한 만큼 추가 충전</h2>
              </div>
              <div className="topup-options">
                {data.policy.topups.map((t) => (
                  <button
                    className="button button-light"
                    key={t.credits}
                    disabled={
                      !owner ||
                      busy ||
                      !(
                        data.payment_capabilities.checkout_available ||
                        data.payment_capabilities.mock_available
                      )
                    }
                    onClick={() =>
                      void createOrder({ kind: "topup", credits: t.credits })
                    }
                  >
                    <Plus size={16} />
                    {t.credits.toLocaleString()} 크레딧 · {money(t.inc_vat)}
                    <small>{t.expires_months}개월</small>
                  </button>
                ))}
              </div>
              <p className="field-hint">
                월 지급분은 다음 결제 주기에 만료되며 이월되지 않습니다.
                구매분은 표시된 유효기간을 따릅니다.
              </p>
            </section>
            <section className="management-card">
              <h2>크레딧 종류와 만료</h2>
              <div className="management-table-wrap">
                <table className="management-table">
                  <thead>
                    <tr>
                      <th>종류</th>
                      <th>사용 가능</th>
                      <th>예약</th>
                      <th>만료</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.summary.buckets.map((b) => (
                      <tr key={b.id}>
                        <td>{creditLabels[b.kind] || b.kind}</td>
                        <td>{b.available}</td>
                        <td>{b.reserved}</td>
                        <td>{dateTime(b.expires_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="management-card">
              <h2>차감·예약·복원 내역</h2>
              <div className="management-table-wrap">
                <table className="management-table">
                  <thead>
                    <tr>
                      <th>시간</th>
                      <th>종류</th>
                      <th>크레딧</th>
                      <th>사유</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.summary.ledger.map((row) => (
                      <tr key={row.id}>
                        <td>{dateTime(row.created_at)}</td>
                        <td>{creditLabels[row.event] || row.event}</td>
                        <td>{row.amount}</td>
                        <td>{row.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="management-card">
              <h2>결제 내역</h2>
              <div className="management-table-wrap">
                <table className="management-table">
                  <thead>
                    <tr>
                      <th>시간</th>
                      <th>주문</th>
                      <th>금액</th>
                      <th>상태</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {data.orders.map((row) => (
                      <tr key={row.order_id || row.id}>
                        <td>{dateTime(row.created_at)}</td>
                        <td>
                          {row.plan_id || `${row.credits} 크레딧`}
                          <small>{row.order_id || row.id}</small>
                        </td>
                        <td>{money(row.amount)}</td>
                        <td>
                          {row.status}
                          <small>{row.error}</small>
                        </td>
                        <td>
                          {owner && row.status === "paid" && (
                            <button
                              className="button button-light button-sm"
                              onClick={() => {
                                setRefund(row);
                                setFormError("");
                              }}
                            >
                              환불 조건 확인
                            </button>
                          )}
                          {owner && row.status === "pending" && (
                            <button
                              className="button button-light button-sm"
                              onClick={() => setOrder(row)}
                            >
                              결제 계속
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )
      )}
      {order && data && (
        <Dialog title="주문과 결제액 확인" onClose={() => setOrder(undefined)}>
          <div className="order-confirmation">
            <span>
              {data.payment_capabilities.mock_available
                ? "모의 결제"
                : data.payment_capabilities.test_mode
                  ? "테스트 결제"
                  : "결제 확인"}
            </span>
            <strong>{money(order.amount)}</strong>
            <p>부가세 포함 · {order.credits.toLocaleString()} 크레딧</p>
            <small>주문 번호 {order.order_id}</small>
          </div>
          <Feedback error={formError} />
          <p className="field-hint">
            서버가 확인한 결제만 반영합니다. 응답이 늦어져도 같은 주문으로
            확인해 중복 지급을 방지합니다.
          </p>
          <button
            className="button button-dark full-width"
            onClick={() => void pay()}
            disabled={busy}
          >
            {busy ? (
              <LoaderCircle className="spin" size={17} />
            ) : data.payment_capabilities.mock_available ? (
              "모의 결제 확인"
            ) : (
              "결제창 열기"
            )}
          </button>
        </Dialog>
      )}
      {cancelDialog && (
        <Dialog
          title="다음 갱신을 중단할까요?"
          onClose={() => setCancelDialog(false)}
        >
          <p className="dialog-description">
            현재 구독 기간이 끝날 때까지 이용할 수 있습니다. 추가 구매분은 별도
            유효기간을 따릅니다.
          </p>
          <Feedback error={formError} />
          <button
            className="button button-dark full-width"
            onClick={() => void cancel()}
            disabled={busy}
          >
            다음 갱신 중단하기
          </button>
        </Dialog>
      )}
      {refund && (
        <Dialog title="환불 요청" onClose={() => setRefund(undefined)}>
          <form onSubmit={submitRefund}>
            <p className="dialog-description">
              사용하지 않은 주문만 서버 정책 확인 후 환불할 수 있습니다. 승인된
              환불은 결제 취소와 크레딧 회수를 함께 처리합니다.
            </p>
            <label className="field">
              환불 사유
              <textarea
                required
                minLength={3}
                maxLength={300}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={3}
              />
            </label>
            <Feedback error={formError} />
            <button className="button button-dark full-width" disabled={busy}>
              환불 요청 확인
            </button>
          </form>
        </Dialog>
      )}
    </ManagementPage>
  );
}
