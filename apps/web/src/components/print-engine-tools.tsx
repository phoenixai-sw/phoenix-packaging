"use client";
import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { Feedback } from "./management";

type Profile = { id: string; name: string; test_only: boolean; requirements: { bleed_mm: number; layout: string; min_ppi: number } };
type Job = { id: string; status: string; download_url?: string | null; error?: string | null; result?: { format?: string } | null };
export function PrintEngineTools({ projectId, saveCurrent, readOnly }: {
  projectId: string; saveCurrent: () => Promise<number>; readOnly: boolean;
}) {
  const [profiles, setProfiles] = useState<Profile[]>([]), [profile, setProfile] = useState("");
  const [job, setJob] = useState<Job>(), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const inFlight = useRef(false), mounted = useRef(true), editable = useRef(!readOnly);
  editable.current = !readOnly;
  useEffect(() => { mounted.current = true; let active = true;
    api<{ items: Profile[] }>("/print-engine/profiles").then(r => { if (active) { setProfiles(r.items); setProfile(r.items[0]?.id || ""); } }).catch(e => { if (active) setError(errorMessage(e)); });
    return () => { active = false; mounted.current = false; };
  }, []);
  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    let active = true;
    const timer = setInterval(() => { api<Job>(`/jobs/${job.id}`).then(r => { if (active) setJob(r); }).catch(e => { if (active) setError(errorMessage(e)); }); }, 2000);
    return () => { active = false; clearInterval(timer); };
  }, [job]);
  async function create() {
    if (inFlight.current || !editable.current || !profile) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      const revision = await saveCurrent();
      if (!editable.current) throw new Error("편집 권한이 변경되었습니다. 다시 확인해 주세요.");
      const next = await api<Job>("/print-engine/tests", { method: "POST", body: JSON.stringify({ project_id: projectId, base_revision: revision, profile_id: profile }) });
      if (mounted.current) setJob(next);
    } catch (e) { if (mounted.current) setError(errorMessage(e)); }
    finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  }
  async function retry() {
    if (!job || inFlight.current || !editable.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try { setJob(await api<Job>(`/jobs/${job.id}/retry`, { method: "POST" })); }
    catch (e) { setError(errorMessage(e)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  const selected = profiles.find(p => p.id === profile);
  return <section className="panel stack" aria-label="CMYK 출력 시험">
    <h3>CMYK 출력 시험</h3>
    <p>저장된 디자인을 ICC 색 변환·한글 윤곽선·CUT/FOLD 분리 파일로 확인합니다. 무료 시험 ZIP에는 제작 사용 불가 표시가 들어갑니다.</p>
    <label>시험 출력 조건<select value={profile} onChange={e => setProfile(e.target.value)} disabled={busy || readOnly}>{profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    {selected && <p className="muted">도련 {selected.requirements.bleed_mm}mm · 최소 {selected.requirements.min_ppi}ppi. 합성 ICC는 변환 시험 전용입니다. 등록 전개도가 없는 스탠드 파우치·박스는 먼저 등록 구조를 적용해 주세요.</p>}
    <p className="muted">PDF/X·별색·화이트 잉크·반투명·구멍/지퍼/노치 가공은 지원하지 않습니다. 제작용 출력은 기존 제조 조건 승인과 검수를 모두 통과해야 합니다.</p>
    <button className="button" onClick={create} disabled={busy || readOnly || !profile || !!job && ["queued", "running"].includes(job.status)}>{busy ? "저장·요청 중…" : "무료 CMYK 시험 ZIP 만들기"}</button>
    <Feedback error={error || (job?.status === "failed" ? job.error || "시험 출력에 실패했습니다." : "")} />
    {job && <div role="status">{job.status === "succeeded" ? "시험 출력 완료" : job.status === "failed" ? "출력 실패" : job.status === "canceled" ? "출력 취소" : "파일 생성 중…"}
      {job.status === "succeeded" && job.download_url && <p><a className="button" href={job.download_url.startsWith("/v1/") ? `/api${job.download_url}` : job.download_url}>시험 ZIP 다운로드</a></p>}
      {job.status === "failed" && <button className="button" disabled={busy || readOnly} onClick={retry}>같은 저장본 다시 시도</button>}
    </div>}
  </section>;
}
