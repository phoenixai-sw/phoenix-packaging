"use client";
import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { api, errorMessage } from "@/lib/api";
import { compareScenes } from "@editor/revisions";
import type { Project, Scene } from "@editor/model";
const Canvas = dynamic(() => import("@editor/canvas"), { ssr: false });
type Revision = {
  id: string;
  number: number;
  scene?: Scene;
  reason: string;
  created_at: string;
};
type Page = {
  items: Revision[];
  next_before_number: number | null;
  has_more: boolean;
  total: number;
  current_revision: number;
};
export function RevisionTools({
  project,
  readOnly,
  saveCurrent,
  onBeforeRestore,
  onRestored,
}: {
  project: Project;
  readOnly: boolean;
  saveCurrent: () => Promise<number>;
  onBeforeRestore: (scene: Scene) => void;
  onRestored: (project: Project) => void;
}) {
  const [items, setItems] = useState<Revision[]>([]),
    [next, setNext] = useState<number | null>(null),
    [total, setTotal] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [scenes, setScenes] = useState<Record<string, Scene>>({}),
    [loadingScene, setLoadingScene] = useState(false);
  const [before, setBefore] = useState(""),
    [after, setAfter] = useState("current"),
    [face, setFace] = useState(project.scene.active_face_id),
    [confirmed, setConfirmed] = useState(false);
  async function load(cursor?: number) {
    setBusy(true);
    setError("");
    try {
      const page = await api<Page>(
        `/projects/${project.id}/revisions?limit=30&include_scene=false${cursor ? `&before_number=${cursor}` : ""}`,
      );
      setItems((previous) =>
        cursor
          ? [
              ...previous,
              ...page.items.filter((r) => !previous.some((p) => p.id === r.id)),
            ]
          : page.items,
      );
      setNext(page.next_before_number);
      setTotal(page.total);
      if (!cursor)
        setBefore(
          page.items.find((r) => r.number < project.base_revision)?.id ||
            page.items[0]?.id ||
            "",
        );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, [project.id]);
  useEffect(() => {
    const ids = [before, after].filter(
      (id) => id && id !== "current" && !scenes[id],
    );
    if (!ids.length) {
      setLoadingScene(false);
      return;
    }
    let active = true;
    setLoadingScene(true);
    Promise.all(
      ids.map((id) => api<Revision>(`/projects/${project.id}/revisions/${id}`)),
    )
      .then((values) => {
        if (active)
          setScenes((previous) => ({
            ...previous,
            ...Object.fromEntries(
              values.filter((r) => r.scene).map((r) => [r.id, r.scene!]),
            ),
          }));
      })
      .catch((e) => {
        if (active) setError(errorMessage(e));
      })
      .finally(() => {
        if (active) setLoadingScene(false);
      });
    return () => {
      active = false;
    };
  }, [before, after, project.id, scenes]);
  const aMeta = items.find((r) => r.id === before),
    bMeta = items.find((r) => r.id === after);
  const a =
      aMeta && scenes[aMeta.id]
        ? { ...aMeta, scene: scenes[aMeta.id] }
        : undefined,
    b =
      after === "current"
        ? { scene: project.scene, number: project.base_revision }
        : bMeta && scenes[bMeta.id]
          ? { ...bMeta, scene: scenes[bMeta.id] }
          : undefined;
  const changes = a && b ? compareScenes(a.scene, b.scene) : [];
  async function restore() {
    if (!a || readOnly || !confirmed) return;
    setBusy(true);
    setError("");
    try {
      onBeforeRestore(a.scene);
      const base_revision = await saveCurrent();
      const restored = await api<Project>(
        `/projects/${project.id}/revisions/${a.id}/restore`,
        { method: "POST", body: JSON.stringify({ base_revision }) },
      );
      onRestored(restored);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="revision-tools">
      <p className="panel-hint">
        자동 저장을 포함한 전체 {total}개 저장본을 탐색합니다. 복원은 새
        저장본을 만들며 제조 검수 확인은 다시 해야 합니다. 아직 저장되지 않은
        편집은 비교에 포함되지 않습니다.
      </p>
      {error && (
        <div className="alert alert-error" role="alert">
          {error}
        </div>
      )}
      <div className="property-row">
        <label className="field">
          이전 / 복원 대상
          <select
            value={before}
            disabled={busy}
            onChange={(e) => {
              setBefore(e.target.value);
              setConfirmed(false);
            }}
          >
            {items.map((r) => (
              <option key={r.id} value={r.id}>
                저장본 {r.number} ·{" "}
                {new Date(r.created_at).toLocaleString("ko-KR")}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          비교 대상
          <select value={after} onChange={(e) => setAfter(e.target.value)}>
            <option value="current">
              현재 서버 저장본 {project.base_revision}
            </option>
            {items.map((r) => (
              <option key={r.id} value={r.id}>
                저장본 {r.number}
              </option>
            ))}
          </select>
        </label>
      </div>
      {next !== null && (
        <button
          className="button button-light button-sm"
          disabled={busy}
          onClick={() => void load(next)}
        >
          이전 저장본 더 불러오기 ({items.length}/{total})
        </button>
      )}
      <label className="field">
        비교할 면
        <select value={face} onChange={(e) => setFace(e.target.value)}>
          {(b?.scene.faces || project.scene.faces).map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      </label>
      {loadingScene && <p role="status">선택한 저장본을 불러오고 있습니다…</p>}
      <div className="revision-previews">
        {[a, b].map((r, i) => {
          const f = r?.scene.faces.find((f) => f.id === face);
          return (
            <div key={i}>
              <strong>
                {i === 0 ? "이전" : "이후"} · 저장본 {r?.number ?? "—"}
              </strong>
              <div className="revision-canvas">
                {f && (
                  <Canvas
                    face={f}
                    selected={null}
                    onSelect={() => {}}
                    onChange={() => {}}
                    zoom={1}
                    guides={false}
                    onEditState={() => {}}
                    holes={r?.scene.holes}
                    readOnly
                    rulers={false}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>
      <h3>전체 면 변경 {changes.length}건</h3>
      <div className="revision-diff">
        {changes.map((change, i) => (
          <div key={i}>
            <strong>
              {project.scene.faces.find((f) => f.id === change.face_id)?.name ||
                change.face_id}{" "}
              · {change.label}
            </strong>
            {change.object_id && (
              <small>레이어 {change.object_id.slice(0, 8)}</small>
            )}
            <del>{change.before}</del>
            <ins>{change.after}</ins>
          </div>
        ))}
        {!changes.length && <p>두 저장본의 디자인 내용이 같습니다.</p>}
      </div>
      <label className="guide-toggle">
        <input
          type="checkbox"
          checked={confirmed}
          disabled={readOnly || busy}
          onChange={(e) => setConfirmed(e.target.checked)}
        />{" "}
        비교 결과를 확인했으며 이전 저장본으로 복원합니다.
      </label>
      <button
        className="button button-dark"
        disabled={readOnly || busy || loadingScene || !confirmed || !a}
        onClick={() => void restore()}
      >
        저장본 {a?.number} 복원
      </button>
      <p className="panel-hint">
        잠긴 레이어가 변경되는 복원은 먼저 잠금을 해제해야 합니다. 원래 저장본은
        보존됩니다.
      </p>
    </div>
  );
}
