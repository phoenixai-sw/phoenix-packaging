"use client";
import { useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import {
  parseStructureJson,
  fixedStructureExample,
  separatedStructureExample,
  type StructureDefinition,
  type StructureGeometry,
} from "@/lib/registered-structures";
import { Feedback } from "./management";
import { RegisteredStructurePreview } from "./registered-structure-preview";
import type { FinishingApproval } from "./finishing-approval-picker";
import styles from "./registered-structures.module.css";

type RegistryStructure = {
  id: string;
  name: string;
  manufacturer?: string;
  status: string;
  is_demo: boolean;
  review_available?: boolean;
  structure_definition?: unknown;
};
export function RegisteredStructureAdmin({
  items,
  onRegistered,
  onAction,
}: {
  items: RegistryStructure[];
  onRegistered: (message: string) => void;
  onAction: (action: "review" | "approve" | "revoke", id: string) => void;
}) {
  const [definition, setDefinition] = useState("");
  const [dimensions, setDimensions] = useState<Record<string, number>>({ width_mm: 160, height_mm: 230 });
  const [checked, setChecked] = useState<{
    input: string;
    normalized_definition: StructureDefinition;
    geometry: StructureGeometry;
    approved_dimensions: Record<string, number>;
    approved_finishing?: FinishingApproval | null;
  }>();
  const [name, setName] = useState(""),
    [manufacturer, setManufacturer] = useState(""),
    [source, setSource] = useState(""),
    [license, setLicense] = useState(""),
    [material, setMaterial] = useState("");
  const [familyKey, setFamilyKey] = useState("");
  const [demo, setDemo] = useState(true),
    [publicReview, setPublicReview] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const inFlight = useRef(false);
  const valid = checked?.input === definition ? checked : undefined;
  async function validate() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setChecked(undefined);
    setError("");
    setNotice("");
    try {
      const structure_definition = parseStructureJson(definition);
      const result = await api<{
        normalized_definition: StructureDefinition;
        geometry: StructureGeometry;
        approved_dimensions: Record<string, number>;
        approved_finishing?: FinishingApproval | null;
      }>("/admin/structures/validate", {
        method: "POST",
        body: JSON.stringify({ structure_definition, inputs: dimensions }),
      });
      setChecked({ ...result, input: definition });
      setNotice(
        "구조 형식과 기하 조건을 검증했습니다. 제조사 승인이나 실제 조립 검증을 뜻하지 않습니다.",
      );
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  async function register(event: React.FormEvent) {
    event.preventDefault();
    if (!valid || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api("/admin/template-versions", {
        method: "POST",
        body: JSON.stringify({
          name,
          manufacturer,
          source,
          license,
          material,
          is_demo: demo,
          geometry_template_id: valid.normalized_definition.family,
          billing_family_key: familyKey,
          structure_definition: valid.normalized_definition,
          approved_dimensions: valid.approved_dimensions,
          ...(valid.approved_finishing ? { approved_finishing: valid.approved_finishing } : {}),
          review_available: publicReview,
        }),
      });
      setChecked(undefined);
      const message = publicReview
        ? "검토용 구조 버전을 등록하고 공개했습니다. 제작 승인 상태는 변경하지 않았습니다."
        : "구조 버전을 비공개 초안으로 등록했습니다. 공개용 변경은 새 버전으로 등록해 주세요.";
      setNotice(message);
      onRegistered(message);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  function example(value: StructureDefinition) {
    setDefinition(JSON.stringify(value, null, 2));
    setChecked(undefined);
    setDemo(true);
    setPublicReview(false);
    setDimensions(value.recipe_id === "fixed-panel-net-v1" ? Object.fromEntries(Object.entries(value.dimensions).filter((entry): entry is [string, number] => typeof entry[1] === "number")) : { width_mm: 160, height_mm: 230 });
    setError("");
    setNotice("자체 시험 예시입니다. 제조사 승인 자료로 사용하지 마세요.");
  }
  return (
    <section className="management-card">
      <h2>등록 구조 · 검토 공개</h2>
      <p>
        제조 승인과 별개로 고정 패널 또는 범위형 삼방 실링 구조를 등록합니다.
        정의 JSON은 관리자 입력용이며 사용자에게는 치수와 미리보기로 표시됩니다.
      </p>
      <Feedback error={error} notice={notice} />
      <div className="management-table-wrap">
        <table className="management-table">
          <thead>
            <tr>
              <th>구조·출처</th>
              <th>공개 상태</th>
              <th>용도</th>
              <th>제조 검토</th>
            </tr>
          </thead>
          <tbody>
            {items
              .filter((item) => item.structure_definition)
              .map((item) => (
                <tr key={item.id}>
                  <td>
                    <strong>{item.name}</strong>
                    <small>{item.manufacturer}</small>
                  </td>
                  <td>
                    {item.status === "revoked"
                      ? "철회됨"
                      : item.review_available
                        ? "검토 공개"
                        : "비공개"}
                  </td>
                  <td>
                    {item.is_demo ? "시험 예시 · 제작 불가" : item.status === "approved" ? "승인 조건·가공·최신 검수 통과 시 제작 가능" : "검토 전용 · 제작 불가"}
                  </td>
                  <td>{item.status === "approved" ? <button className="button button-light button-sm" onClick={() => onAction("revoke", item.id)}>승인 철회</button>
                    : item.status === "draft" ? <button className="button button-light button-sm" disabled={item.is_demo} onClick={() => onAction("review", item.id)}>검토 요청</button>
                    : <button className="button button-light button-sm" disabled={item.is_demo || item.status !== "review"} onClick={() => onAction("approve", item.id)}>증빙 확인 후 승인</button>}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      <details className={styles.details}>
        <summary>새 검토 구조 검증·등록</summary>
        <div>
          <div className={styles.actions}>
            <button
              className="button button-light button-sm"
              disabled={busy}
              onClick={() => example(separatedStructureExample)}
            >
              범위형 삼방 실링 시험 예시
            </button>
            <button
              className="button button-light button-sm"
              disabled={busy}
              onClick={() => example(fixedStructureExample)}
            >
              고정 패널 시험 예시
            </button>
          </div>
          <label className="field">
            구조 정의 JSON
            <textarea
              className={styles.definition}
              spellCheck={false}
              value={definition}
              disabled={busy}
              onChange={(event) => {
                setDefinition(event.target.value);
                setChecked(undefined);
              }}
              placeholder="제조 도면을 검토해 작성한 구조 정의를 입력하세요."
            />
          </label>
          <fieldset className="registry-fieldset" disabled={busy}>
            <legend>검토·승인 대상 완성 치수</legend>
            <p className="field-hint">범위형 구조도 제조 승인 시에는 실제 사용할 규격을 고정합니다. 고정 패널의 치수는 등록 정의와 같아야 합니다.</p>
            <div className="form-two-columns">{[["width_mm", "폭"], ["height_mm", "높이"], ["bottom_mm", "펼친 바닥 폭 (스탠드형만)"], ["depth_mm", "깊이 (상자만)"]].map(([key, label]) => <label className="field" key={key}>{label} mm<input type="number" min={1} step="0.1" value={dimensions[key] ?? ""} onChange={event => {
              const next = { ...dimensions };
              if (event.target.value === "") delete next[key]; else next[key] = Number(event.target.value);
              setDimensions(next); setChecked(undefined);
            }} /></label>)}</div>
          </fieldset>
          <button
            type="button"
            className="button button-light"
            disabled={busy || !definition.trim()}
            onClick={() => void validate()}
          >
            구조 정의 서버 검증
          </button>
          {valid && <RegisteredStructurePreview geometry={valid.geometry} />}
          <form onSubmit={register}>
            <fieldset disabled={busy} className="editor-properties-fieldset">
              <div className="form-two-columns">
                <label className="field">
                  버전 이름
                  <input
                    required
                    maxLength={160}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                  />
                </label>
                <label className="field">
                  제조사·등록 주체
                  <input
                    required
                    maxLength={160}
                    value={manufacturer}
                    onChange={(event) => setManufacturer(event.target.value)}
                  />
                </label>
                <label className="field">
                  구조 그룹 식별자
                  <input
                    required
                    maxLength={100}
                    value={familyKey}
                    onChange={(event) => setFamilyKey(event.target.value)}
                    placeholder="예: supplier-pouch-series-a"
                  />
                </label>
                <label className="field">
                  소재
                  <input
                    maxLength={120}
                    value={material}
                    onChange={(event) => setMaterial(event.target.value)}
                  />
                </label>
              </div>
              <label className="field">
                도면 출처
                <textarea
                  required
                  maxLength={2000}
                  value={source}
                  onChange={(event) => setSource(event.target.value)}
                  placeholder="시험 예시는 자체 시험 자료임을 명시하세요."
                />
              </label>
              <label className="field">
                사용권 근거
                <textarea
                  required
                  maxLength={2000}
                  value={license}
                  onChange={(event) => setLicense(event.target.value)}
                />
              </label>
              <label className="compact-check">
                <input
                  type="checkbox"
                  checked={demo}
                  onChange={(event) => setDemo(event.target.checked)}
                />{" "}
                시험 예시 구조
              </label>
              <label className="compact-check">
                <input
                  type="checkbox"
                  checked={publicReview}
                  onChange={(event) => setPublicReview(event.target.checked)}
                />{" "}
                사용자가 검토용으로 선택하도록 공개
              </label>
              <p className="field-hint">
                공개는 제조 승인이 아닙니다. 제작에는 정확한 규격·재질·가공을 포함한 도면 증빙과 인쇄 조건 승인이 필요합니다.
                지원하는 파우치 가공은 구조 정의에 고정하며 편집기에서 임의로 추가하지 않습니다. 고정 패널은 등록 치수를 바꿀 수 없습니다.
              </p>
              <button className="button button-dark" disabled={busy || !valid}>
                {publicReview
                  ? "검증한 구조 등록·검토 공개"
                  : "검증한 구조 초안 등록"}
              </button>
            </fieldset>
          </form>
        </div>
      </details>
    </section>
  );
}
