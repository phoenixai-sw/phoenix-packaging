"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, errorMessage } from "@/lib/api";
import { Feedback } from "./management";
type Overview = {
  project_count: number;
  job_counts: { kind: string; status: string; count: number }[];
  timeline: { id: string; type: string; project_id: string; project_name: string; revision: number; status: string; at: string }[];
};
const labels: Record<string, string> = { revision: "저장", review_export: "검토 PDF", production_export: "제작 출력", ai_generation: "AI 이미지", queued: "대기", running: "처리 중", waiting_provider: "생성 중", validating: "검사 중", succeeded: "완료", partially_succeeded: "일부 완료", failed: "실패", canceled: "취소", saved: "저장됨", reconciliation_required: "사용량 확인 중" };
export function DashboardActivity() {
  const [overview, setOverview] = useState<Overview>(), [wallet, setWallet] = useState<{ available: number; reserved: number }>();
  const [error, setError] = useState(""), [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    async function refresh() {
      const results = await Promise.allSettled([api<Overview>("/workspace/overview"), api<{ available: number; reserved: number }>("/credits")]);
      if (!active) return;
      const [activity, balance] = results;
      if (activity.status === "fulfilled") setOverview(activity.value);
      if (balance.status === "fulfilled") setWallet(balance.value);
      const failure = results.find(r => r.status === "rejected");
      setError(failure?.status === "rejected" ? errorMessage(failure.reason) : "");
    }
    void refresh();
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 30000);
    return () => { active = false; clearInterval(timer); };
  }, [retry]);
  const working = overview?.job_counts.filter(j => ["queued", "running", "waiting_provider", "validating"].includes(j.status)).reduce((sum, j) => sum + j.count, 0);
  const needsAttention = overview?.job_counts.filter(j => ["failed", "reconciliation_required", "partially_succeeded"].includes(j.status)).reduce((sum, j) => sum + j.count, 0);
  return <section className="dashboard-activity" aria-label="작업 현황과 최근 이력">
    <div className="workspace-metrics">
      <Link href="/app/billing"><span>사용 가능 크레딧</span><strong>{wallet?.available ?? "—"}</strong><small>예약 {wallet?.reserved ?? "—"}</small></Link>
      <div><span>진행 중 작업</span><strong>{working ?? "—"}</strong><small>AI 생성·PDF 출력</small></div>
      <div><span>확인이 필요한 이력</span><strong>{needsAttention ?? "—"}</strong><small>전체 이력의 실패·일부 완료·사용량 확인</small></div>
    </div>
    <Feedback error={error} />
    {error && <button className="button button-light" onClick={() => setRetry(n => n + 1)}>현황 다시 불러오기</button>}
    <details className="activity-timeline" open><summary>최근 저장·AI·출력 이력</summary>
      {!overview ? <p role="status">작업 현황을 불러오는 중…</p> : !overview.timeline.length ? <p>아직 저장·작업 이력이 없습니다.</p> :
        <ul>{overview.timeline.map(item => <li key={`${item.type}:${item.id}`}>
          <Link href={`/app/projects/${item.project_id}/${item.type.endsWith("_export") ? "exports" : "editor"}`}>
            <strong>{item.project_name}</strong><span>{labels[item.type] || item.type} · 버전 {item.revision} · {labels[item.status] || item.status}</span>
            <time dateTime={item.at}>{new Date(item.at).toLocaleString("ko-KR")}</time>
          </Link>
        </li>)}</ul>}
    </details>
  </section>;
}
