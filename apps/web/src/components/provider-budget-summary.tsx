export type ProviderBudget = {
  provider: string;
  enforced: boolean;
  timezone: string;
  date: string;
  day_limit_usd: number;
  per_request_allowance_usd: number;
  day_guard_usd: number;
  day_recorded_cost_usd: number;
  day_unknown_attempts: number;
  day_attempts: number;
  month_guard_usd: number;
  month_alert_usd: number;
  estimate_only: boolean;
  alerts: Array<{ code: string; severity: string; message: string }>;
};

const usd = (value: number) =>
  Number.isFinite(value)
    ? new Intl.NumberFormat("ko-KR", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: 2,
        maximumFractionDigits: 4,
      }).format(value)
    : "조회하지 못함";

export function ProviderBudgetSummary({ budget }: { budget?: ProviderBudget }) {
  if (!budget)
    return (
      <p className="field-hint">공급자 예산 현황을 조회하지 못했습니다.</p>
    );
  const metrics = [
    ["일일 요청 차단 기준", usd(budget.day_limit_usd)],
    ["미확인 요청 1건의 가정 비용", usd(budget.per_request_allowance_usd)],
    ["오늘 추정 비용 + 미확인 가정 비용", usd(budget.day_guard_usd)],
    ["오늘 기록된 추정 비용", usd(budget.day_recorded_cost_usd)],
    [
      "오늘 비용 미확인 요청",
      `${budget.day_unknown_attempts.toLocaleString("ko-KR")}건`,
    ],
    ["오늘 기록된 요청", `${budget.day_attempts.toLocaleString("ko-KR")}건`],
    ["이번 달 추정 비용 + 미확인 가정 비용", usd(budget.month_guard_usd)],
    ["월간 화면 알림 기준", usd(budget.month_alert_usd)],
  ];
  return (
    <div>
      <div className="management-section-heading">
        <h3>공급자 API 예산 · USD</h3>
        <span className="pill">
          {budget.enforced
            ? "일일 사전 차단 적용"
            : "현재 제공자는 차단 미적용"}
        </span>
      </div>
      <p className="field-hint">
        {budget.date} · {budget.timezone} 기준 ·{" "}
        {budget.provider === "openai"
          ? "OpenAI"
          : budget.provider === "fixture"
            ? "시험 이미지 제공"
            : budget.provider}
      </p>
      <div className="quality-metrics">
        {metrics.map(([label, value]) => (
          <div key={label}>
            <small>{label}</small>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <p className="field-hint">
        고객 크레딧과 별도로 관리하는 공급자 비용입니다. 미확인 요청은 가정
        비용을 합산해 다음 호출 전에 확인하며, 실제 청구액의 정확한 상한을
        보장하지 않습니다. 월간 기준은 이 화면의 알림이며 요청 차단이나 외부
        메시지 발송 기준이 아닙니다.
      </p>
      {budget.alerts.map((alert) => (
        <div
          key={alert.code}
          className={`alert ${alert.severity === "error" && budget.enforced ? "alert-error" : "alert-info"}`}
          role="status"
        >
          {!budget.enforced && alert.code === "AI_DAILY_COST_LIMIT"
            ? "일일 예산 기준을 넘었습니다. 현재 제공자에는 사전 차단을 적용하지 않습니다."
            : alert.message}
        </div>
      ))}
      <p className="field-hint">
        기준값은 서버 운영 설정입니다. 이 화면에서는 변경하지 않습니다.
      </p>
    </div>
  );
}
