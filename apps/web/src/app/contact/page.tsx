"use client";
import { useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { SiteHeader, SiteFooter } from "@/components/brand";
import { api, errorMessage } from "@/lib/api";
import { Feedback } from "@/components/management";

const packageTypes = [
  ["three_side_seal", "3면 실링 봉투"],
  ["stand_up_pouch", "스탠드 파우치"],
  ["folding_box", "접이식 박스"],
  ["other", "기타·미정"],
] as const;

export default function ContactPage() {
  const [form, setForm] = useState({ company: "", name: "", email: "", phone: "", package_type: "three_side_seal", monthly_changes: "3", next_order_date: "", has_dieline: "no", message: "", consent: false });
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [done, setDone] = useState<string>();
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((prev) => ({ ...prev, [key]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value }));
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try {
      const receipt = await api<{ id: string; received_at: string }>("/inquiries", {
        method: "POST",
        body: JSON.stringify({
          company: form.company.trim(), name: form.name.trim(), email: form.email.trim(), phone: form.phone.trim() || null,
          package_type: form.package_type, monthly_changes: Number(form.monthly_changes), next_order_date: form.next_order_date || null,
          has_dieline: form.has_dieline === "yes", message: form.message.trim() || null, source: "contact-page", consent: true,
        }),
      });
      setDone(receipt.id);
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }
  return (
    <>
      <SiteHeader />
      <main className="section-wrap contact-page">
        <div className="pricing-intro">
          <div className="eyebrow">TALK TO US</div>
          <h1>유료 시험·별도 협의 문의</h1>
          <p>포장 형태와 다음 발주 일정을 알려주시면 지원 가능한 범위와 요금을 안내드립니다.</p>
        </div>
        {done ? (
          <div className="alert alert-info" role="status">
            문의를 접수했습니다 (접수 번호 {done.slice(0, 8)}). 영업일 기준 1~2일 안에 입력하신 이메일로 답변드립니다.
          </div>
        ) : (
          <form className="management-card stack contact-form" onSubmit={submit}>
            <div className="region-fields">
              <label className="field">회사·브랜드<input required maxLength={120} value={form.company} onChange={set("company")} /></label>
              <label className="field">담당자<input required maxLength={80} value={form.name} onChange={set("name")} /></label>
              <label className="field">이메일<input required type="email" value={form.email} onChange={set("email")} /></label>
              <label className="field">연락처 (선택)<input maxLength={40} value={form.phone} onChange={set("phone")} /></label>
            </div>
            <div className="region-fields">
              <label className="field">포장 형태
                <select value={form.package_type} onChange={set("package_type")}>{packageTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
              </label>
              <label className="field">월 문구·디자인 변경 수<input type="number" min={0} max={1000} value={form.monthly_changes} onChange={set("monthly_changes")} /></label>
              <label className="field">다음 발주 예정일<input type="date" value={form.next_order_date} onChange={set("next_order_date")} /></label>
              <label className="field">보유 칼선(도면)
                <select value={form.has_dieline} onChange={set("has_dieline")}><option value="no">없음</option><option value="yes">있음 (인쇄소 도면 보유)</option></select>
              </label>
            </div>
            <label className="field">요청 내용 (선택)<textarea rows={4} maxLength={4000} value={form.message} onChange={set("message")} placeholder="상품, 규격, 현재 외주 방식, 궁금한 점" /></label>
            <label className="checkbox-label"><input type="checkbox" checked={form.consent} onChange={set("consent")} required /> 문의 답변을 위해 입력한 연락처를 사용하는 데 동의합니다.</label>
            <Feedback error={error} />
            <button className="button button-dark" disabled={busy || !form.consent}>{busy ? "보내는 중…" : "문의 보내기"} <ArrowUpRight size={17} /></button>
          </form>
        )}
      </main>
      <SiteFooter />
    </>
  );
}
