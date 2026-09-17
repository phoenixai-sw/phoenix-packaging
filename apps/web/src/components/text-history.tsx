"use client";
import { useEffect, useState } from "react";
import { History, LoaderCircle, RefreshCw } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { textRevisionHistory, type TextRevision } from "@editor/text-history";
import { Feedback } from "./management";

export function TextHistory({ projectId, saveCurrent, readOnly }: {
  projectId: string; saveCurrent: () => Promise<number>; readOnly: boolean;
}) {
  const [revisions, setRevisions] = useState<TextRevision[]>([]);
  const [face, setFace] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    async function load() {
      try {
        if (!readOnly) await saveCurrent();
        const data = await api<{ items: TextRevision[] }>(`/projects/${projectId}/revisions`);
        if (active) setRevisions(data.items);
      } catch (cause) { if (active) setError(errorMessage(cause)); }
      finally { if (active) setLoading(false); }
    }
    void load();
    return () => { active = false; };
    // Capture the editor save operation when this history panel opens or refreshes.
  }, [projectId, readOnly, refresh]);
  const faces = new Map(revisions.flatMap((revision) => revision.scene.faces.map((f) => [f.id, f.name] as const)));
  const history = textRevisionHistory(revisions).map((entry) => ({ ...entry,
    changes: entry.changes.filter((change) => face === "all" || change.face_id === face),
  })).filter((entry) => entry.changes.length);
  return <div className="text-history-panel">
    <p className="panel-hint">서버에 저장된 최근 100개 버전을 비교합니다. 자동저장 전 키 입력은 별도 기록되지 않으며, 과거 저장 기록이 없는 구간은 복원하지 않습니다.</p>
    <div className="management-section-heading">
      <label className="field">확인할 면<select value={face} onChange={(event) => setFace(event.target.value)}>
        <option value="all">모든 면</option>{[...faces].map(([id, name]) => <option key={id} value={id}>{name}</option>)}
      </select></label>
      <button className="button button-light button-sm" disabled={loading} onClick={() => setRefresh((value) => value + 1)}><RefreshCw size={15} /> {readOnly ? "새로고침" : "저장하고 새로고침"}</button>
    </div>
    <Feedback error={error} />
    {loading ? <div className="loading-state"><LoaderCircle className="spin" /> 저장 기록을 확인하고 있어요.</div> : <>
      {revisions.length > 0 && <p className="field-hint">비교 기준: 저장 {Math.min(...revisions.map((revision) => revision.number))}번부터 {Math.max(...revisions.map((revision) => revision.number))}번까지</p>}
      {!history.length ? <div className="management-empty"><History size={26} /><p>이 면에서 비교할 텍스트 변경 기록이 아직 없습니다.</p></div> :
        history.map(({ revision, previous_number, has_gap, changes }) => <section className="history-revision" key={revision.id}>
          <header><strong>저장 {previous_number} → {revision.number}</strong><time dateTime={revision.created_at}>{new Date(revision.created_at).toLocaleString("ko-KR")}</time></header>
          {has_gap && <p className="field-hint">두 기록 사이의 중간 저장본이 없어 이 구간의 최종 차이만 표시합니다.</p>}
          {changes.map((change) => <article className="history-change" key={change.key}>
            <div><span className="pill">{change.face_name}</span> <strong>{{ added: "텍스트 추가", removed: "텍스트 삭제", changed: "텍스트 변경" }[change.kind]}</strong><small>요소 {change.object_id}</small></div>
            <div className="history-change-grid"><div><span>이전</span><p>{change.before ?? "없음"}</p></div><div><span>이후</span><p>{change.after ?? "없음"}</p></div></div>
            {!!change.properties.length && <p className="field-hint">함께 바뀐 속성: {change.properties.join(" · ")}</p>}
          </article>)}
        </section>)}
    </>}
  </div>;
}
