"use client";
import { useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { Feedback } from "./management";
type ICC = { id: string; sha256: string; description: string; byte_size: number };
export function PrintEngineAdmin({ onRegistered }: { onRegistered?: (message: string) => void }) {
  const [file, setFile] = useState<File>(), [icc, setIcc] = useState<ICC>();
  const [source, setSource] = useState(""), [license, setLicense] = useState("");
  const [name, setName] = useState(""), [manufacturer, setManufacturer] = useState(""), [material, setMaterial] = useState("");
  const [bleed, setBleed] = useState("3"), [ppi, setPpi] = useState("300"), [ink, setInk] = useState("300"), [layout, setLayout] = useState("face_pages");
  const [finishing, setFinishing] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const inFlight = useRef(false);
  async function upload() {
    if (inFlight.current || !file) return;
    if (file.size > 4 * 1024 * 1024) { setError("ICC 파일은 4MiB 이하여야 합니다."); return; }
    inFlight.current = true; setBusy(true); setError("");
    try { const body = new FormData(); body.append("file", file); body.append("source", source); body.append("license", license);
      setIcc(await api<ICC>("/admin/print-engine/icc", { method: "POST", body })); setNotice("ICC 바이트·해시·CMYK 변환 확인 완료. 출력 조건을 등록해 주세요.");
    } catch (e) { setError(errorMessage(e)); } finally { inFlight.current = false; setBusy(false); }
  }
  async function register() {
    if (!icc || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try { const result = await api<{ id: string }>("/admin/print-engine/profiles", { method: "POST", body: JSON.stringify({ name, manufacturer, material, source, license, review_available: true,
      requirements: { icc_id: icc.id, icc_sha256: icc.sha256, bleed_mm: Number(bleed), min_ppi: Number(ppi), max_ink_percent: Number(ink), layout,
        ...(finishing ? { finishing_delivery: "separate_process_pdf_v1" } : {}) } }) });
      const message = `시험 가능한 미승인 출력 조건을 등록했습니다. 기존 제조 등록에서 별도 승인 증빙을 연결해 주세요. (${result.id})`;
      setNotice(message); onRegistered?.(message);
    } catch (e) { setError(errorMessage(e)); } finally { inFlight.current = false; setBusy(false); }
  }
  return <section className="panel stack"><h3>ICC · CMYK 출력 조건 등록</h3>
    <p>제조사에서 사용을 허락한 CMYK 출력 ICC를 등록합니다. 등록과 시험은 제조 승인이나 PDF/X 인증을 대신하지 않습니다. 업로드 원본은 비공개로 보관되며 프로필을 PDF에 포함할 사용권이 필요합니다.</p>
    <label>ICC 파일 (최대 4MiB)<input type="file" accept=".icc,.icm" disabled={busy} onChange={e => { setFile(e.target.files?.[0]); setIcc(undefined); }} /></label>
    <label>출처<input value={source} onChange={e => setSource(e.target.value)} maxLength={2000} /></label>
    <label>보관·PDF 임베드 사용권<input value={license} onChange={e => setLicense(e.target.value)} maxLength={2000} /></label>
    <button className="button" onClick={upload} disabled={busy || !file || !source.trim() || !license.trim()}>ICC 검사하고 보관</button>
    {icc && <><p>{icc.description} · {(icc.byte_size / 1024).toFixed(1)}KiB · SHA256 {icc.sha256.slice(0, 16)}…</p>
      <label>조건 이름<input value={name} onChange={e => setName(e.target.value)} maxLength={160} /></label>
      <label>제조사<input value={manufacturer} onChange={e => setManufacturer(e.target.value)} maxLength={160} /></label>
      <label>확정 재질<input value={material} onChange={e => setMaterial(e.target.value)} maxLength={120} /></label>
      <label>출력 배치<select value={layout} onChange={e => setLayout(e.target.value)}><option value="face_pages">삼방 파우치 면별</option><option value="net">등록 구조 전개도</option></select></label>
      <label>도련 mm<input type="number" min={0} max={10} step={0.001} value={bleed} onChange={e => setBleed(e.target.value)} /></label>
      <label>최소 원본 해상도 ppi<input type="number" min={72} max={2400} value={ppi} onChange={e => setPpi(e.target.value)} /></label>
      <label>총잉크량 상한 %<input type="number" min={100} max={400} value={ink} onChange={e => setInk(e.target.value)} /></label>
      <label className="compact-check"><input type="checkbox" checked={finishing} onChange={event => setFinishing(event.target.checked)} /> 걸이 구멍·노치의 CUT와 지퍼·개봉 안내 파일을 분리해서 납품</label>
      <p className="muted">가공 치수까지 승인된 도면과 연결해야 합니다. 가공 안내는 인쇄 그림에 넣지 않습니다. 일반 PDF · CMYK · 글꼴 윤곽선 · CUT/FOLD 별도 PDF이며 PDF/X·별색·화이트·오버프린트·반투명은 지원하지 않습니다.</p>
      <button className="button" onClick={register} disabled={busy || !name.trim() || !manufacturer.trim() || !material.trim()}>미승인 조건 등록 · 시험에 공개</button></>}
    <Feedback error={error} notice={notice} />
  </section>;
}
