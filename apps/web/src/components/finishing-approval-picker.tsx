"use client";
import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { useApiData } from "@/lib/business";

export type FinishingApproval = {
  schema_version: "1.0";
  geometry_hash: string;
  pouch_features: Record<string, number | boolean | string> | null;
  holes: Array<{ center_x_mm: number; center_y_mm: number; diameter_mm: number }>;
};
export type FinishingDraft = {
  project_id: string;
  base_revision: number;
  geometry_template_id: string;
  approved_dimensions: Record<string, number>;
  approved_finishing: FinishingApproval;
};

export function FinishingApprovalPicker({ value, onPick, onClear }: {
  value?: FinishingDraft;
  onPick: (value: FinishingDraft) => void;
  onClear: () => void;
}) {
  const projects = useApiData<{ items: Array<{ id: string; name: string; base_revision: number }> }>("/projects");
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function load() {
    const project = projects.data?.items.find(item => item.id === selected);
    if (!project || busy) return;
    setBusy(true); setError("");
    try {
      const result = await api<FinishingDraft>("/admin/template-finishing/preview", {
        method: "POST", body: JSON.stringify({ project_id: project.id, base_revision: project.base_revision }),
      });
      if (mounted.current) onPick(result);
    } catch (cause) { if (mounted.current) setError(errorMessage(cause)); }
    finally { if (mounted.current) setBusy(false); }
  }
  return <fieldset className="registry-fieldset">
    <legend>걸이 구멍·개봉부·지퍼·노치 승인 범위</legend>
    <p className="field-hint">편집기에서 저장한 가공 치수를 가져옵니다. 제조사 도면과 대조하고 해당 재질·가공을 포함한 증빙으로 별도 승인해야 제작에 사용할 수 있습니다.</p>
    <label className="field">가공 치수를 가져올 프로젝트
      <select value={selected} onChange={event => { setSelected(event.target.value); onClear(); }} disabled={busy}>
        <option value="">프로젝트 선택</option>
        {projects.data?.items.map(project => <option key={project.id} value={project.id}>{project.name} · 저장본 {project.base_revision}</option>)}
      </select>
    </label>
    <button type="button" className="button button-light" disabled={busy || !selected} onClick={() => void load()}>{busy ? "가공 치수 확인 중…" : "저장한 가공 치수 가져오기"}</button>
    {value && <div className="stack" role="status">
      <p>저장본 {value.base_revision}의 치수와 가공을 고정했습니다. 치수나 구조를 바꾸면 다시 가져오세요.</p>
      {value.approved_finishing.holes.map((hole, index) => <p key={index}>걸이 구멍 {index + 1}: 앞면 기준 X{hole.center_x_mm} / Y{hole.center_y_mm}mm · 지름 {hole.diameter_mm}mm</p>)}
      {value.approved_finishing.pouch_features && <p>개봉부 높이 {String(value.approved_finishing.pouch_features.header_height_mm)}mm · 지퍼 {value.approved_finishing.pouch_features.zipper_enabled ? `${value.approved_finishing.pouch_features.zipper_y_mm}mm 위치` : "없음"} · 노치 {value.approved_finishing.pouch_features.tear_enabled ? `${value.approved_finishing.pouch_features.tear_y_mm}mm 위치` : "없음"}</p>}
      <button type="button" className="button button-light button-sm" onClick={onClear}>가공 승인 범위 제외</button>
    </div>}
    {(error || projects.error) && <p className="alert alert-error" role="alert">{error || projects.error}</p>}
  </fieldset>;
}
