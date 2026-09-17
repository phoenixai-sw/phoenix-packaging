const serviceIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Only a service identifier may affect the return destination, never a URL. */
export function serviceReturnId(value: string | null | undefined): string | undefined {
  return value && serviceIdPattern.test(value) ? value : undefined;
}

export function mayConfirmPaymentReturn(hasProviderResult: boolean, failureReturn: boolean, status?: string): boolean {
  return hasProviderResult && !failureReturn && (status === undefined || status === "pending" || status === "failed");
}

export function paymentReturnPaths(orderId: string, billingAuth: boolean, serviceId?: string) {
  const service = serviceReturnId(serviceId);
  const common = new URLSearchParams();
  if (service) common.set("service_order_id", service);
  const success = new URLSearchParams(common);
  if (billingAuth) success.set("order_id", orderId);
  const failure = new URLSearchParams(common);
  failure.set("failed", "true");
  failure.set("order_id", orderId);
  return {
    successPath: `/app/billing/return${success.size ? `?${success}` : ""}`,
    failPath: `/app/billing/return?${failure}`,
    destination: service ? `/app/services?service_order_id=${encodeURIComponent(service)}` : "/app/billing",
  };
}
