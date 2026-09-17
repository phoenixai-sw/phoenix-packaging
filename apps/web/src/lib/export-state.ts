import type { ApiSchema } from "./api-contract";

export type ExportJobState = { kind?: string; status: string; availability?: ApiSchema<"ExportAvailability"> | null };

/** Server IDs refer to the checked snapshot; never reinterpret its index in today's scene. */
export function preflightIssueTarget(
  issue: { face_id?: string | null; object_id?: string | null },
  faces: Array<{ id: string; objects: Array<{ id: string }> }>,
) {
  const face = faces.find((value) => value.id === issue.face_id);
  if (!face) return null;
  const object = face.objects.find((value) => value.id === issue.object_id);
  return { faceId: face.id, objectId: object?.id };
}

/** Availability is current storage evidence, independent of the immutable output. */
export function exportAvailabilityNotice(job: ExportJobState) {
  const value = job.availability;
  if (!value || ["unchecked", "available"].includes(value.status)) return null;
  return {
    title: ({
      suspect: "파일 상태 재확인 중",
      temporarily_unverified: "저장소 연결 확인 필요",
      unavailable: "출력 파일 사용 불가",
      compensated: "출력 파일 사용 불가 · 차감 복원",
      deleted: "파일 접근 종료",
    } as Record<string, string>)[value.status] || "파일 상태 확인 필요",
    message: value.message,
    creditRestored: value.credit_restored,
    nextCheckAt: ["suspect", "temporarily_unverified"].includes(value.status) ? value.next_check_at : null,
  };
}

/** This is today's registry state, never a rewrite of the original file result. */
export function exportApprovalNotice(job: {
  kind?: string;
  current_approval?: ApiSchema<"ExportCurrentApproval"> | null;
}) {
  if (job.kind !== "production_export") return null;
  const approval = job.current_approval;
  const revoked = approval?.status === "revoked";
  const approved = approval?.status === "approved";
  return {
    warning: !approved,
    title: revoked
      ? "해당 조건 승인 철회"
      : approved
        ? "현재 도면·인쇄 프로필 승인 유지"
        : "현재 승인 상태 확인 필요",
    detail: revoked
      ? "기존 출력 파일은 보존됩니다. 다시 제작하기 전에 현재 조건을 확인해 주세요."
      : approved
        ? "출력 당시 사용한 조건의 현재 상태입니다. 새 제작의 검수·승인을 대신하지 않습니다."
        : "출력 당시의 조건을 현재 승인된 상태로 확인할 수 없습니다. 제작 전에 운영자에게 확인해 주세요.",
    reasons: (approval?.versions || [])
      .filter((version) => version.status === "revoked")
      .map((version) => ({
        id: `${version.kind}:${version.id || "unknown"}`,
        label: `${version.kind === "template" ? "도면" : "인쇄 프로필"} · ${version.name}`,
        reason: version.public_reason || "승인이 철회되었습니다. 운영자에게 상세 확인을 요청하세요.",
        revokedAt: version.revoked_at,
      })),
    checkedAt: approval?.checked_at,
  };
}
export function canRetryExport(job: ExportJobState) {
  return (
    ["review_export", "editable_export"].includes(job.kind || "") &&
    job.status === "failed" &&
    job.availability?.retry_allowed !== false
  );
}
// Kept for existing callers that explicitly need the review-only predicate.
export function canRetryReviewExport(job: ExportJobState) {
  return job.kind === "review_export" && canRetryExport(job);
}
export function exportKindLabel(kind?: string, resultFormat?: string) {
  if (kind === "review_export" && resultFormat === "print_engine_zip")
    return "CMYK 출력 시험 ZIP";
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
        unavailable: "파일 사용 불가",
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
