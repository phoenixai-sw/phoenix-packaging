"use client";
import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { Feedback } from "./management";

type Profile = { id: string; name: string; test_only: boolean; print_request_available?: boolean; requirements: { bleed_mm: number; layout: string; min_ppi: number } };
type Mode = "engine_test" | "print_request";
type Job = { id: string; status: string; download_url?: string | null; error?: string | null; result?: { format?: string } | null };
export function PrintEngineTools({ projectId, saveCurrent, readOnly }: {
  projectId: string; saveCurrent: () => Promise<number>; readOnly: boolean;
}) {
  const [profiles, setProfiles] = useState<Profile[]>([]), [profile, setProfile] = useState(""), [mode, setMode] = useState<Mode>("engine_test");
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
  const selected = profiles.find(p => p.id === profile);
  const requestable = profiles.filter(p => p.print_request_available);
  const request = mode === "print_request";
  // A print request needs a real registered ICC; the synthetic engine profile stays test-only.
  const modeBlocked = request && !selected?.print_request_available;
  async function create() {
    if (inFlight.current || !editable.current || !profile || modeBlocked) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      const revision = await saveCurrent();
      if (!editable.current) throw new Error("편집 권한이 변경되었습니다. 다시 확인해 주세요.");
      const next = await api<Job>("/print-engine/tests", { method: "POST", body: JSON.stringify({ project_id: projectId, base_revision: revision, profile_id: profile, mode }) });
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
  const jobIsRequest = job?.result?.format === "print_request_zip";
  return <section className="panel stack" aria-label="CMYK 인쇄 파일">
    <h3>CMYK 인쇄 파일</h3>
    <p>저장된 디자인을 ICC 색 변환·한글 윤곽선·CUT/FOLD 분리 파일로 만듭니다. 칼선을 CutContour·Crease 별색 레이어로 겹친 합본 PDF(artwork-with-dieline.pdf)도 함께 들어갑니다.</p>
    <fieldset className="stack" disabled={busy || readOnly}>
      <legend>파일 종류</legend>
      <label><input type="radio" name="print-mode" value="engine_test" checked={!request} onChange={() => setMode("engine_test")} /> 무료 CMYK 시험 ZIP — 모든 페이지에 “제작 사용 불가” 표시</label>
      <label><input type="radio" name="print-mode" value="print_request" checked={request} onChange={() => setMode("print_request")} /> 인쇄 의뢰본 ZIP — 표시 없이 인쇄소에 보낼 수 있는 파일 (제조사 확인 전)</label>
    </fieldset>
    <label>출력 조건<select value={profile} onChange={e => setProfile(e.target.value)} disabled={busy || readOnly}>{profiles.map(p => <option key={p.id} value={p.id}>{p.name}{p.print_request_available ? "" : " (시험 전용)"}</option>)}</select></label>
    {selected && <p className="muted">도련 {selected.requirements.bleed_mm}mm · 최소 {selected.requirements.min_ppi}ppi. 등록 전개도가 없는 스탠드 파우치·박스는 먼저 등록 구조를 적용해 주세요.</p>}
    {request && requestable.length === 0 && <p className="muted">인쇄 의뢰본에는 실제 ICC 프로필이 필요합니다. 관리자 메뉴에서 인쇄소 ICC(예: Japan Color 2001 Coated)를 등록하고 시험 공개로 켜 주세요.</p>}
    {request && requestable.length > 0 && modeBlocked && <p className="muted">선택한 조건은 합성 시험 ICC입니다. 실제 ICC 프로필을 선택해 주세요.</p>}
    <p className="muted">{request
      ? "인쇄 의뢰본은 저PPI·샘플 바코드 같은 문제를 막지 않고 preflight.json에 경고로 남깁니다. 제조사 도면·재질·색상 승인은 기록되지 않으며 실물 검수는 인쇄소와 진행합니다."
      : "합성 ICC 시험은 원형 걸이 구멍·V/U 노치의 CUT와 개봉부·지퍼 안내를 별도 파일로 포함합니다. 실물 가공 검증은 별도입니다."} PDF/X·화이트 잉크·반투명은 지원하지 않습니다.</p>
    <button className="button" onClick={create} disabled={busy || readOnly || !profile || modeBlocked || !!job && ["queued", "running"].includes(job.status)}>{busy ? "저장·요청 중…" : request ? "인쇄 의뢰본 ZIP 만들기 (0크레딧)" : "무료 CMYK 시험 ZIP 만들기"}</button>
    <Feedback error={error || (job?.status === "failed" ? job.error || "출력에 실패했습니다." : "")} />
    {job && <div role="status">{job.status === "succeeded" ? (jobIsRequest ? "인쇄 의뢰본 완료" : "시험 출력 완료") : job.status === "failed" ? "출력 실패" : job.status === "canceled" ? "출력 취소" : "파일 생성 중…"}
      {job.status === "succeeded" && job.download_url && <p><a className="button" href={job.download_url.startsWith("/v1/") ? `/api${job.download_url}` : job.download_url}>{jobIsRequest ? "인쇄 의뢰본 ZIP 다운로드" : "시험 ZIP 다운로드"}</a></p>}
      {job.status === "failed" && <button className="button" disabled={busy || readOnly} onClick={retry}>같은 저장본 다시 시도</button>}
    </div>}
  </section>;
}
