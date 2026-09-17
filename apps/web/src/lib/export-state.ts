export function canRetryReviewExport(job: { kind?: string; status: string }) {
  return job.kind === "review_export" && job.status === "failed";
}

export function isExportInProgress(status: string) {
  return ["queued", "running", "validating"].includes(status);
}
