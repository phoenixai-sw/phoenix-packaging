"use client";
import { useEffect, useRef, useState } from "react";
import type { SceneObject } from "@editor/model";
import { loadFontAsset } from "@editor/fonts";
import { apiRequest, type ApiSchema } from "@/lib/api-contract";
import { errorMessage } from "@/lib/api";

export function FontPicker({ object, brandId, readOnly, onChange }: { object: SceneObject; brandId?: string | null; readOnly: boolean; onChange: (patch: Partial<SceneObject>) => void }) {
  const [fonts, setFonts] = useState<ApiSchema<"FontData">[]>([]);
  const [allowed, setAllowed] = useState<string[]>([]);
  const [catalogState, setCatalogState] = useState<"loading" | "ready" | "error">("loading");
  const [catalogError, setCatalogError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const current = useRef({ object, readOnly }); current.current = { object, readOnly };
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let alive = true;
    setCatalogState("loading"); setCatalogError("");
    Promise.all([apiRequest("get", "/v1/fonts", {}), apiRequest("get", "/v1/brands", {})]).then(([catalog, brands]) => {
      if (alive) { setFonts(catalog.items); setAllowed(brands.items.find(brand => brand.id === brandId)?.font_asset_ids ?? []); setCatalogState("ready"); }
    }).catch(error => { if (alive) { setCatalogError(errorMessage(error)); setCatalogState("error"); } });
    return () => { alive = false; };
  }, [brandId]);
  useEffect(() => {
    let alive = true;
    if (!object.font_asset_id) { setError(""); return; }
    const identity = object.font_asset_id;
    const timer = setTimeout(() => { void apiRequest("post", "/v1/fonts/{identity}/glyphs", { path: { identity }, body: { text: object.text ?? "" } }).then(report => {
      if (alive) setError(report.supported ? "" : `이 글꼴에서 확인이 필요한 문자: ${[...report.missing_codepoints, ...report.unsupported_shaping].slice(0, 8).join(", ")}. 출력 전 문구 또는 글꼴을 확인하세요.`);
    }).catch(error => { if (alive) setError(errorMessage(error)); }); }, 450);
    return () => { alive = false; clearTimeout(timer); };
  }, [object.font_asset_id, object.text]);
  async function select(id: string) {
    if (readOnly || busy || catalogState !== "ready") return;
    if (!id) { onChange({ font_asset_id: undefined, font_id: "NotoSansKR", font_weight: 400 }); return; }
    const before = object.id; setBusy(true); setError("");
    try {
      const font = await loadFontAsset(id);
      if (!mounted.current || current.current.readOnly || current.current.object.id !== before) return;
      onChange({ font_asset_id: id, font_id: "NotoSansKR", font_weight: font.weight });
    } catch (error) { setError(errorMessage(error)); } finally { setBusy(false); }
  }
  return <label className="field property-field">글꼴
    <select disabled={readOnly || busy || catalogState !== "ready"} value={object.font_asset_id ?? ""} onChange={e => void select(e.target.value)}>
      <option value="">Noto Sans KR · 기본</option>
      {object.font_asset_id && !fonts.some(font => font.id === object.font_asset_id) && <option value={object.font_asset_id}>{catalogState === "loading" ? "등록 글꼴 확인 중…" : catalogState === "error" ? "등록 글꼴 조회 실패 · 저장본 유지" : "저장된 등록 글꼴 · 확인 필요"}</option>}
      {fonts.filter(font => allowed.includes(font.id) || font.id === object.font_asset_id).map(font => <option key={font.id} value={font.id}>{font.family} · {font.weight}{allowed.includes(font.id) ? "" : " (저장본 유지)"}</option>)}
    </select>
    <small>{catalogState === "loading" ? "브랜드 글꼴 목록을 확인하고 있습니다." : busy ? "글꼴 원본 확인 중…" : brandId ? "브랜드 보관함에서 허용한 글꼴입니다. 기존 저장본의 글꼴은 계속 유지됩니다." : "브랜드를 연결하고 브랜드 보관함에서 글꼴을 허용하세요."} <a href="/app/brands" target="_blank" rel="noreferrer">브랜드 글꼴 관리</a></small>
    {catalogError && <small role="alert">{catalogError} 저장된 글꼴은 변경하지 않았습니다. 속성을 다시 열어 조회해 주세요.</small>}
    {error && <small role="alert">{error}</small>}
  </label>;
}
