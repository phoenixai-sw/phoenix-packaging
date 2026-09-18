"use client";
import { useState } from "react";
import { useApiData } from "@/lib/business";
import { api, errorMessage } from "@/lib/api";
import type { ApiSchema } from "@/lib/api-contract";
import { Feedback } from "@/components/management";

type Inquiry = ApiSchema<"InquiryData">;
const packageLabel: Record<string, string> = { three_side_seal: "3면 실링", stand_up_pouch: "스탠드 파우치", folding_box: "접이식 박스", other: "기타" };
const statusLabel: Record<string, string> = { new: "신규", contacted: "연락함", closed: "종료" };

export default function InquiriesAdminPage() {
  const { data, error, refresh } = useApiData<{ items: Inquiry[] }>("/admin/inquiries");
  const [busy, setBusy] = useState(""), [saveError, setSaveError] = useState("");
  async function update(item: Inquiry, status: Inquiry["status"], note: string | null) {
    setBusy(item.id); setSaveError("");
    try { await api(`/admin/inquiries/${item.id}`, { method: "PATCH", body: JSON.stringify({ status, note }) }); refresh(); }
    catch (e) { setSaveError(errorMessage(e)); }
    finally { setBusy(""); }
  }
  return (
    <section className="management-card">
      <h2>문의 관리</h2>
      <p className="field-hint">홈페이지·요금 페이지 문의입니다. 유효 문의(자체 상품·지원 포장·30일 내 제작 계획)를 구분해 기록하세요.</p>
      <Feedback error={error || saveError} />
      <div className="management-table-wrap">
        <table className="management-table">
          <thead><tr><th>접수</th><th>회사·담당</th><th>연락처</th><th>포장·월 변경·발주일·칼선</th><th>내용</th><th>상태</th><th>메모</th></tr></thead>
          <tbody>
            {(data?.items || []).map((item) => (
              <tr key={item.id}>
                <td>{new Date(item.received_at).toLocaleString("ko-KR")}</td>
                <td>{item.company}<br /><small>{item.name}</small></td>
                <td>{item.email}<br /><small>{item.phone || "-"}</small></td>
                <td>{packageLabel[item.package_type] || item.package_type} · 월 {item.monthly_changes}회<br /><small>{item.next_order_date || "발주일 미정"} · 칼선 {item.has_dieline ? "있음" : "없음"}</small></td>
                <td style={{ maxWidth: 280, whiteSpace: "pre-wrap" }}>{item.message || "-"}</td>
                <td>
                  <select value={item.status} disabled={busy === item.id} onChange={(e) => void update(item, e.target.value as Inquiry["status"], item.note)}>
                    {Object.entries(statusLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </td>
                <td>
                  <input defaultValue={item.note || ""} maxLength={1000} placeholder="통화·판단 메모" disabled={busy === item.id}
                    onBlur={(e) => { if (e.target.value !== (item.note || "")) void update(item, item.status, e.target.value || null); }} />
                </td>
              </tr>
            ))}
            {data && !data.items.length && <tr><td colSpan={7}>접수된 문의가 없습니다.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
