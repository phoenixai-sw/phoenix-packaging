export type PaymentOrderResult = { status: string; error?: string | null };
export type PaymentOutcome = {
  state: "paid" | "pending" | "failed";
  title: string;
  message: string;
};

export function paymentOutcome(order: PaymentOrderResult): PaymentOutcome {
  if (order.status === "paid")
    return { state: "paid", title: "결제가 완료되었습니다.", message: "서버가 승인을 확인했습니다. 잔액과 결제 내역에서 결과를 확인하세요." };
  if (["failed", "refunded", "canceled"].includes(order.status))
    return {
      state: "failed",
      title: order.status === "refunded" ? "이미 환불된 주문입니다." : "결제가 완료되지 않았습니다.",
      message: order.error || "주문 내역에서 상태를 확인해 주세요. 이 응답으로 새 크레딧이 지급되지 않습니다.",
    };
  return {
    state: "pending",
    title: "결제 상태를 확인하고 있습니다.",
    message: order.error || "아직 결제 완료가 확인되지 않았습니다. 새 주문을 만들지 말고 주문 내역에서 상태를 확인해 주세요.",
  };
}

export function refundOutcome(order: PaymentOrderResult): { completed: boolean; message: string } {
  if (order.status === "refunded")
    return { completed: true, message: "환불 완료를 확인했습니다. 결제 취소와 크레딧 조정 내역을 확인하세요." };
  return {
    completed: false,
    message: "환불 완료가 아직 확인되지 않았습니다. " + (order.error || "주문 상태를 확인하고 있습니다. 중복 요청하지 말고 결제 내역에서 결과를 확인해 주세요."),
  };
}

export function planSelection(
  subscription: { plan_id: string } | null,
  activeSubscription: boolean,
  targetPlan: string,
): "subscribe" | "change" | "current" {
  if (!subscription || !activeSubscription) return "subscribe";
  return subscription.plan_id === targetPlan ? "current" : "change";
}
