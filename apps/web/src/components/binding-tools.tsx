"use client";
import { useState } from "react";
import { Copy, LoaderCircle, Link2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useApiData, type ProductData } from "@/lib/business";
import { api, errorMessage } from "@/lib/api";
import { Feedback } from "./management";
import type { Project } from "@editor/model";
type Preview = {
  base_revision: number;
  changes: Array<{
    object_id: string;
    field: string;
    before: string;
    after: string;
  }>;
};
export function BindingTools({
  project,
  saveCurrent,
  onServerProject,
  readOnly,
  lockedIds = [],
}: {
  project: Project;
  saveCurrent: () => Promise<number>;
  onServerProject: (project: Project) => void;
  readOnly: boolean;
  lockedIds?: string[];
}) {
  const router = useRouter();
  const products = useApiData<{ items: ProductData[] }>("/products");
  const [variant, setVariant] = useState(project.product_variant_id || "");
  const [preview, setPreview] = useState<Preview>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [copyName, setCopyName] = useState(`${project.name} 복사`);
  async function getPreview() {
    setBusy(true);
    setError("");
    try {
      await saveCurrent();
      setPreview(
        await api<Preview>(`/projects/${project.id}/bindings/preview`, {
          method: "POST",
          body: JSON.stringify({ product_variant_id: variant }),
        }),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function apply() {
    if (!preview || readOnly) return;
    if (
      preview.changes.some((change) => lockedIds.includes(change.object_id))
    ) {
      setError(
        "변경할 문구에 잠긴 레이어가 있습니다. 먼저 잠금을 해제해 주세요.",
      );
      return;
    }
    setBusy(true);
    try {
      const result = await api<Project>(
        `/projects/${project.id}/bindings/apply`,
        {
          method: "POST",
          body: JSON.stringify({
            product_variant_id: variant,
            base_revision: preview.base_revision,
          }),
        },
      );
      onServerProject(result);
      setPreview(undefined);
      setNotice("연결된 문구에 확인한 변경을 반영했습니다.");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function duplicate() {
    setBusy(true);
    setError("");
    try {
      await saveCurrent();
      const result = await api<Project>(`/projects/${project.id}/duplicate`, {
        method: "POST",
        body: JSON.stringify({ name: copyName }),
      });
      router.push(`/app/projects/${result.id}/editor`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      <Feedback error={error || products.error} notice={notice} />
      <section className="management-card">
        <h3>
          <Link2 size={18} /> 상품 정보 연결
        </h3>
        <p className="field-hint">
          브랜드 보관함에 저장한 확정 정보를 연결합니다. 수동으로 연결을 해제한
          문구는 유지됩니다.
        </p>
        <label className="field">
          연결할 상품 변형
          <select
            value={variant}
            onChange={(e) => {
              setVariant(e.target.value);
              setPreview(undefined);
            }}
          >
            <option value="">선택하세요</option>
            {products.data?.items.flatMap((p) =>
              p.variants.map((v) => (
                <option key={v.id} value={v.id}>
                  {p.name} · {v.name}
                </option>
              )),
            )}
          </select>
        </label>
        <button
          className="button button-light"
          disabled={busy || !variant || readOnly}
          onClick={() => void getPreview()}
        >
          바뀔 문구 미리 확인
        </button>
        {preview && (
          <>
            <div className="management-table-wrap">
              <table className="management-table">
                <thead>
                  <tr>
                    <th>연결 항목</th>
                    <th>변경 전</th>
                    <th>변경 후</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.changes.map((c, i) => (
                    <tr key={`${c.object_id}-${i}`}>
                      <td>
                        {c.field === "text"
                          ? "연결 문구"
                          : c.field === "barcode_value"
                            ? "바코드"
                            : c.field}
                      </td>
                      <td>{c.before}</td>
                      <td>{c.after}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!preview.changes.length && (
              <p className="field-hint">바뀔 문구가 없습니다.</p>
            )}
            <button
              className="button button-dark"
              disabled={busy || readOnly}
              onClick={() => void apply()}
            >
              위 변경을 확인하고 적용
            </button>
          </>
        )}
      </section>
      <section className="management-card">
        <h3>
          <Copy size={18} /> 프로젝트 복제
        </h3>
        <p className="field-hint">
          디자인을 복제해 맛과 용량 등 다른 상품을 준비하세요. 원본 프로젝트는
          유지됩니다.
        </p>
        <label className="field">
          새 프로젝트 이름
          <input
            value={copyName}
            onChange={(e) => setCopyName(e.target.value)}
            maxLength={160}
          />
        </label>
        <button
          className="button button-dark"
          disabled={busy || !copyName.trim() || readOnly}
          onClick={() => void duplicate()}
        >
          {busy ? (
            <LoaderCircle className="spin" size={16} />
          ) : (
            <Copy size={16} />
          )}{" "}
          복제해서 편집
        </button>
      </section>
    </div>
  );
}
