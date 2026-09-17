"use client";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Check,
  Download,
  FileText,
  LoaderCircle,
  RotateCcw,
} from "lucide-react";
import { api, ApiError, errorMessage } from "@/lib/api";
import {
  canRetryExport,
  canRecordPrinterIntake,
  exportKindLabel,
  exportStatusLabel,
  isExportInProgress,
} from "@/lib/export-state";
import { Dialog } from "@/components/management";
import { PrinterIntake } from "@/components/printer-intake";
import { useSession } from "@/components/workspace";
import { canEdit } from "@/lib/business";
import type { Project } from "@editor/model";
type ExportJob = {
  id: string;
  kind?: string;
  status: string;
  created_at: string;
  error?: { message?: string } | string;
  download_url?: string;
  result?: {
    revision_number?: number;
    asset_count?: number;
    font_count?: number;
    byte_size?: number;
    rights_notice?: string;
  };
};
export default function Exports({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const session = useSession();
  const [intakeJob, setIntakeJob] = useState<string>();
  const [notice, setNotice] = useState("");
  const [project, setProject] = useState<Project>();
  const [jobs, setJobs] = useState<ExportJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const [p, result] = await Promise.all([
          api<Project>(`/projects/${id}`),
          api<{ items: ExportJob[] }>(`/projects/${id}/exports`),
        ]);
        if (!active) return;
        setProject(p);
        setJobs(result.items);
        setError("");
        if (result.items.some((job) => isExportInProgress(job.status)))
          timer = setTimeout(load, 2500);
      } catch (e) {
        if (active) setError(errorMessage(e));
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [id, retry]);
  async function retryExport(jobId: string) {
    const job = jobs.find((j) => j.id === jobId);
    if (!canEdit(session) || !job || !canRetryExport(job)) return;
    setError("");
    setBusy(jobId);
    try {
      await api(`/jobs/${jobId}/retry`, { method: "POST" });
      setRetry((n) => n + 1);
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 423
          ? "다른 편집창에서 작업 중입니다. 해당 창의 검수와 출력에서 다시 시도하거나 편집 권한을 반납한 뒤 요청해 주세요."
          : errorMessage(e),
      );
    } finally {
      setBusy(null);
    }
  }
  return (
    <main className="dashboard-content exports-content">
      <Link className="text-link muted" href={`/app/projects/${id}/editor`}>
        <ArrowLeft size={16} /> 편집기로 돌아가기
      </Link>
      <div className="page-heading">
        <div className="eyebrow">READY FOR A CLOSER LOOK</div>
        <h1>프로젝트 파일 이력</h1>
        <p>
          {project?.name || "프로젝트"} · 저장된 검토·제작 파일과 편집용 ZIP의
          처리 상태를 확인하세요.
        </p>
      </div>
      <div className="alert alert-info">
        <FileText size={18} />
        <span>
          편집용 ZIP은 저장본과 원본 자산을 보관하는 파일이며 제작 PDF가
          아닙니다. 원본 자산·글꼴의 사용권이 확대되지 않습니다. 검토용 PDF도
          제작 파일을 대신하지 않습니다. 제작용 번들은 해당 출력 당시의 승인과
          검수 조건을 확인하세요. 기존 파일 다운로드에는 크레딧이 차감되지
          않습니다.
        </span>
      </div>
      {notice && (
        <div className="alert alert-info" role="status">
          {notice}
        </div>
      )}
      {error && (
        <div className="alert alert-error" role="alert">
          {error}
          <button className="text-link" onClick={() => setRetry((n) => n + 1)}>
            다시 불러오기
          </button>
        </div>
      )}
      {loading ? (
        <div className="loading-state">
          <LoaderCircle className="spin" /> 파일 이력을 확인하고 있어요.
        </div>
      ) : !jobs.length ? (
        <div className="empty-state">
          <FileText size={30} />
          <h3>아직 준비한 파일이 없어요.</h3>
          <p>편집기에서 검토용 PDF나 편집용 프로젝트 ZIP을 준비하세요.</p>
          <Link
            className="button button-dark"
            href={`/app/projects/${id}/editor`}
          >
            디자인 편집하기
          </Link>
        </div>
      ) : (
        <div className="export-history-list">
          {jobs.map((job, index) => (
            <article className="export-history-row" key={job.id}>
              <span className="export-history-icon">
                <FileText size={23} />
              </span>
              <div>
                <h3>
                  {exportKindLabel(job.kind)}{" "}
                  <span>#{jobs.length - index}</span>
                </h3>
                <p>
                  {new Date(job.created_at).toLocaleString("ko-KR")} ·{" "}
                  {exportStatusLabel(job.status)}
                </p>
                {job.kind === "editable_export" && job.result && (
                  <p>
                    저장본 {job.result.revision_number ?? "—"} · 이미지{" "}
                    {job.result.asset_count ?? 0}개 · 글꼴{" "}
                    {job.result.font_count ?? 0}개
                    {typeof job.result.byte_size === "number"
                      ? ` · ${(job.result.byte_size / 1024 / 1024).toFixed(1)} MiB`
                      : ""}{" "}
                    · 0크레딧
                  </p>
                )}
                {job.kind === "editable_export" &&
                  job.result?.rights_notice && (
                    <p>{job.result.rights_notice}</p>
                  )}
                {job.error && (
                  <p className="export-history-error">
                    {typeof job.error === "string"
                      ? job.error
                      : job.error.message || "출력 중 문제가 발생했습니다."}
                  </p>
                )}
              </div>
              {job.status === "succeeded" ? (
                <div className="export-row-actions">
                  {canEdit(session) && canRecordPrinterIntake(job) && (
                    <button
                      className="button button-light button-sm"
                      onClick={() => setIntakeJob(job.id)}
                    >
                      제조사 입고 기록
                    </button>
                  )}
                  <a
                    className="button button-light button-sm"
                    href={`/api/v1/exports/${job.id}/download`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <Download size={16} />{" "}
                    {job.kind === "editable_export"
                      ? "편집용 ZIP 다운로드"
                      : "다운로드"}
                  </a>
                </div>
              ) : canRetryExport(job) ? (
                <button
                  className="button button-light button-sm"
                  onClick={() => void retryExport(job.id)}
                  disabled={busy === job.id || !canEdit(session)}
                >
                  {busy === job.id ? (
                    <LoaderCircle className="spin" size={15} />
                  ) : (
                    <RotateCcw size={15} />
                  )}{" "}
                  파일 준비 다시 시도
                </button>
              ) : job.status === "failed" ? (
                <Link
                  className="button button-light button-sm"
                  href={`/app/projects/${id}/editor`}
                >
                  디자인 검수와 새 견적
                </Link>
              ) : isExportInProgress(job.status) ? (
                <span className="export-history-pending">
                  <LoaderCircle className="spin" size={17} /> 작업 중
                </span>
              ) : (
                <span className="pill">{exportStatusLabel(job.status)}</span>
              )}
            </article>
          ))}
        </div>
      )}
      {intakeJob && (
        <Dialog
          title="제조사 입고 결과"
          onClose={() => setIntakeJob(undefined)}
        >
          <PrinterIntake
            projectId={id}
            jobId={intakeJob}
            onDone={() => {
              setIntakeJob(undefined);
              setNotice("입고 결과를 기록했습니다.");
            }}
          />
        </Dialog>
      )}
    </main>
  );
}
