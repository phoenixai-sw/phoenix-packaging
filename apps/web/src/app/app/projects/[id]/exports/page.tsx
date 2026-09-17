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
import { api, errorMessage } from "@/lib/api";
import { canRetryReviewExport, isExportInProgress } from "@/lib/export-state";
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
};
const statusLabel: Record<string, string> = {
  queued: "대기 중",
  running: "만드는 중",
  validating: "확인 중",
  succeeded: "출력 완료",
  failed: "출력 실패",
  canceled: "취소됨",
  reconciliation_required: "결과 확인 필요",
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
        if (
          result.items.some((job) => isExportInProgress(job.status))
        )
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
    setBusy(jobId);
    try {
      await api(`/jobs/${jobId}/retry`, { method: "POST" });
      setRetry((n) => n + 1);
    } catch (e) {
      setError(errorMessage(e));
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
        <h1>출력 파일 이력</h1>
        <p>
          {project?.name || "프로젝트"} · 저장된 출력 결과와 처리 상태를
          확인하세요.
        </p>
      </div>
      <div className="alert alert-info">
        <FileText size={18} />
        <span>
          검토용 PDF는 제작 파일을 대신하지 않습니다. 제작용 번들은 해당 출력
          당시의 승인과 검수 조건을 확인하세요. 기존 파일 다운로드에는 크레딧이
          차감되지 않습니다.
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
          <h3>아직 출력한 파일이 없어요.</h3>
          <p>편집기에서 검토용 PDF를 만들어 보세요.</p>
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
                  {job.kind === "production_export"
                    ? "제작용 번들"
                    : "검토용 PDF"}{" "}
                  <span>#{jobs.length - index}</span>
                </h3>
                <p>
                  {new Date(job.created_at).toLocaleString("ko-KR")} ·{" "}
                  {statusLabel[job.status] || job.status}
                </p>
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
                  {canEdit(session) && (
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
                    <Download size={16} /> 다운로드
                  </a>
                </div>
              ) : canRetryReviewExport(job) ? (
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
                  다시 출력
                </button>
              ) : job.status === "failed" ? (
                <Link className="button button-light button-sm" href={`/app/projects/${id}/editor`}>
                  디자인 검수와 새 견적
                </Link>
              ) : isExportInProgress(job.status) ? (
                <span className="export-history-pending">
                  <LoaderCircle className="spin" size={17} /> 작업 중
                </span>
              ) : (
                <span className="pill">{statusLabel[job.status] || job.status}</span>
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
