"use client";
import { useEffect, useRef, useState } from "react";
import { Eye, RefreshCw } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { useApiData } from "@/lib/business";
import {
  structureInitialInputs,
  structureInputError,
  structureIssueLocation,
  structurePreviewCurrent,
  structureSelectionKey,
  type RegisteredStructure,
  type StructureInputs,
  type StructurePreview,
} from "@/lib/registered-structures";
import type { Project, Scene } from "@editor/model";
import { Feedback, Loading } from "./management";
import { RegisteredStructurePreview } from "./registered-structure-preview";
import styles from "./registered-structures.module.css";

type CheckedPreview = StructurePreview & {
  selectionKey: string;
  sceneKey: string;
  baseRevision: number;
};
export function RegisteredStructureTools({
  project,
  scene,
  saveCurrent,
  onServerProject,
  onFaceSelect,
  onApplyingChange,
  readOnly,
}: {
  project: Project;
  scene: Scene;
  saveCurrent: () => Promise<number>;
  onServerProject: (project: Project) => void;
  onFaceSelect: (faceId: string, objectId?: string) => void;
  onApplyingChange: (applying: boolean) => void;
  readOnly: boolean;
}) {
  const catalogue = useApiData<{ items: RegisteredStructure[] }>("/structures");
  const [selected, setSelected] = useState("");
  const [inputs, setInputs] = useState<StructureInputs>({
    width_mm: project.width_mm,
    height_mm: project.height_mm,
  });
  const [preview, setPreview] = useState<CheckedPreview>();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const inFlight = useRef(false),
    mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const items =
    catalogue.data?.items.filter(
      (item) => item.family === project.template_id,
    ) || [];
  const item = items.find((entry) => entry.id === selected);
  const sceneKey = JSON.stringify(scene),
    selectionKey = structureSelectionKey(selected, inputs);
  const latest = useRef({ sceneKey, selectionKey, readOnly });
  latest.current = { sceneKey, selectionKey, readOnly };
  const current = structurePreviewCurrent(
    preview,
    selectionKey,
    sceneKey,
    project.base_revision,
  )
    ? preview
    : undefined;
  const inputError = item ? structureInputError(item.definition, inputs) : null;
  function choose(id: string) {
    setSelected(id);
    setPreview(undefined);
    setError("");
    setNotice("");
    const entry = items.find((value) => value.id === id);
    if (entry) setInputs(structureInitialInputs(entry.definition, project));
  }
  function assertCurrent(key: string, sceneSnapshot: string) {
    if (!mounted.current || latest.current.readOnly)
      throw new Error("편집 권한을 다시 확인해 주세요.");
    if (
      latest.current.selectionKey !== key ||
      latest.current.sceneKey !== sceneSnapshot
    )
      throw new Error(
        "선택이나 디자인이 변경되었습니다. 다시 미리보기해 주세요.",
      );
  }
  async function inspect() {
    if (!item || inputError || readOnly || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    setPreview(undefined);
    try {
      const base_revision = await saveCurrent();
      assertCurrent(selectionKey, sceneKey);
      const result = await api<StructurePreview>("/structures/preview", {
        method: "POST",
        body: JSON.stringify({
          template_version_id: selected,
          inputs,
          project_id: project.id,
          base_revision,
        }),
      });
      assertCurrent(selectionKey, sceneKey);
      setPreview({
        ...result,
        selectionKey,
        sceneKey,
        baseRevision: base_revision,
      });
    } catch (cause) {
      if (mounted.current) setError(errorMessage(cause));
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function apply() {
    if (
      !current ||
      !current.can_apply ||
      !current.layout_checked ||
      readOnly ||
      inFlight.current
    )
      return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const base_revision = await saveCurrent();
      assertCurrent(current.selectionKey, current.sceneKey);
      if (
        !structurePreviewCurrent(
          current,
          latest.current.selectionKey,
          latest.current.sceneKey,
          base_revision,
        )
      )
        throw new Error(
          "저장본이 변경되었습니다. 현재 저장본으로 다시 미리보기해 주세요.",
        );
      onApplyingChange(true);
      const result = await api<Project>(`/projects/${project.id}/structure`, {
        method: "PATCH",
        body: JSON.stringify({
          base_revision,
          template_version_id: selected,
          inputs,
        }),
      });
      onServerProject(result);
      if (!mounted.current) return;
      setPreview(undefined);
      setNotice(
        "등록 구조를 새 저장본에 적용했습니다. 기존 객체의 위치와 크기는 유지했습니다. 인쇄 프로필과 모든 면·표시사항 확인은 해제되므로 다시 확인해 주세요.",
      );
    } catch (cause) {
      if (mounted.current) setError(errorMessage(cause));
    } finally {
      onApplyingChange(false);
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <section className="management-card">
      <div className="management-section-heading">
        <h3>등록 구조 검토</h3>
        <button
          className="icon-button"
          disabled={busy}
          onClick={catalogue.refresh}
          aria-label="등록 구조 새로고침"
        >
          <RefreshCw size={16} />
        </button>
      </div>
      <p>
        현재 포장 종류에 공개된 구조를 먼저 확인한 뒤 적용합니다. 제조 승인과
        별개인 검토용 구조이며 제작용 출력에는 사용할 수 없습니다.
      </p>
      <Feedback error={catalogue.error || error} notice={notice} />
      {readOnly && (
        <p className="field-hint">
          읽기 모드입니다. 구조 변경은 편집 권한을 확보한 창에서 진행하세요.
        </p>
      )}
      {catalogue.loading ? (
        <Loading />
      ) : !items.length ? (
        <p className="field-hint">
          이 포장 종류에 공개된 등록 구조가 없습니다. 운영자가 검증한 구조를
          검토용으로 공개하면 여기에 표시됩니다.
        </p>
      ) : (
        <>
          <label className="field">
            검토할 구조
            <select
              value={selected}
              disabled={busy || readOnly}
              onChange={(event) => choose(event.target.value)}
            >
              <option value="">구조 선택</option>
              {items.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.name}
                  {entry.is_demo ? " · 시험 예시" : ""} · 검토 전용
                </option>
              ))}
            </select>
          </label>
          {item && (
            <>
              <p className="field-hint">
                {item.manufacturer || "등록 출처 미확정"} ·{" "}
                {item.definition.recipe_id === "fixed-panel-net-v1"
                  ? "고정 치수: 등록된 값 그대로 사용합니다."
                  : "허용된 범위 안에서 폭과 높이를 지정합니다."}
              </p>
              <div className="form-two-columns">
                {(["width_mm", "height_mm", "bottom_mm", "depth_mm"] as const)
                  .filter((key) => inputs[key] !== undefined)
                  .map((key) => (
                    <label className="field" key={key}>
                      {
                        {
                          width_mm: "폭",
                          height_mm: "높이",
                          bottom_mm: "펼친 바닥 폭",
                          depth_mm: "깊이",
                        }[key]
                      }{" "}
                      (mm)
                      <input
                        type="number"
                        step={0.1}
                        value={inputs[key]}
                        disabled={
                          busy ||
                          readOnly ||
                          item.definition.recipe_id === "fixed-panel-net-v1"
                        }
                        onChange={(event) => {
                          setInputs({
                            ...inputs,
                            [key]: Number(event.target.value),
                          });
                          setPreview(undefined);
                        }}
                      />
                    </label>
                  ))}
              </div>
              {inputError && <Feedback error={inputError} />}
              <p className="field-hint">
                기존 객체의 mm 위치와 크기를 자동으로 바꾸지 않습니다.
                면·치수·안전영역과 충돌하면 적용을 차단합니다. 이 구조에서는
                추가 구멍·지퍼·뜯는 가공을 지원하지 않습니다.
              </p>
              <button
                className="button button-light"
                disabled={busy || readOnly || !!inputError}
                onClick={() => void inspect()}
              >
                <Eye size={16} /> 저장하고 구조 미리보기
              </button>
            </>
          )}
          {current && (
            <>
              <RegisteredStructurePreview geometry={current.geometry} />
              <div className={styles.comparison}>
                <table className="management-table">
                  <thead>
                    <tr>
                      <th>면</th>
                      <th>현재 → 적용 치수 (mm)</th>
                      <th>새 안전영역 (mm)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {current.geometry.faces.map((face) => {
                      const old = scene.faces.find(
                        (entry) => entry.id === face.id,
                      );
                      return (
                        <tr key={face.id}>
                          <td>{face.name}</td>
                          <td>
                            {old
                              ? `${old.width_mm} × ${old.height_mm}`
                              : "없음"}{" "}
                            → {face.width_mm} × {face.height_mm}
                          </td>
                          <td>
                            {face.regions.safe.width_mm} ×{" "}
                            {face.regions.safe.height_mm}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {current.layout_issues.map((issue, index) => {
                const location = structureIssueLocation(issue, scene.faces);
                return (
                  <div
                    className="alert alert-error"
                    key={`${issue.code}-${index}`}
                  >
                    {issue.message}
                    {location && (
                      <button
                        className="text-link"
                        onClick={() =>
                          onFaceSelect(location.faceId, location.objectId)
                        }
                      >
                        해당 면·객체 확인
                      </button>
                    )}
                  </div>
                );
              })}
              <p className="field-hint">
                {current.can_apply
                  ? "현재 저장본의 배치 조건을 확인했습니다."
                  : "충돌을 수정한 뒤 다시 미리보기해 주세요."}{" "}
                적용하면 새 저장본이 생기고 인쇄 프로필·모든 면 확인·표시사항
                확인을 해제합니다.
              </p>
              <button
                className="button button-dark"
                disabled={
                  busy ||
                  readOnly ||
                  !current.can_apply ||
                  !current.layout_checked
                }
                onClick={() => void apply()}
              >
                이 구조를 현재 프로젝트에 적용
              </button>
            </>
          )}
          {preview && !current && (
            <p className="field-hint">
              선택 또는 저장본이 변경되었습니다. 구조를 다시 미리보기해 주세요.
            </p>
          )}
        </>
      )}
    </section>
  );
}
