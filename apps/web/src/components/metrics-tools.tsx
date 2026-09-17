"use client";
import { useRef, useState } from "react";
import { ManagementPage, Feedback, Loading } from "./management";
import { useApiData, dateTime } from "@/lib/business";
import { errorMessage } from "@/lib/api";
import { apiRequest, type ApiSchema } from "@/lib/api-contract";

type Cost = ApiSchema<"CostDTO">;
type Report = ApiSchema<"MetricsOverviewDTO">;
const categories:Record<string,string>={provider:"AI 제공자",storage:"저장",output:"출력",support:"지원",payment_fee:"PG 수수료"};
const bases:Record<string,string>={actual:"실측·확인",estimate:"추정",unknown:"불명",fixture:"검증 fixture",not_called:"호출하지 않음"};
const events:Record<string,string>={signup_completed:"가입 완료",trial_granted:"체험 지급",project_created:"프로젝트 생성",brief_completed:"필수 상품·브랜드 입력",generation_succeeded:"AI 결과 성공",generation_failed:"AI 실패",text_edited:"문구 수정 저장",all_faces_reviewed:"전체 면 검토",preflight_failed:"출력 검수 실패",production_export_succeeded:"정상 제작 출력 성공",credits_insufficient:"크레딧 부족",topup_paid:"충전 승인",subscription_paid:"구독 승인",renewal_paid:"갱신 승인",printer_accepted:"인쇄소 수락 신고",printer_rejected:"인쇄소 반려 신고"};
const paymentNames:Record<string,string>={subscription_payments:"실결제 구독 승인",first_subscription_customers:"첫 구독 실결제 조직",renewals:"실결제 갱신",topups:"실결제 충전",upgrades:"실결제 상향",refund_events:"환불 이벤트",mock_payments_excluded:"제외한 모의 결제",test_payments_excluded:"제외한 테스트 결제",unrecognized_providers_excluded:"제외한 미지원 제공자"};
const daysAgo=(days:number)=>new Date(Date.now()-days*86400000).toISOString().slice(0,10);
const minutes=(seconds:number|null)=>seconds===null?"미완료":`${(seconds/60).toFixed(1)}분`;
export function MetricsPage() {
  const [start,setStart]=useState(daysAgo(30)),[end,setEnd]=useState(daysAgo(-1));
  const [range,setRange]=useState(`?start=${daysAgo(30)}T00:00:00Z&end=${daysAgo(-1)}T00:00:00Z`);
  const report=useApiData<Report>(`/admin/metrics${range}`);
  const [offset,setOffset]=useState(0);const history=useApiData<{items:Cost[];next_offset:number|null}>(`/admin/metrics/costs?limit=25&offset=${offset}`);
  const [category,setCategory]=useState<Cost["category"]>("storage"),[basis,setBasis]=useState<Cost["basis"]>("unknown"),[currency,setCurrency]=useState<Cost["currency"]>("KRW");
  const [amount,setAmount]=useState(""),[supportMinutes,setSupportMinutes]=useState(""),[tenant,setTenant]=useState(""),[payment,setPayment]=useState(""),[reason,setReason]=useState("");
  const [correction,setCorrection]=useState<Cost>(),[occurred,setOccurred]=useState(daysAgo(0));
  const [busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState("");
  const pending=useRef<{body:string;key:string}|null>(null),saving=useRef(false);
  function correct(row:Cost) {
    setCorrection(row);setCategory(row.category);setBasis(row.basis);setCurrency(row.currency);setAmount(row.amount??"");setSupportMinutes(row.support_minutes===null?"":String(row.support_minutes));setTenant(row.tenant_id??"");setPayment(row.payment_id??"");setOccurred(row.occurred_at.slice(0,10));setReason("");
  }
  async function save() {
    if(saving.current)return;saving.current=true;setBusy(true);setError("");setNotice("");
    const payload:ApiSchema<"CostInput">={category,basis,currency,amount:basis==="unknown"?null:amount,tenant_id:tenant.trim()||null,payment_id:category==="payment_fee"?(payment.trim()||null):null,support_minutes:category==="support"&&supportMinutes!==""?Number(supportMinutes):null,reason:reason.trim(),supersedes_id:correction?.id??null,project_id:correction?.project_id??null,job_id:correction?.job_id??null,occurred_at:`${occurred}T00:00:00Z`};
    const body=JSON.stringify(payload);
    if(pending.current?.body!==body)pending.current={body,key:crypto.randomUUID()};
    try {await apiRequest("post","/v1/admin/metrics/costs",{headers:{"Idempotency-Key":pending.current.key},body:payload});pending.current=null;setCorrection(undefined);setReason("");setNotice("사유와 함께 비용 기록을 추가했습니다. 이전 기록은 보존됩니다.");report.refresh();history.refresh();}
    catch(e){setError(errorMessage(e));}finally{saving.current=false;setBusy(false);}
  }
  return <ManagementPage eyebrow="운영" title="성과와 원가" description="내부 서버 기록입니다. 실제 비용·추정치·미확인 비용을 구분하며 외부 광고·분석 서비스로 전송하지 않습니다.">
    <Feedback error={error||report.error||history.error} notice={notice}/>
    <section className="management-card"><h2>집계 기간 · UTC</h2><form onSubmit={e=>{e.preventDefault();setRange(`?start=${start}T00:00:00Z&end=${end}T00:00:00Z`);}}><label>시작일 포함<input type="date" value={start} onChange={e=>setStart(e.target.value)} required/></label><label>종료일 제외<input type="date" value={end} onChange={e=>setEnd(e.target.value)} required/></label><button className="button secondary">조회</button></form></section>
    {report.loading?<Loading/>:report.data&&<>
      <section className="management-card"><h2>집계 해석</h2>{report.data.limitations.map(text=><p key={text}>{text}</p>)}<p>고객 원문·프롬프트·이메일은 이 화면의 집계에 포함하지 않습니다. 기존 미수집 자료는 소급 추정하지 않습니다.</p></section>
      <section className="management-card"><h2>기능 이용 이벤트</h2><p>이벤트 수는 조직 수와 다릅니다. AI 제공자와 입고 회신 출처를 분리합니다. fixture·test는 검증 자료이며 실사용 성과가 아닙니다. 실결제 전환은 아래에서 별도 집계합니다.</p><div className="table-scroll"><table><thead><tr><th>이벤트</th><th>건수</th></tr></thead><tbody>{report.data.event_counts.map(row=><tr key={[row.name,row.provider,row.record_source].join(":")}><td>{events[row.name]||row.name}<small> {row.name}</small>{row.provider&&<p>제공자: {row.provider}</p>}{row.record_source&&<p>회신 출처: {row.record_source}</p>}</td><td>{row.count}</td></tr>)}</tbody></table></div></section>
      <section className="management-card"><h2>유입 → 체험 → 첫 구독 실결제</h2><p>조회 기간에 가입한 조직을 종료일 시점까지 추적합니다. 캠페인은 비식별 그룹 해시로 표시합니다.</p><div className="table-scroll"><table><thead><tr><th>채널·출처·매체</th><th>캠페인 해시</th><th>가입</th><th>체험</th><th>첫 결제</th><th>전환</th></tr></thead><tbody>{report.data.cohorts.map((row,i)=><tr key={i}><td>{row.channel} / {row.utm_source||"—"} / {row.utm_medium||"—"}</td><td title={row.campaign_hash||""}>{row.campaign_hash?.slice(0,12)||"—"}</td><td>{row.signups}</td><td>{row.trial_tenants}</td><td>{row.first_subscription_customers}</td><td>{row.conversion_rate===null?"—":`${(row.conversion_rate*100).toFixed(1)}%`}</td></tr>)}</tbody></table></div>{Object.entries(report.data.payments).map(([key,value])=><p key={key}>{paymentNames[key]||key}: {value}</p>)}</section>
      <section className="management-card"><h2>파일 준비 경과와 활동 추정 · 최신 100개 프로젝트</h2><p>경과 시간은 생성부터 최초 정상 제작 출력까지입니다. 활동 추정은 입력이 있던 활성 편집창의 제한된 구간 합계로 작업자의 노동시간과 같지 않습니다.</p><div className="table-scroll"><table><thead><tr><th>프로젝트</th><th>생성 → 제작 출력 경과</th><th>활동 추정</th></tr></thead><tbody>{report.data.timings.map(row=><tr key={row.project_id}><td>{row.project_id}</td><td>{minutes(row.elapsed_seconds)}</td><td>{row.activity_observed?minutes(row.estimated_active_edit_seconds):"관측 없음"}</td></tr>)}</tbody></table></div></section>
      <section className="management-card"><h2>구분별 원가</h2><p>현재 저장: {report.data.storage_observed_objects}개 / {(report.data.storage_observed_bytes/1048576).toFixed(2)} MiB · 기간 출력 요청 {report.data.output_jobs}건 · 기록 지원 시간 {report.data.reported_support_minutes}분 · 수수료 미확인 결제 {report.data.payment_fees_unknown}건</p><p>확인 금액 없는 항목: {report.data.missing_cost_categories.map(v=>categories[v]).join(", ")||"없음"}</p><table><thead><tr><th>항목</th><th>근거</th><th>금액</th><th>기록 수</th></tr></thead><tbody>{report.data.totals.map(row=><tr key={[row.category,row.currency,row.basis].join(":")}><td>{categories[row.category]||row.category}</td><td>{bases[row.basis]||row.basis}</td><td>{row.amount===null?"불명":`${row.amount} ${row.currency}`}</td><td>{row.entries}</td></tr>)}</tbody></table></section>
      <section className="management-card"><h2>AI 제공자 요청별 원가 · {report.data.provider_attempt_count}건 중 최신 100건</h2><p>실패·재시도 요청도 포함합니다. 사용자 크레딧 환불이 제공자 비용을 없애지는 않습니다.</p><div className="table-scroll"><table><thead><tr><th>시각·요청</th><th>모델·상태</th><th>근거</th><th>USD</th></tr></thead><tbody>{report.data.provider_attempts.map(row=><tr key={row.attempt_id}><td>{dateTime(row.created_at)}<br/>{row.attempt_id}</td><td>{row.model}<br/>요청 {row.requested_quality||"미기록"} / 응답 {row.actual_quality||"불명"}<br/>{row.output_size||"크기 미기록"} · {row.status}</td><td>{bases[row.basis]}</td><td>{row.cost_usd??"불명"}</td></tr>)}</tbody></table></div></section>
    </>}
    <section className="management-card"><h2>{correction?"원가 정정 기록":"원가·지원 시간 기록"}</h2><p>확인된 청구·운영 자료의 금액 또는 추정 기준을 사유에 적습니다. 불명 금액은 0원으로 바꾸지 않습니다. 고객 문구·개인정보는 사유에 입력하지 마세요.</p>{correction&&<p>정정 대상 {correction.id} <button disabled={busy} onClick={()=>setCorrection(undefined)}>정정 취소</button></p>}<form onSubmit={e=>{e.preventDefault();void save();}}>
      <label>항목<select value={category} disabled={busy||!!correction} onChange={e=>setCategory(e.target.value as Cost["category"])}>{["storage","output","support","payment_fee"].map(value=><option key={value} value={value}>{categories[value]}</option>)}</select></label>
      <label>근거<select value={basis} disabled={busy} onChange={e=>setBasis(e.target.value as Cost["basis"])}>{["actual","estimate","unknown"].map(value=><option key={value} value={value}>{bases[value]}</option>)}</select></label>
      <label>통화<select value={currency} disabled={busy||!!correction} onChange={e=>setCurrency(e.target.value as Cost["currency"])}><option>KRW</option><option>USD</option></select></label>
      {basis!=="unknown"&&<label>금액<input type="number" min="0" max="1000000000" step="0.000001" required value={amount} disabled={busy} onChange={e=>setAmount(e.target.value)}/></label>}
      <label>조직 ID · 전체 운영비는 비움<input value={tenant} disabled={busy||!!correction} onChange={e=>setTenant(e.target.value)}/></label>
      {category==="payment_fee"&&<label>결제 ID · 개별 수수료 연결<input value={payment} disabled={busy||!!correction} onChange={e=>setPayment(e.target.value)}/></label>}
      {category==="support"&&<label>지원 시간 · 분<input type="number" min="0" max="100000" value={supportMinutes} disabled={busy} onChange={e=>setSupportMinutes(e.target.value)}/></label>}
      <label>발생일 · UTC<input type="date" required value={occurred} disabled={busy} onChange={e=>setOccurred(e.target.value)}/></label><label>자료·측정 근거 및 정정 사유<textarea value={reason} minLength={5} maxLength={1000} required disabled={busy} onChange={e=>setReason(e.target.value)}/></label><button className="button primary" disabled={busy||reason.trim().length<5}>기록 추가</button>
    </form></section>
    <section className="management-card"><h2>원가 기록 이력 · 정정 이전 원문 포함</h2>{history.data?.items.map(row=><div key={row.id} className="management-row"><p>{dateTime(row.occurred_at)} · {categories[row.category]} · {bases[row.basis]} · {row.amount===null?"불명":`${row.amount} ${row.currency}`}<br/>{row.reason}<br/><small>{row.id}{row.supersedes_id?` · 정정 원문 ${row.supersedes_id}`:""}</small></p><button className="button secondary" disabled={busy} onClick={()=>correct(row)}>사유와 함께 정정</button></div>)}<button disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-25))}>이전</button><button disabled={history.data?.next_offset==null} onClick={()=>setOffset(history.data?.next_offset??offset)}>다음</button></section>
  </ManagementPage>;
}
