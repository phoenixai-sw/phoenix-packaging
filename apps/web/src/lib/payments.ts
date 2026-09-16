type PaymentRequest = {
  method: "CARD";
  amount: { currency: "KRW"; value: number };
  orderId: string;
  orderName: string;
  successUrl: string;
  failUrl: string;
};
type BillingRequest = { method: "CARD"; successUrl: string; failUrl: string };
type TossClient = {
  payment: (options: { customerKey: string }) => {
    requestPayment: (request: PaymentRequest) => Promise<void>;
    requestBillingAuth: (request: BillingRequest) => Promise<void>;
  };
};
declare global {
  interface Window {
    TossPayments?: (clientKey: string) => TossClient;
  }
}
export async function openTossPayment(
  clientKey: string,
  order: {
    order_id: string;
    amount: number;
    customer_key: string;
    checkout_kind?: string;
  },
) {
  if (!window.TossPayments)
    await new Promise<void>((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://js.tosspayments.com/v2/standard";
      script.onload = () => resolve();
      script.onerror = () =>
        reject(
          new Error("결제창을 불러오지 못했습니다. 연결을 확인해 주세요."),
        );
      document.head.appendChild(script);
    });
  if (!window.TossPayments)
    throw new Error("결제 서비스를 연결하지 못했습니다.");
  const payment = window
    .TossPayments(clientKey)
    .payment({ customerKey: order.customer_key });
  const origin = window.location.origin;
  if (order.checkout_kind === "billing_auth")
    await payment.requestBillingAuth({
      method: "CARD",
      successUrl: `${origin}/app/billing/return?order_id=${encodeURIComponent(order.order_id)}`,
      failUrl: `${origin}/app/billing/return?failed=true`,
    });
  else
    await payment.requestPayment({
      method: "CARD",
      amount: { currency: "KRW", value: order.amount },
      orderId: order.order_id,
      orderName: "Phoenix Packaging 크레딧",
      successUrl: `${origin}/app/billing/return`,
      failUrl: `${origin}/app/billing/return?failed=true`,
    });
}
