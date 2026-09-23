import { paymentReturnPaths } from "./payment-return-context";
import { closeBlockingModals } from "./payment-window";

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
    customer_key: string | null;
    checkout_kind?: string;
  },
  context?: { serviceOrderId?: string; orderName?: string },
) {
  if (!order.customer_key)
    throw new Error("주문의 결제 식별자를 확인하지 못했습니다. 내역을 새로고침해 주세요.");
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
  closeBlockingModals(document);
  const origin = window.location.origin;
  const paths = paymentReturnPaths(order.order_id, order.checkout_kind === "billing_auth", context?.serviceOrderId);
  if (order.checkout_kind === "billing_auth")
    await payment.requestBillingAuth({
      method: "CARD",
      successUrl: `${origin}${paths.successPath}`,
      failUrl: `${origin}${paths.failPath}`,
    });
  else
    await payment.requestPayment({
      method: "CARD",
      amount: { currency: "KRW", value: order.amount },
      orderId: order.order_id,
      orderName: context?.orderName?.slice(0, 100) || "Phoenix Package Design 크레딧",
      successUrl: `${origin}${paths.successPath}`,
      failUrl: `${origin}${paths.failPath}`,
    });
}
