import type { ApiSchema } from "./api-contract";

export const INTAKE_NOTICE =
  "제조사 회신은 사용자가 직접 기록한 내용입니다. 외부 검증이나 플랫폼의 제조 승인을 뜻하지 않으며, 파일 생성 상태와 별개입니다.";

export function intakeStatusLabel(
  summary: ApiSchema<"IntakeSourceSummary"> | null | undefined,
) {
  if (summary?.technical_rejected) return "기술 반려 이력 있음";
  if (summary?.status === "accepted") return "입고 수락 기록";
  if (summary?.status === "rejected") return "반려 기록";
  if (summary?.status === "submitted") return "제출 기록 · 회신 대기";
  return "기록 없음";
}

export function intakeRecordLabel(
  item: Pick<ApiSchema<"PrinterIntakeHistoryItem">, "record_source" | "verification" | "status" | "rejection_kind">,
) {
  // A test or ambiguous legacy record must never become manufacturer evidence.
  const source = item.record_source === "manufacturer" && item.verification === "self_reported"
    ? "제조사 회신 · 사용자 기록"
    : item.record_source === "test" && item.verification === "test_record"
      ? "내부 시험 기록"
      : "출처 미확인 기록";
  const status = item.status === "accepted" ? "입고 수락"
    : item.status === "submitted" ? "제출"
      : item.rejection_kind === "technical" ? "기술 반려"
        : item.rejection_kind === "aesthetic" ? "디자인 변경 요청" : "반려";
  return { source, status };
}
