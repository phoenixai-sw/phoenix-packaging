"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
type Job = { id: string; status: string; kind?: string };
export function EditorActivity({
  projectId,
  onAI,
}: {
  projectId: string;
  onAI: () => void;
}) {
  const [credits, setCredits] = useState<number>(),
    [lowBalance, setLowBalance] = useState(false),
    [jobs, setJobs] = useState<Job[]>([]),
    [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      const result = await Promise.allSettled([
        api<{ balance: number; low_balance?: { active: boolean } | null }>("/credits"),
        api<{ items: Job[] }>(`/projects/${projectId}/generations`),
        api<{ items: Job[] }>(`/projects/${projectId}/exports`),
      ]);
      if (!active) return;
      setFailed(result.some((r) => r.status === "rejected"));
      if (result[0].status === "fulfilled") {
        setCredits(result[0].value.balance);
        setLowBalance(!!result[0].value.low_balance?.active);
      }
      setJobs(
        result
          .slice(1)
          .flatMap((r) =>
            r.status === "fulfilled" && "items" in r.value
              ? r.value.items || []
              : [],
          ),
      );
    };
    void refresh();
    const timer = setInterval(refresh, 10000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [projectId]);
  const running = jobs.filter((j) =>
      ["queued", "running", "processing", "cancel_requested"].includes(
        j.status,
      ),
    ),
    errors = jobs.filter((j) =>
      ["failed", "partially_succeeded", "reconciliation_required"].includes(
        j.status,
      ),
    );
  return (
    <div className="editor-activity">
      <Link href="/app/billing">
        남은 크레딧{" "}
        <strong>
          {credits === undefined ? "—" : credits.toLocaleString()}
        </strong>{" "}
        · 충전/결제
        {lowBalance && (
          <em className="low-balance" role="status">
            잔액 20% 이하 · 충전 또는 요금제 확인
          </em>
        )}
      </Link>
      <button onClick={onAI}>
        작업 {running.length ? `${running.length}건 진행 중` : "대기 없음"}
      </button>
      <Link href={`/app/projects/${projectId}/exports`} target="_blank">
        출력·파일 이력{errors.length ? ` · 확인할 작업 ${errors.length}건` : ""}
      </Link>
      {failed && <span>일부 상태를 새로 확인하지 못했습니다.</span>}
    </div>
  );
}
