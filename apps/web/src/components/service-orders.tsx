"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, errorMessage } from "@/lib/api";
import { type ServiceCode, type ServiceOrder, type ServiceCatalogItem, serviceStatus } from "@/lib/service-order-types";
import { useSession } from "./workspace";
import { Feedback, Loading, ManagementPage } from "./management";
import { ServiceCheckout } from "./service-checkout";
import { serviceReturnId } from "@/lib/payment-return-context";
import { servicePaymentLabel, serviceWorkPaymentReady } from "@/lib/service-payment-state";

const won = (value: number) => value.toLocaleString("ko-KR") + "원";
type Catalog = { policy_version: string; items: ServiceCatalogItem[]; notice: string };
type OrderList = { items: ServiceOrder[]; next_cursor: string | null };
export function ServiceOrders({ admin = false }: { admin?: boolean }) {
  const session = useSession();
  const [catalog, setCatalog] = useState<Catalog>();
  const [orders, setOrders] = useState<ServiceOrder[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [projects, setProjects] = useState<Array<{ id: string; name: string }>>([]);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [code, setCode] = useState<ServiceCode>("file_review"), [projectId, setProjectId] = useState(""), [requestNote, setRequestNote] = useState("");
  const [selectedId, setSelectedId] = useState(""), [note, setNote] = useState(""), [understood, setUnderstood] = useState(false);
  const [amount, setAmount] = useState(55000), [scope, setScope] = useState(""), [exclusions, setExclusions] = useState("제조 승인·인쇄·샘플 제작 비용은 포함하지 않습니다."), [days, setDays] = useState(7);
  const createKey = useRef<{ body: string; key: string } | undefined>(undefined);
  const actionLock = useRef(false);
  const allowed = admin ? session?.user.is_admin : session?.user.role === "owner";
  const base = admin ? "/admin/service-orders" : "/service-orders";
  const selected = orders.find((o) => o.id === selectedId);
  async function load(next?: string) {
    const data = await api<OrderList>(base + (next ? `?cursor=${encodeURIComponent(next)}` : ""));
    const detail = !admin && selectedId && !next ? await api<ServiceOrder>(`/service-orders/${selectedId}`) : null;
    const items = detail ? [detail, ...data.items.filter((item) => item.id !== detail.id)] : data.items;
    setOrders((old) => next ? [...old, ...items.filter((item) => !old.some((row) => row.id === item.id))] : items);
    setCursor(data.next_cursor);
  }
  useEffect(() => {
    let live = true;
    if (!allowed) { setLoading(false); return; }
    setLoading(true);
    const returnedServiceId = !admin ? serviceReturnId(new URLSearchParams(window.location.search).get("service_order_id")) : undefined;
    Promise.all([api<Catalog>("/service-catalog"), api<OrderList>(base), admin ? Promise.resolve({ items: [] as Array<{ id: string; name: string }> }) : api<{ items: Array<{ id: string; name: string }> }>("/projects"), returnedServiceId ? api<ServiceOrder>(`/service-orders/${returnedServiceId}`) : Promise.resolve(null)])
      .then(([c, list, p, returned]) => { if (live) { setCatalog(c); setOrders(returned ? [returned, ...list.items.filter((item) => item.id !== returned.id)] : list.items); setCursor(list.next_cursor); setProjects(p.items); if (returned) setSelectedId(returned.id); } })
      .catch((cause) => { if (live) setError(errorMessage(cause)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [allowed, base, admin]);
  async function execute(action: () => Promise<unknown>, success: string, refresh = true) {
    if (actionLock.current) return;
    actionLock.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); if (refresh) await load(); setNotice(success); setUnderstood(false); }
    catch (cause) { setError(errorMessage(cause)); await load().catch(() => undefined); }
    finally { actionLock.current = false; setBusy(false); }
  }
  function choose(order: ServiceOrder) {
    setSelectedId(order.id); setNote(""); setUnderstood(false); setAmount(order.catalog_snapshot.suggested_amount_krw); setScope(order.catalog_snapshot.scope);
  }
  async function create(event: React.FormEvent) {
    event.preventDefault();
    const body = JSON.stringify({ service_code: code, project_id: projectId || null, request_note: requestNote });
    if (!createKey.current || createKey.current.body !== body) createKey.current = { body, key: crypto.randomUUID() };
    const key = createKey.current.key;
    await execute(() => api("/service-orders", { method: "POST", headers: { "Idempotency-Key": key }, body }), "신청을 접수했습니다. 결제나 구독 등록은 실행하지 않았습니다.");
  }
  function orderAction(path: string, body: Record<string, unknown>, message: string) {
    if (!selected) return;
    return execute(() => api(`${path}/${selected.id}/${String(body.action)}`, { method: "POST", body: JSON.stringify(Object.fromEntries(Object.entries({ ...body, base_revision: selected.revision, note }).filter(([k]) => k !== "action"))) }), message);
  }
  const currentQuote = selected?.quotes.find((q) => q.id === selected.current_quote_id);
  const acceptedQuote = selected?.quotes.find((q) => q.id === selected.accepted_quote_id);
  const legacyAccepted = selected?.status === "accepted" && !selected.checkout_terms && !selected.payment_order;
  const canCancel = selected && ["requested", "quoted", "accepted"].includes(selected.status) && (!selected.payment_order || selected.payment_status === "refunded");
  const workOptions = !selected ? [] : ["requested", "quoted"].includes(selected.status) ? ["rejected"] : ({ accepted: ["in_progress"], in_progress: ["delivered"], delivered: ["completed"] } as Record<string, string[]>)[selected.status] || [];
  const paidForWork = !selected || ["requested", "quoted"].includes(selected.status) || serviceWorkPaymentReady({ serviceCode: selected.service_code, paymentStatus: selected.payment_status, hasCheckoutTerms: Boolean(acceptedQuote?.checkout_terms), amount: acceptedQuote?.amount_inc_vat ?? 0 });
  return <ManagementPage eyebrow={admin ? "SERVICE OPERATIONS" : "OPTIONAL SERVICES"} title={admin ? "서비스 신청과 견적 관리" : "별도 서비스 신청"} description="제공 범위와 견적을 확인하고 결제·구독과 업무 진행 상태를 따로 관리합니다." actions={allowed && <button className="button button-light" disabled={busy || loading} onClick={() => void execute(() => load(), "최신 신청·결제 상태를 불러왔습니다.", false)}>신청 내역 새로고침</button>}>
    <Feedback error={error} notice={notice} />
    {!allowed ? <p className="alert alert-info">{admin ? "운영 관리자만 볼 수 있는 화면입니다." : "별도 서비스는 조직 소유자가 신청할 수 있습니다."}</p> : loading ? <Loading /> : <>
      <p className="alert alert-info">{catalog?.notice}</p>
      <nav className="button-row">{admin ? <Link className="button button-light" href="/admin">운영 관리</Link> : <Link className="button button-light" href="/app/billing">구독·결제 안내</Link>}</nav>
      {!admin && <section className="management-card"><h2>새 신청</h2><form onSubmit={create}><fieldset disabled={busy} className="editor-properties-fieldset">
        <label className="field">서비스<select value={code} onChange={(e) => setCode(e.target.value as ServiceCode)}>{catalog?.items.map((item) => <option key={item.code} value={item.code}>{item.name} · {won(item.suggested_amount_krw)} 제안</option>)}</select></label>
        <p>{catalog?.items.find((item) => item.code === code)?.scope}</p><p className="field-hint">{catalog?.items.find((item) => item.code === code)?.price_note}</p>
        <label className="field">연결할 프로젝트{code === "file_review" ? " (필수)" : " (선택)"}<select required={code === "file_review"} value={projectId} onChange={(e) => setProjectId(e.target.value)}><option value="">프로젝트 선택</option>{projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="field">요청 내용<textarea required minLength={3} maxLength={4000} value={requestNote} onChange={(e) => setRequestNote(e.target.value)} placeholder="검토할 내용과 원하는 지원 범위를 적어주세요." /></label>
        <button className="button button-dark">서비스 신청 접수</button>
      </fieldset></form></section>}
      <section className="management-card"><h2>{admin ? "접수 목록" : "내 신청 목록"}</h2>{orders.length === 0 && <p>아직 접수한 신청이 없습니다.</p>}
        <div className="management-table-wrap"><table className="management-table"><thead><tr><th>서비스·접수일</th><th>업무·결제 상태</th><th>현재 견적</th><th>내용</th></tr></thead><tbody>{orders.map((item) => <tr key={item.id}><td>{item.catalog_snapshot.name}<small>{new Date(item.created_at).toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" })}</small></td><td>{serviceStatus[item.status]}<small>{servicePaymentLabel(item.payment_status)}{item.subscription_active ? " · 구독 이용 중" : ""}</small></td><td>{item.quotes[0] ? won(item.quotes[0].amount_inc_vat) + " (VAT 포함)" : "견적 대기"}</td><td><button className="button button-light button-sm" disabled={busy} onClick={() => choose(item)}>상세 보기</button></td></tr>)}</tbody></table></div>
        {cursor && <button className="button button-light" disabled={busy} onClick={() => void execute(() => load(cursor), "이전 신청을 불러왔습니다.", false)}>이전 신청 더 보기</button>}
      </section>
      {selected && <section className="management-card"><h2>{selected.catalog_snapshot.name}</h2><p style={{ whiteSpace: "pre-wrap" }}>{selected.request_note}</p><p>업무 상태: {serviceStatus[selected.status]} · 저장번호 {selected.revision}</p><p className="field-hint">결제 상태: {servicePaymentLabel(selected.payment_status)} · 이 결제로 지급한 크레딧 {selected.credits_granted.toLocaleString()}개</p>
        <ServiceCheckout key={selected.id} order={selected} admin={admin} busy={busy} onBusyChange={(value) => { actionLock.current = value; setBusy(value); }} onRefresh={load} />
        <h3>견적 이력</h3>
        {selected.quotes.length === 0 ? <p>아직 견적이 없습니다.</p> : selected.quotes.map((q) => <article className="management-card" key={q.id}>
          <h4>견적 {q.number} · {won(q.amount_inc_vat)} · VAT 포함</h4>
          <p style={{ whiteSpace: "pre-wrap" }}>{q.scope}</p><p>제외: {q.exclusions}</p>
          {q.checkout_terms?.automatic_renewal ? <p className="alert alert-info">첫 달 {won(q.checkout_terms.amount_inc_vat)}, 다음 달부터 매월 {q.checkout_terms.renewal_amount_inc_vat ? won(q.checkout_terms.renewal_amount_inc_vat) : "금액 확인 필요"}(VAT 포함) · Pro {q.checkout_terms.credits.toLocaleString()}크레딧·{q.checkout_terms.seats}명. 결제창을 열기 전에 자동 갱신 동의를 별도로 받습니다.</p> : q.checkout_terms ? <p className="field-hint">일회 결제 · 자동 갱신·크레딧 지급 없음</p> : <p className="field-hint">이전 견적 · 결제 조건을 포함한 새 견적 수락 전에는 수납하지 않습니다.</p>}
          <p>기한: {new Date(q.expires_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })}</p>
          <p className="field-hint">사유: {q.reason} · 정책 {q.policy_version}{q.id === selected.accepted_quote_id ? " · 수락한 견적" : ""}</p>
        </article>)}
        <fieldset disabled={busy} className="editor-properties-fieldset"><label className="field">처리 메모<textarea minLength={3} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} placeholder="진행·변경 사유를 3자 이상 적어주세요." /></label>
          {!admin && selected.status === "quoted" && currentQuote && <><label className="compact-check"><input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />VAT 포함 금액·제공/제외 범위를 확인했습니다. 수락만으로 결제·자동 갱신·구독 개통을 실행하지 않습니다.</label><button className="button button-dark" disabled={!understood || note.trim().length < 3} onClick={() => void orderAction("/service-orders", { action: "accept", quote_id: currentQuote.id, understands_no_payment: true }, "견적을 수락했습니다. 결제·자동 갱신 조건은 위 결제 영역에서 별도로 확인해 주세요.")}>현재 견적 수락</button></>}
          {!admin && canCancel && <button className="button button-light" disabled={note.trim().length < 3} onClick={() => void orderAction("/service-orders", { action: "cancel" }, "신청을 취소했습니다. 새 결제나 환불을 실행한 것은 아닙니다.")}>신청 취소</button>}
          {!admin && !canCancel && selected.payment_order && <p className="field-hint">연결된 결제의 확인·전액 환불을 먼저 완료해야 신청을 취소할 수 있습니다. 업무가 시작된 신청은 담당자에게 확인해 주세요.</p>}
          {admin && (["requested", "quoted"].includes(selected.status) || legacyAccepted) && <form onSubmit={(e) => { e.preventDefault(); void execute(() => api(`/admin/service-orders/${selected.id}/quotes`, { method: "POST", body: JSON.stringify({ base_revision: selected.revision, amount_inc_vat: amount, scope, exclusions, valid_days: days, reason: note }) }), "결제 조건을 포함한 새 견적 버전을 저장했습니다. 소유자의 새 수락이 필요합니다."); }}><h3>새 견적 발행</h3>{legacyAccepted && <p className="alert alert-info">기존 수락은 보존하며 결제 조건을 포함한 새 견적을 발행합니다. 이전 동의를 자동 갱신 동의로 사용하지 않습니다.</p>}<label className="field">최종 금액 (VAT 포함 원)<input required type="number" min={0} max={10000000} step={1} readOnly={selected.service_code === "pilot_pro_first_month"} value={amount} onChange={(e) => setAmount(Number(e.target.value))} /></label>{selected.service_code === "pilot_pro_first_month" && <p className="field-hint">첫 달 Pro는 VAT 포함99,000원입니다. 다음 월 금액·크레딧·좌석은 발행 시 서버 정책으로 고정하고, 소유자가 결제 직전 자동 갱신에 별도로 동의합니다.</p>}<label className="field">제공 범위<textarea required minLength={5} maxLength={4000} value={scope} onChange={(e) => setScope(e.target.value)} /></label><label className="field">제외 범위<textarea required minLength={3} maxLength={2000} value={exclusions} onChange={(e) => setExclusions(e.target.value)} /></label><label className="field">유효 기간 (일)<input required type="number" min={1} max={30} value={days} onChange={(e) => setDays(Number(e.target.value))} /></label><button className="button button-dark" disabled={note.trim().length < 3}>새 견적 버전 발행</button></form>}
          {admin && <><div className="button-row">{workOptions.map((status) => <button key={status} className="button button-light" disabled={note.trim().length < 3 || !paidForWork} onClick={() => void orderAction("/admin/service-orders", { action: "transition", status }, "업무 상태를 기록했습니다. 결제 내역은 변경하지 않았습니다.")}>{serviceStatus[status as keyof typeof serviceStatus]} 기록</button>)}</div>{!paidForWork && <p className="field-hint">결제 승인과 필요한 구독 상태를 확인한 뒤 업무를 시작할 수 있습니다.</p>}</>}
        </fieldset>
        <h3>진행 기록</h3><ol>{selected.events.map((e) => <li key={e.id}><strong>{serviceStatus[e.kind as keyof typeof serviceStatus] || e.kind}</strong> · {new Date(e.created_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })}<p style={{ whiteSpace: "pre-wrap" }}>{e.note}</p></li>)}</ol>
      </section>}
    </>}
  </ManagementPage>;
}
