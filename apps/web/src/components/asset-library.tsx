"use client";
import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { Feedback, Loading } from "./management";
export type LibraryAsset = { id: string; name: string; width_px: number; height_px: number; byte_size: number; source: string; created_at: string };
export function AssetLibrary({ projectId, readOnly = false, onSelect }: {
  projectId?: string; readOnly?: boolean; onSelect?: (asset: LibraryAsset) => void;
}) {
  const [query, setQuery] = useState(""), [source, setSource] = useState("");
  const [offset, setOffset] = useState(0), [nextOffset, setNextOffset] = useState<number | null>(null);
  const [items, setItems] = useState<LibraryAsset[]>([]), [busy, setBusy] = useState(true);
  const [error, setError] = useState(""), [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setBusy(true); setError("");
    const timer = setTimeout(() => {
      const params = new URLSearchParams({ q: query, offset: String(offset), limit: "24" });
      if (source) params.set("source", source);
      if (projectId) params.set("project_id", projectId);
      void api<{ items: LibraryAsset[]; next_offset: number | null }>(`/assets?${params}`).then(data => {
        if (active) { setItems(data.items); setNextOffset(data.next_offset); }
      }).catch(e => { if (active) setError(errorMessage(e)); }).finally(() => { if (active) setBusy(false); });
    }, 200);
    return () => { active = false; clearTimeout(timer); };
  }, [projectId, query, source, offset, retry]);
  return <div className="asset-library">
    <p className="field-hint">접근 가능한 작업 공간의 원본과 AI 결과를 재사용합니다. 배치는 새 이미지 객체를 추가하며 원본 파일을 변경하지 않습니다.</p>
    <div className="form-row">
      <label className="field">파일명 검색<input value={query} maxLength={160} onChange={e => { setQuery(e.target.value); setOffset(0); }} placeholder="로고, 제품 사진, AI 시안" /></label>
      <label className="field">출처<select value={source} onChange={e => { setSource(e.target.value); setOffset(0); }}>
        <option value="">전체 이미지</option><option value="upload">업로드</option><option value="openai">AI 생성·수정</option><option value="image_quality">해상도·도련 보완</option><option value="fixture">예시 이미지</option>
      </select></label>
    </div>
    <Feedback error={error} />
    {error && <button className="button button-light" onClick={() => setRetry(v => v + 1)}>다시 불러오기</button>}
    {busy ? <Loading /> : !items.length ? <p className="empty-state">조건에 맞는 이미지가 없습니다. 편집기에서 이미지를 업로드해 보세요.</p> : <div className="asset-library-grid">
      {items.map(asset => <article className="asset-library-card" key={asset.id}>
        <img src={`/api/v1/assets/${asset.id}/content`} alt={asset.name} loading="lazy" />
        <strong title={asset.name}>{asset.name}</strong>
        <small>{asset.width_px} × {asset.height_px} px · {(asset.byte_size / 1024 / 1024).toFixed(2)} MiB</small>
        <small>{new Date(asset.created_at).toLocaleDateString("ko-KR")}</small>
        {onSelect ? <button className="button button-light" disabled={readOnly} onClick={() => onSelect(asset)}>현재 면에 배치</button>
          : <a className="button button-light" href={`/api/v1/assets/${asset.id}/content`} target="_blank" rel="noreferrer">원본 보기</a>}
      </article>)}
    </div>}
    <div className="form-row" style={{ marginTop: 18 }}>
      <button className="button button-light" disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 24))}>이전 이미지</button>
      <span aria-live="polite">{Math.floor(offset / 24) + 1} 페이지</span>
      <button className="button button-light" disabled={busy || nextOffset === null} onClick={() => setOffset(nextOffset!)}>다음 이미지</button>
    </div>
  </div>;
}
