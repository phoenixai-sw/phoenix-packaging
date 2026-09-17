export type ExportJobState = { kind?: string; status: string };
export function canRetryExport(job: ExportJobState) {
  return (
    ["review_export", "editable_export"].includes(job.kind || "") &&
    job.status === "failed"
  );
}
// Kept for existing callers that explicitly need the review-only predicate.
export function canRetryReviewExport(job: ExportJobState) {
  return job.kind === "review_export" && job.status === "failed";
}
export function exportKindLabel(kind?: string) {
  return (
    (
      {
        review_export: "검토용 PDF",
        production_export: "제작용 번들",
        editable_export: "편집용 프로젝트 ZIP",
      } as Record<string, string>
    )[kind || ""] || "프로젝트 파일"
  );
}
export function exportStatusLabel(status: string) {
  return (
    (
      {
        queued: "대기 중",
        running: "준비 중",
        validating: "확인 중",
        succeeded: "준비 완료",
        failed: "준비 실패",
        canceled: "취소됨",
        reconciliation_required: "결과 확인 필요",
      } as Record<string, string>
    )[status] || "상태 확인 필요"
  );
}
export function canRecordPrinterIntake(job: ExportJobState) {
  return (
    job.status === "succeeded" &&
    ["review_export", "production_export"].includes(job.kind || "")
  );
}
export function isExportInProgress(status: string) {
  return ["queued", "running", "validating"].includes(status);
}
export function editableExportBody(projectId: string, revision: number) {
  if (!projectId || !Number.isSafeInteger(revision) || revision < 1)
    throw new Error("저장된 프로젝트 버전을 먼저 확인해 주세요.");
  return {
    project_id: projectId,
    base_revision: revision,
    kind: "editable" as const,
  };
}
