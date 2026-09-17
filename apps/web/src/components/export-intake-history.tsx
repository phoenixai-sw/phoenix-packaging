"use client";
import { useEffect, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { apiRequest, type ApiSchema } from "@/lib/api-contract";
import { errorMessage } from "@/lib/api";
import { INTAKE_NOTICE, intakeRecordLabel, intakeStatusLabel } from "@/lib/export-intake-state";
import styles from "./export-intake-history.module.css";

type Summary = ApiSchema<"ExportCurrentIntake">;
type Item = ApiSchema<"PrinterIntakeHistoryItem">;

export function ExportIntakeSummary({ summary, onOpen }: {
  summary?: Summary | null;
  onOpen: () => void;
}) {
  return <section className={styles.summary} aria-label="제조사 입고 기록 상태">
    <strong>제조사 회신 · 사용자 기록</strong>
    <p>{summary ? intakeStatusLabel(summary.manufacturer) : "입고 상태 확인 필요"}
      {summary?.manufacturer.record_count ? ` · ${summary.manufacturer.record_count}건` : ""}</p>
    {summary?.manufacturer.technical_rejected && <p>같은 출력본의 기술 반려 이력이 있습니다. 이후 수락 기록이 있어도 이 경고는 유지됩니다.</p>}
    <p>내부 시험: {summary ? intakeStatusLabel(summary.test) : "확인 필요"}
      {summary?.test.record_count ? ` · ${summary.test.record_count}건` : ""}</p>
    {!!summary?.unclassified_count && <p>출처 미확인 {summary.unclassified_count}건 · 제조사 회신과 시험 결과에 포함하지 않습니다.</p>}
    <p>{INTAKE_NOTICE}</p>
    <button className="text-link" onClick={onOpen}>입고 기록 보기{summary ? ` (${summary.total_count})` : ""}</button>
  </section>;
}

export function ExportIntakeHistory({ jobId }: { jobId: string }) {
  const [items, setItems] = useState<Item[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [total, setTotal] = useState<number>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const mounted = useRef(false);
  const fetching = useRef(false);
  useEffect(() => {
    mounted.current = true;
    let active = true;
    setLoading(true);
    setError("");
    fetching.current = true;
    void apiRequest("get", "/v1/jobs/{job_id}/printer-intakes", { path: { job_id: jobId }, query: { limit: 30 } })
      .then((result) => {
        if (!active) return;
        setItems(result.items); setNext(result.next_cursor); setTotal(result.total);
      })
      .catch((e: unknown) => { if (active) setError(errorMessage(e)); })
      .finally(() => { if (active) { fetching.current = false; setLoading(false); } });
    return () => { active = false; mounted.current = false; };
  }, [jobId, retry]);

  async function more() {
    if (!next || fetching.current) return;
    fetching.current = true; setLoading(true); setError("");
    try {
      const result = await apiRequest("get", "/v1/jobs/{job_id}/printer-intakes", {
        path: { job_id: jobId }, query: { limit: 30, before: next },
      });
      if (!mounted.current) return;
      setItems((previous) => {
        const existing = new Set(previous.map((item) => item.id));
        return [...previous, ...result.items.filter((item) => !existing.has(item.id))];
      });
      setNext(result.next_cursor); setTotal(result.total);
    } catch (e) { if (mounted.current) setError(errorMessage(e)); }
    finally { if (mounted.current) { fetching.current = false; setLoading(false); } }
  }

  return <div className={styles.history}>
    <p>{INTAKE_NOTICE} 기록은 해당 출력본에 연결되며 나중에 파일 상태가 달라져도 보존됩니다.</p>
    {error && <div className="alert alert-error" role="alert">{error}
      <button className="text-link" disabled={loading} onClick={() => setRetry((value) => value + 1)}>다시 불러오기</button>
    </div>}
    {total !== undefined && <p>전체 {total}건 · 최근 기록부터 표시</p>}
    <ol className={styles.items}>{items.map((item) => {
      const label = intakeRecordLabel(item);
      return <li className={styles.item} key={item.id}>
        <strong>{label.source} · {label.status}</strong>
        <small>{new Date(item.created_at).toLocaleString("ko-KR")} · 기록 ID {item.id}</small>
        <p>제조사: {item.manufacturer}</p><p>{item.notes}</p>
        <small>분류: {({ geometry: "치수·구조", font: "글꼴", color: "색상", resolution: "해상도", content: "내용", file: "파일", other: "기타" } as Record<string, string>)[item.category] || item.category}
          {item.evidence_attached ? " · 증빙 연결됨 (진위 미검증)" : " · 연결된 증빙 없음"}</small>
      </li>;
    })}</ol>
    {!loading && !error && !items.length && <p>이 출력본에 연결된 입고 기록이 없습니다.</p>}
    {loading && <p role="status"><LoaderCircle className="spin" size={17} /> 입고 기록을 불러오는 중입니다.</p>}
    {next && <button className="button button-light" disabled={loading} onClick={() => void more()}>이전 기록 더 보기</button>}
  </div>;
}
