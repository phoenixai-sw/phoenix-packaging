export function servicePaymentLabel(status: string): string {
  return ({
    not_collected: "결제 전",
    pending: "결제 대기",
    paid: "결제 승인 확인",
    failed: "결제 실패",
    canceled: "결제 취소",
    refunded: "전액 환불 완료",
    reconciliation_required: "결제 내역 확인 필요",
  } as Record<string, string>)[status] || "결제 상태 확인 필요";
}

/** A started service cannot offer a self-service refund, even with stale metadata. */
export function serviceRefundDecision(input: {
  serviceStatus: string;
  serviceCode?: string;
  paymentStatus: string;
  allowed: boolean;
  blockedReason?: string | null;
}): { allowed: boolean; message: string } {
  if (input.serviceCode !== "pilot_pro_first_month" && ["in_progress", "delivered", "completed"].includes(input.serviceStatus))
    return { allowed: false, message: "업무가 시작된 서비스는 자동 환불할 수 없습니다. 제공 범위와 처리 내역을 담당자에게 확인해 주세요." };
  if (input.paymentStatus === "refunded")
    return { allowed: false, message: "이 주문은 전액 환불됐습니다." };
  if (input.paymentStatus !== "paid")
    return { allowed: false, message: input.blockedReason || "서버에서 결제 완료를 확인한 주문만 환불 조건을 확인할 수 있습니다." };
  return {
    allowed: input.allowed,
    message: input.allowed
      ? input.serviceCode === "pilot_pro_first_month"
        ? "지급 크레딧이 전부 미사용 상태인지 서버가 다시 확인합니다. 환불 완료 응답이 오기 전에는 취소를 확정하지 않습니다."
        : "업무 시작 전이며 서버가 최종 환불 조건을 다시 확인합니다. 환불 완료 응답이 오기 전에는 취소를 확정하지 않습니다."
      : input.blockedReason || "현재 주문은 자동 환불 조건을 충족하지 않습니다. 내역을 재조회해 주세요.",
  };
}

/** Paid work and subscription fulfilment remain separate from quote acceptance. */
export function serviceWorkPaymentReady(input: {
  serviceCode: string;
  paymentStatus: string;
  hasCheckoutTerms: boolean;
  amount: number;
}): boolean {
  if (input.serviceCode === "pilot_pro_first_month" || (input.hasCheckoutTerms && input.amount > 0))
    return input.paymentStatus === "paid";
  return true;
}

export function servicePaymentWindowOpen(payment: { status: string; expires_at: string }, now = Date.now()): boolean {
  return ["pending", "failed"].includes(payment.status) && new Date(payment.expires_at).getTime() > now;
}

export function recurringConsent(terms: {
  terms_version: string;
  amount_inc_vat: number;
  renewal_amount_inc_vat?: number | null;
  currency: string;
  automatic_renewal: boolean;
  plan_id?: string | null;
}, agreed: boolean) {
  if (!terms.automatic_renewal) return undefined;
  if (!agreed) throw new Error("첫 결제와 다음 결제 금액, 자동 갱신 조건을 확인해 주세요.");
  if (terms.currency !== "KRW" || terms.plan_id !== "pro" ||
      !terms.terms_version || !Number.isSafeInteger(terms.amount_inc_vat) || terms.amount_inc_vat <= 0 ||
      !Number.isSafeInteger(terms.renewal_amount_inc_vat) || (terms.renewal_amount_inc_vat ?? 0) <= 0)
    throw new Error("구독 결제 조건을 확인하지 못했습니다. 최신 신청 내역을 불러와 주세요.");
  return {
    terms_version: terms.terms_version,
    first_amount_inc_vat: terms.amount_inc_vat,
    renewal_amount_inc_vat: terms.renewal_amount_inc_vat as number,
    currency: "KRW" as const,
    automatic_renewal: true as const,
    plan_id: "pro" as const,
  };
}

export function servicePaymentMatchesQuote(order: {
  amount: number;
  currency: string;
  service_order_id?: string | null;
  service_quote_id?: string | null;
}, serviceId: string, quoteId: string, amount: number): boolean {
  return order.amount === amount && order.currency === "KRW" &&
    order.service_order_id === serviceId && order.service_quote_id === quoteId;
}
