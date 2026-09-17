"use client";
import { useRef, useState } from "react";
import Link from "next/link";
import { ManagementPage, Feedback, Loading } from "./management";
import { useApiData, dateTime, roleOf } from "@/lib/business";
import { api, errorMessage } from "@/lib/api";
import { useSession } from "./workspace";

type TargetKind = "tenant" | "project" | "asset" | "export";
type Hold = { id: string; tenant_id: string; target_kind: TargetKind; target_id: string; reason: string; reason_code: string; revision: number; released_at: string | null };
type Notice = { id: string; due_at: string; stage_days: number; status: string };
type Deletion = { id: string; tenant_id: string; target_kind: TargetKind; target_id: string; reason: string; status: string; review_reason: string | null; revision: number; blockers: string[]; created_at: string; due_at: string | null; executed_at: string | null; cancelable: boolean; execution_supported: boolean };
type Support = { id: string; tenant_id: string; target_kind: TargetKind; target_id: string; reason: string; expires_at: string; revoked_at: string | null };
type Audit = { id: string; action: string; created_at: string; details: { reason?: string; target_id?: string } };
type Retention = { protected_until: string | null; state: string; holds: Hold[]; notices: Notice[]; deletion_capabilities: { customer_data_execution: boolean; known_orphan_execution: boolean } };
type Backup = { id: string; state: string; verified: boolean; object_count: number; created_at: string };
type Operations = { requests: Deletion[]; holds: Hold[]; support_sessions: Support[]; backups: Backup[]; gc_candidates: Array<{ id: string; status: string; blocker: string | null; byte_size: number }>; recent_audit: Audit[]; known_orphan_execution: boolean; customer_data_execution: boolean };
const targetNames: Record<TargetKind, string> = { tenant: "조직 전체", project: "프로젝트", asset: "이미지 원본", export: "출력 파일" };
const stateNames: Record<string, string> = { active: "유료 기간 중 보관", protected: "보관 기간 내", unspecified: "보관 기한 미정 · 자동 삭제 없음", expired_retained: "보관 기준일 경과 · 자료 유지 중", requested: "검토 대기", approved: "검토 승인 · 유예/보존 상태 확인", executing: "파일 삭제 실행 중", executed: "파일 삭제 완료 · 이력 보존", attention_required: "실행 중단 · 운영 확인 필요", rejected: "반려", canceled: "요청 취소" };
function useOperation(refresh: () => void) {
  const active = useRef(false), [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  async function run(work: () => Promise<unknown>, success: string) {
    if (active.current) return; active.current = true; setBusy(true); setError(""); setNotice("");
    try { await work(); refresh(); setNotice(success); } catch (e) { setError(errorMessage(e)); }
    finally { active.current = false; setBusy(false); }
  }
  return { busy, error, notice, run };
}
function ReasonField({ value, onChange, disabled }: { value: string; onChange: (v: string) => void; disabled: boolean }) {
  return <label>처리 사유<textarea value={value} onChange={e => onChange(e.target.value)} minLength={5} maxLength={1000} disabled={disabled} placeholder="고객 요청과 처리 사유를 5자 이상 기록해 주세요." /></label>;
}
function Targets({ kind, setKind, id, setId, disabled }: { kind: TargetKind; setKind: (v: TargetKind) => void; id: string; setId: (v: string) => void; disabled: boolean }) {
  return <><label>대상<select value={kind} disabled={disabled} onChange={e => setKind(e.target.value as TargetKind)}>{Object.entries(targetNames).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>대상 ID<input value={id} onChange={e => setId(e.target.value)} disabled={disabled} placeholder="프로젝트·이미지·출력 파일의 ID" required /></label></>;
}

export function RetentionPage() {
  const session = useSession(), info = useApiData<Retention>("/retention"), requests = useApiData<{items: Deletion[]; next_offset: number | null}>("/deletion-requests?limit=100"), access = useApiData<{items: Audit[]; next_offset: number | null}>("/retention/access-history?limit=100");
  const [kind,setKind] = useState<TargetKind>("project"), [id,setId] = useState(""), [reason,setReason] = useState("");
  const operation = useOperation(() => { info.refresh(); requests.refresh(); access.refresh(); });
  const pending = useRef<{ body: string; key: string } | null>(null);
  const canManage = roleOf(session) === "owner";
  async function submit() {
    const body = JSON.stringify({target_kind:kind,target_id:kind === "tenant" ? session?.tenant.id : id.trim(),reason:reason.trim()});
    if (pending.current?.body !== body) pending.current = {body,key:crypto.randomUUID()};
    const stable = pending.current;
    await operation.run(async () => { await api("/deletion-requests", {method:"POST",headers:{"Idempotency-Key":stable.key},body:stable.body}); pending.current=null; }, "삭제 검토 요청을 접수했습니다. 자료는 그대로 보존됩니다.");
  }
  return <ManagementPage eyebrow="보관" title="자료 보관과 지원 기록" description="원본·저장본·출력물의 보관 상태를 확인하고 삭제 검토를 요청합니다. 개별 이미지·출력 파일은 관리자 승인 후 7일 유예와 보호 검사 후 삭제할 수 있습니다.">
    <Feedback error={operation.error || info.error || requests.error || access.error} notice={operation.notice} />
    {!canManage ? <p>보관·삭제 요청은 조직 소유자가 관리합니다.</p> : <>
      {info.loading ? <Loading /> : info.data && <section className="management-card"><h2>{stateNames[info.data.state] || info.data.state}</h2><p>보관 기준일: {info.data.protected_until ? dateTime(info.data.protected_until) : "미정"}</p><p>개별 파일 요청 실행: {info.data.deletion_capabilities.customer_data_execution ? "승인·유예 후 보호 검사" : "꺼짐"}</p><p>기준일이 지나도 현재 자료가 자동 삭제되지는 않습니다. 구독 종료 후에도 소유자는 기존 자료를 확인할 수 있습니다.</p><Link href="/app" className="button secondary">프로젝트·파일 다운로드</Link>
        {info.data.notices.map(n => <div key={n.id} className="alert alert-info"><span>{n.stage_days === 0 ? "보관 기준일이 지났습니다." : `보관 기준일 ${n.stage_days}일 전 알림입니다.`} {dateTime(n.due_at)} · 앱 내 알림</span>{n.status === "unread" && <button disabled={operation.busy} onClick={() => void operation.run(() => api(`/notices/${n.id}/read`,{method:"POST"}),"알림을 읽음으로 표시했습니다.")}>읽음</button>}</div>)}
        {info.data.holds.map(h => <p key={h.id}><strong>보존 중:</strong> {targetNames[h.target_kind]} {h.target_id} — {h.reason}</p>)}
      </section>}
      <section className="management-card"><h2>삭제 검토 요청</h2><p>요청 대상은 검토 중 보존됩니다. 개별 이미지·완료 출력은 승인 후 최소 7일이 지나고 참조·분쟁·백업 보호가 없을 때만 삭제됩니다. 실행 전에는 취소할 수 있습니다. 조직·프로젝트 전체 삭제는 별도 수동 계획이 필요하며 여기서 실행하지 않습니다. 기존 백업 사본과 결제·작업 이력은 별도로 보존됩니다.</p><form onSubmit={e => {e.preventDefault();void submit();}}><Targets kind={kind} setKind={setKind} id={kind === "tenant" ? session?.tenant.id || "" : id} setId={setId} disabled={operation.busy} /><ReasonField value={reason} onChange={setReason} disabled={operation.busy} /><button type="submit" className="button primary" disabled={operation.busy || reason.trim().length < 5 || (kind !== "tenant" && !id.trim())}>검토 요청 접수</button></form></section>
      <section className="management-card"><h2>접수 이력</h2>{requests.data?.items.length === 0 && <p>접수한 요청이 없습니다.</p>}{requests.data?.items.map(r => <article key={r.id} style={{padding:"16px 0",borderBottom:"1px solid var(--border)"}}><strong>{stateNames[r.status] || r.status}</strong><p>{targetNames[r.target_kind]} {r.target_id}</p><p>{r.reason}</p>{r.review_reason && <p>검토 사유: {r.review_reason}</p>}{r.due_at && <p>가장 이른 실행일: {dateTime(r.due_at)} · 보호 상태가 있으면 계속 보존됩니다.</p>}{r.executed_at && <p>실행 완료: {dateTime(r.executed_at)}</p>}{!r.execution_supported && <p>이 범위는 요청·검토만 지원하며 전체 자료를 자동 삭제하지 않습니다.</p>}{r.cancelable && <button disabled={operation.busy} onClick={() => void operation.run(() => api(`/deletion-requests/${r.id}/cancel`,{method:"POST",body:JSON.stringify({revision:r.revision,reason:"소유자가 보관 화면에서 삭제 요청을 취소했습니다."})}),"삭제 요청을 취소했습니다.")}>요청 취소</button>}</article>)}{requests.data?.next_offset !== null && requests.data?.next_offset !== undefined && <p>최신 100건을 표시합니다. 이전 요청은 운영 지원에 문의해 주세요.</p>}</section>
      <section className="management-card"><h2>운영 지원 접근 이력</h2><p>운영 관리자의 접근 사유와 자료 열람이 기록됩니다.</p>{access.data?.items.map(a => <p key={a.id}>{dateTime(a.created_at)} · {a.action.startsWith("support_access") ? "지원 접근 변경" : "지원 자료 열람"} — {a.details.reason || "기록된 지원 사유"} {a.details.target_id}</p>)}{access.data?.items.length === 0 && <p>지원 접근 기록이 없습니다.</p>}</section>
    </>}
  </ManagementPage>;
}

export function OperationsPage() {
  const data = useApiData<Operations>("/admin/retention/overview");
  const operation = useOperation(data.refresh);
  const [tenant,setTenant] = useState(""), [kind,setKind] = useState<TargetKind>("project"), [id,setId] = useState(""), [reason,setReason] = useState("");
  const [code,setCode] = useState("manual"), [preview,setPreview] = useState("");
  const [backupStopped,setBackupStopped] = useState(false);
  const invalid = operation.busy || reason.trim().length < 5;
  const reasonBody = {reason:reason.trim()};
  return <ManagementPage eyebrow="운영" title="보관·지원 운영" description="보존 상태, 삭제 요청 검토, 사유를 기록한 한시적 읽기 접근을 관리합니다. 개별 파일만 유예·참조 보호를 확인한 뒤 실행하며 전체 조직·프로젝트 삭제는 수동 계획이 필요합니다.">
    <Feedback error={operation.error || data.error} notice={operation.notice} />{data.loading ? <Loading /> : data.data && <>
      <section className="management-card"><h2>운영 상태</h2><p>승인한 개별 파일 삭제: {data.data.customer_data_execution ? "7일 유예 후 보호 검사와 함께 실행" : "실행 꺼짐 · 승인해도 현재 보존"}</p><p>알려진 미게시 작업 파일 정리: {data.data.known_orphan_execution ? "서버 정책에 따라 실행" : "관찰만 · 자동 삭제 꺼짐"}</p><p>기존 이미지·저장본·출력물, 보존 hold, 진행 작업, 백업은 삭제 대상에서 제외됩니다.</p></section>
      <section className="management-card"><h2>처리 사유와 대상</h2><label>조직 ID<input value={tenant} onChange={e => setTenant(e.target.value)} disabled={operation.busy}/></label><Targets kind={kind} setKind={setKind} id={id} setId={setId} disabled={operation.busy}/><ReasonField value={reason} onChange={setReason} disabled={operation.busy}/><label>보존 사유<select value={code} onChange={e => setCode(e.target.value)} disabled={operation.busy}><option value="manual">운영 보관</option><option value="dispute">분쟁 보존</option><option value="support">지원 조사</option></select></label><div className="actions"><button disabled={invalid || !tenant || !id} onClick={() => void operation.run(() => api("/admin/retention/holds",{method:"POST",body:JSON.stringify({tenant_id:tenant,target_kind:kind,target_id:id,...reasonBody,reason_code:code})}),"보존 상태를 추가했습니다.")}>보존 hold 추가</button><button disabled={invalid || !tenant || !id} onClick={() => void operation.run(() => api("/admin/support-sessions",{method:"POST",body:JSON.stringify({tenant_id:tenant,target_kind:kind,target_id:id,...reasonBody})}),"30분 지원 읽기 권한을 시작했습니다. 열람은 별도로 기록됩니다.")}>30분 지원 읽기 시작</button></div></section>
      <section className="management-card"><h2>삭제 요청 검토</h2>{data.data.requests.map(r => <article key={r.id} style={{padding:"16px 0"}}><strong>{stateNames[r.status] || r.status}</strong><p>조직 {r.tenant_id} · {targetNames[r.target_kind]} {r.target_id}</p><p>{r.reason}</p>{r.due_at && <p>가장 이른 실행일 {dateTime(r.due_at)}</p>}{r.blockers.length > 0 && <p>보존·처리 사유: {r.blockers.join(" · ")}</p>}{r.status === "attention_required" && <button disabled={invalid} onClick={() => void operation.run(() => api(`/admin/deletion-requests/${r.id}/retry`,{method:"POST",body:JSON.stringify({revision:r.revision,...reasonBody})}),"원본 식별과 보호 상태를 다시 검사하도록 기록했습니다.")}>실행 재확인 요청</button>}{r.status === "requested" && <div className="actions">{(["approved","rejected"] as const).map(decision => <button key={decision} disabled={invalid} onClick={() => void operation.run(() => api(`/admin/deletion-requests/${r.id}/review`,{method:"POST",body:JSON.stringify({revision:r.revision,decision,...reasonBody})}),"검토 상태를 기록했습니다. 즉시 삭제되지 않으며 유예기간과 보호 상태를 확인합니다.")}>{decision === "approved" ? r.execution_supported ? "승인 · 7일 유예" : "수동 검토 승인" : "반려"}</button>)}</div>}</article>)}</section>
      <section className="management-card"><h2>보존 hold</h2>{data.data.holds.map(h => <p key={h.id}>{h.tenant_id} · {h.reason} · {h.released_at ? "해제됨" : "보존 중"} {!h.released_at && h.reason_code !== "deletion_request" && <button disabled={invalid} onClick={() => void operation.run(() => api(`/admin/retention/holds/${h.id}/release`,{method:"POST",body:JSON.stringify({revision:h.revision,...reasonBody})}),"해제 사유를 기록했습니다.")}>보존 해제</button>}</p>)}</section>
      <section className="management-card"><h2>지원 읽기 권한</h2>{data.data.support_sessions.map(s => <article key={s.id} style={{padding:"12px 0"}}><p>{s.reason} · {targetNames[s.target_kind]} {s.target_id} · {s.revoked_at ? "철회됨" : `만료 ${dateTime(s.expires_at)}`}</p>{!s.revoked_at && Date.parse(s.expires_at) > Date.now() && <div className="actions">{s.target_kind === "project" && <button disabled={operation.busy} onClick={() => void operation.run(async () => {const result=await api(`/admin/support-sessions/${s.id}/projects/${s.target_id}`);setPreview(JSON.stringify(result,null,2));},"프로젝트 열람 사유를 감사 기록에 저장했습니다.")}>프로젝트 읽기</button>}{s.target_kind === "asset" && <a href={`/api/v1/admin/support-sessions/${s.id}/assets/${s.target_id}/content`} target="_blank" rel="noreferrer">이미지 읽기 · 기록됨</a>}{s.target_kind === "export" && <a href={`/api/v1/admin/support-sessions/${s.id}/exports/${s.target_id}/download`} target="_blank" rel="noreferrer">출력 다운로드 · 기록됨</a>}<button disabled={invalid} onClick={() => void operation.run(() => api(`/admin/support-sessions/${s.id}/revoke`,{method:"POST",body:JSON.stringify(reasonBody)}),"지원 접근을 철회했습니다.")}>접근 종료</button></div>}</article>)}{preview && <details open><summary>감사된 읽기 결과</summary><pre style={{maxHeight:360,overflow:"auto",whiteSpace:"pre-wrap"}}>{preview}</pre><button onClick={() => setPreview("")}>읽기 결과 닫기</button></details>}</section>
      <section className="management-card"><h2>백업 보호</h2><label><input type="checkbox" checked={backupStopped} onChange={e => setBackupStopped(e.target.checked)} />24시간 이상 응답 없는 백업 프로세스가 실제 종료되었음을 확인했습니다.</label>{data.data.backups.map(b => <p key={b.id}>{dateTime(b.created_at)} · {b.state} · {b.object_count}개 객체 · {b.verified ? "검증 완료" : "검증 미완료"} {(b.state === "attention_required" || (["pinning","copying","verifying"].includes(b.state) && Date.parse(b.created_at) < Date.now()-86400000 && backupStopped)) && <button disabled={invalid} onClick={() => void operation.run(() => api(`/admin/retention/backups/${b.id}/abandon`,{method:"POST",body:JSON.stringify({...reasonBody,process_stopped:backupStopped})}),"백업 포기 사유를 기록하고 해당 보호를 해제했습니다.")}>중단 확인 후 보호 해제</button>}</p>)}</section>
      <section className="management-card"><h2>미게시 작업 파일 관찰</h2>{data.data.gc_candidates.map(c => <p key={c.id}>{c.status} · {Math.round(c.byte_size/1024)} KiB {c.blocker && `· ${c.blocker}`}</p>)}{data.data.gc_candidates.length === 0 && <p>기록된 정리 후보가 없습니다. 알 수 없는 파일은 탐색·삭제하지 않습니다.</p>}</section>
      <section className="management-card"><h2>최근 감사 기록</h2>{data.data.recent_audit.map(a => <p key={a.id}>{dateTime(a.created_at)} · {a.action} · {a.details.reason || "시스템 처리 기록"}</p>)}</section>
    </>}
  </ManagementPage>;
}
