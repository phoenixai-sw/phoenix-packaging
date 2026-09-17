"use client";
import { useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { apiRequest, type ApiSchema } from "@/lib/api-contract";
import { Feedback } from "./management";

type Catalog = ApiSchema<"FontList">;
export function FontManagement({ catalog, readOnly, onChanged }: { catalog?: Catalog | null; readOnly: boolean; onChanged: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [holder, setHolder] = useState("");
  const [license, setLicense] = useState("");
  const [source, setSource] = useState("");
  const [licenseText, setLicenseText] = useState("");
  const [web, setWeb] = useState(false);
  const [print, setPrint] = useState(false);
  const [zip, setZip] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function upload() {
    if (busy || readOnly || !catalog) return;
    setError(""); setNotice("");
    if (!file) { setError("정적 TTF 파일을 선택해 주세요."); return; }
    if (file.size > catalog.max_bytes) { setError(`파일은 ${(catalog.max_bytes / 1024 / 1024).toFixed(0)}MiB 이하로 선택해 주세요.`); return; }
    const declaration = { license_name: license, license_text: licenseText, source_url: source, rights_holder: holder, web_use_confirmed: web, print_use_confirmed: print, redistribution_allowed: zip };
    setBusy(true);
    try {
      if (catalog.direct_upload) {
        const ticket = await apiRequest("post", "/v1/fonts/uploads", { body: { ...declaration, name: file.name, byte_size: file.size } });
        const response = await fetch(ticket.upload_url, { method: "PUT", headers: ticket.headers, body: file, credentials: "omit" });
        if (!response.ok) throw new Error("글꼴 업로드가 완료되지 않았습니다. 다시 시도해 주세요.");
        await apiRequest("post", "/v1/fonts/uploads/{identity}/complete", { path: { identity: ticket.id } });
      } else {
        const body = new FormData(); body.append("file", file); body.append("declaration", JSON.stringify(declaration));
        await api("/fonts", { method: "POST", body });
      }
      setNotice("원본과 권리 기록을 보관했습니다. 브랜드 편집에서 사용할 글꼴을 선택하세요.");
      setFile(null); onChanged();
    } catch (error) { setError(errorMessage(error)); } finally { setBusy(false); }
  }
  return <section className="management-card" style={{ marginTop: 24 }}>
    <h2>팀 글꼴 보관함</h2>
    <p>브랜드에서 허용한 글꼴을 편집기·3D·PDF에 같은 원본으로 적용합니다. 등록은 사용 권리 진술이며 라이선스 인증을 대신하지 않습니다.</p>
    {catalog?.items.length ? <ul>{catalog.items.map(font => <li key={font.id}><strong>{font.family} · {font.weight}</strong> — {font.license_name} · {font.redistribution_allowed ? "편집 ZIP 포함 권한 확인" : "편집 ZIP 포함 불가"}</li>)}</ul> : <p className="field-hint">기본 Noto Sans KR 400/700은 항상 사용할 수 있습니다.</p>}
    {!readOnly && <details><summary>사용 권한이 있는 글꼴 등록</summary><fieldset disabled={busy} style={{ border: 0, padding: "16px 0" }}>
      <label className="field">정적 TrueType 글꼴 (.ttf)<input type="file" accept=".ttf,font/ttf" onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
      <p className="field-hint">가변·CFF·컬러 글꼴은 지원하지 않습니다. 등록된 원본과 라이선스는 과거 디자인 보존을 위해 수정·삭제하지 않습니다.</p>
      <div className="form-grid"><label className="field">권리자<input value={holder} maxLength={200} onChange={e => setHolder(e.target.value)} /></label><label className="field">라이선스 이름<input value={license} maxLength={200} placeholder="예: SIL OFL 1.1" onChange={e => setLicense(e.target.value)} /></label></div>
      <label className="field">공식 출처 / 라이선스 주소<input type="url" value={source} placeholder="https://…" onChange={e => setSource(e.target.value)} /></label>
      <label className="field">사용 허락 / 라이선스 원문<textarea rows={5} value={licenseText} maxLength={30000} onChange={e => setLicenseText(e.target.value)} /></label>
      <label className="checkbox-label"><input type="checkbox" checked={web} onChange={e => setWeb(e.target.checked)} /> 웹 제공과 디자인 편집에 사용할 권한을 확인했습니다.</label>
      <label className="checkbox-label"><input type="checkbox" checked={print} onChange={e => setPrint(e.target.checked)} /> PDF 포함·인쇄·윤곽선 출력에 사용할 권한을 확인했습니다.</label>
      <label className="checkbox-label"><input type="checkbox" checked={zip} onChange={e => setZip(e.target.checked)} /> 원본 글꼴과 라이선스를 편집 ZIP으로 전달할 권한도 확인했습니다. (선택)</label>
      <p className="field-hint">마지막 항목을 확인하지 않은 글꼴이 포함된 디자인은 PDF 검토가 가능하지만 편집 ZIP을 만들 수 없습니다.</p>
      <button type="button" className="button button-dark" disabled={!catalog || !file || !web || !print || !holder || !license || licenseText.trim().length < 20 || !source} onClick={() => void upload()}>{busy ? "글꼴 검사·보관 중…" : "검사 후 등록"}</button>
    </fieldset></details>}
    <Feedback error={error} notice={notice} />
  </section>;
}
