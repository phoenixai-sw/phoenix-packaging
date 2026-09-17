"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Check, Download, LoaderCircle, ShieldCheck } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import {
  canRetryExport,
  exportKindLabel,
  exportStatusLabel,
  editableExportBody,
  isExportInProgress,
} from "@/lib/export-state";
import { useApiData } from "@/lib/business";
import { Feedback } from "./management";
import type { Project, Scene } from "@editor/model";
type Version = {
  id: string;
  name: string;
  status?: string;
  geometry_template_id?: string;
  material?: string;
  requirements?: { required_fields?: string[] };
};
type Preflight = {
  status: string;
  review_allowed: boolean;
  production_allowed: boolean;
  issues: Array<{
    code: string;
    severity: string;
    scope: string;
    message: string;
    face_id?: string;
    object_id?: string;
    field?: string;
  }>;
};
type Quote = {
  id: string;
  credit_total: number;
  balance_before: number;
  balance_after: number;
  expires_at: string;
};
type Job = {
  id: string;
  kind?: string;
  status: string;
  download_url?: string;
  error?: { message?: string } | string;
  result?: {
    format?: string;
    revision_number?: number;
    asset_count?: number;
    font_count?: number;
    byte_size?: number;
    credits_charged?: number;
    rights_notice?: string;
    review_only?: boolean;
  };
};
export function ExportTools({
  project,
  scene,
  saveCurrent,
  onServerProject,
  onCommit,
  onFaceSelect,
  onOpenStructures,
  readOnly,
}: {
  project: Project;
  scene: Scene;
  saveCurrent: () => Promise<number>;
  onServerProject: (p: Project) => void;
  onCommit: (s: Scene, confirmationOnly?: boolean) => void;
  onFaceSelect: (id: string, objectId?: string) => void;
  onOpenStructures: () => void;
  readOnly: boolean;
}) {
  const templates = useApiData<{ items: Version[] }>("/templates");
  const profiles = useApiData<{ items: Version[] }>("/print-profiles");
  const [template, setTemplate] = useState(
    project.template_version_id ||
      (String(scene.template_version_id || "").includes("demo")
        ? ""
        : String(scene.template_version_id || "")),
  );
  const [profile, setProfile] = useState(
    project.print_profile_version_id || "",
  );
  const [material, setMaterial] = useState(project.material || "");
  const [kind, setKind] = useState<"review" | "production">("review");
  const [preflight, setPreflight] = useState<Preflight>();
  const [quote, setQuote] = useState<Quote>();
  const [job, setJob] = useState<Job>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [checkedRevision, setCheckedRevision] = useState(0);
  const key = useRef("");
  const editableKey = useRef<{ revision: number; key: string } | undefined>(
      undefined,
    ),
    editablePending = useRef(false),
    readOnlyRef = useRef(readOnly);
  readOnlyRef.current = readOnly;
  const jobPanel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (job?.id) jobPanel.current?.scrollIntoView({ block: "nearest" });
  }, [job?.id]);
  const reviewed = scene.reviewed_face_ids || [];
  const registeredStructure = !!scene.structure_ref;
  const requiredFields = profiles.data?.items.find(
    (p) => p.id === project.print_profile_version_id,
  )?.requirements?.required_fields || [
    "product_name",
    "net_weight",
    "ingredients",
    "allergens",
    "manufacturer",
    "storage",
  ];
  const fieldNames: Record<string, string> = {
    product_name: "상품명",
    net_weight: "중량·용량",
    ingredients: "원재료",
    allergens: "알레르기",
    manufacturer: "제조사",
    storage: "보관 방법",
    brand_name: "브랜드명",
  };
  useEffect(() => {
    setPreflight(undefined);
    setQuote(undefined);
  }, [scene, kind]);
  useEffect(() => {
    if (!job || !isExportInProgress(job.status)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value = await api<Job>(`/jobs/${job!.id}`);
        if (!active) return;
        setJob(value);
        if (isExportInProgress(value.status)) timer = setTimeout(poll, 1800);
      } catch (e) {
        if (active) {
          setError(errorMessage(e));
          timer = setTimeout(poll, 6000);
        }
      }
    }
    timer = setTimeout(poll, 1000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [job?.id, job?.status]);
  async function saveSettings() {
    setBusy(true);
    setError("");
    try {
      const base_revision = await saveCurrent();
      const p = await api<Project>(`/projects/${project.id}/settings`, {
        method: "PATCH",
        body: JSON.stringify({
          base_revision,
          ...(!registeredStructure
            ? { template_version_id: template || null }
            : {}),
          print_profile_version_id: profile || null,
          material,
        }),
      });
      onServerProject(p);
      setNotice("제조 조건을 저장했습니다. 새 조건으로 다시 검수해 주세요.");
      setPreflight(undefined);
      setQuote(undefined);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function inspect() {
    setBusy(true);
    setError("");
    setQuote(undefined);
    try {
      const base_revision = await saveCurrent();
      const result = await api<Preflight>("/preflight", {
        method: "POST",
        body: JSON.stringify({
          project_id: project.id,
          base_revision,
          kind,
          reviewed_face_ids: reviewed,
        }),
      });
      setPreflight(result);
      setCheckedRevision(base_revision);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function exportFile(confirmQuote = false) {
    if (readOnlyRef.current) return;
    setBusy(true);
    setError("");
    try {
      const base_revision = await saveCurrent();
      if (base_revision !== checkedRevision)
        throw new Error("디자인이 변경되었습니다. 다시 검수해 주세요.");
      if (kind === "production" && !confirmQuote) {
        const result = await api<Quote>("/quotes", {
          method: "POST",
          body: JSON.stringify({
            project_id: project.id,
            base_revision,
            action: "export.production.first",
            requested_units: 1,
            face_id: scene.faces[0].id,
            input_data: { reviewed_face_ids: reviewed },
          }),
        });
        setQuote(result);
        key.current = crypto.randomUUID();
        return;
      }
      const result = await api<Job>("/exports", {
        method: "POST",
        headers: { "Idempotency-Key": key.current || crypto.randomUUID() },
        body: JSON.stringify({
          project_id: project.id,
          base_revision,
          kind,
          reviewed_face_ids: reviewed,
          ...(quote ? { quote_id: quote.id } : {}),
        }),
      });
      setJob(result);
      setQuote(undefined);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function exportEditable() {
    if (readOnlyRef.current || editablePending.current) return;
    editablePending.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const base_revision = await saveCurrent();
      if (readOnlyRef.current)
        throw new Error("편집 권한을 다시 확인해 주세요.");
      if (editableKey.current?.revision !== base_revision)
        editableKey.current = {
          revision: base_revision,
          key: crypto.randomUUID(),
        };
      const result = await api<Job>("/exports", {
        method: "POST",
        headers: { "Idempotency-Key": editableKey.current.key },
        body: JSON.stringify(editableExportBody(project.id, base_revision)),
      });
      setJob(result);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      editablePending.current = false;
      setBusy(false);
    }
  }
  async function retryJob() {
    if (readOnlyRef.current || !job || !canRetryExport(job)) return;
    setBusy(true);
    setError("");
    try {
      setJob(await api<Job>(`/jobs/${job.id}/retry`, { method: "POST" }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="export-tools">
      <Feedback error={error} notice={notice} />
      <section className="management-card">
        <h3>편집용 프로젝트 ZIP</h3>
        <p>
          현재 저장본의 장면 JSON, 연결된 원본 이미지, 재배포 가능한 글꼴과
          라이선스 안내를 한 파일로 준비합니다.
        </p>
        <p className="field-hint">
          제작 PDF나 제조 승인 파일이 아닙니다. 원본의 사용 권한과 라이선스는
          그대로 적용됩니다. 현재 편집 내용을 먼저 저장합니다. ZIP을 앱에 다시
          가져오는 기능은 아직 제공하지 않습니다.
        </p>
        <button
          className="button button-dark"
          disabled={
            busy || readOnly || (!!job && isExportInProgress(job.status))
          }
          onClick={() => void exportEditable()}
        >
          <Download size={16} /> 편집용 프로젝트 ZIP · 0크레딧
        </button>
      </section>
      {job && (
        <div
          ref={jobPanel}
          className="ai-job-state management-card"
          aria-live="polite"
        >
          <strong>
            {exportKindLabel(job.kind)} · {exportStatusLabel(job.status)}
          </strong>
          {job.kind === "editable_export" && job.result && (
            <p className="field-hint">
              저장본 {job.result.revision_number ?? "—"} · 이미지{" "}
              {job.result.asset_count ?? 0}개 · 글꼴{" "}
              {job.result.font_count ?? 0}개
              {typeof job.result.byte_size === "number"
                ? ` · ${(job.result.byte_size / 1024 / 1024).toFixed(1)} MiB`
                : ""}{" "}
              · 0크레딧
            </p>
          )}
          {job.kind === "editable_export" && job.result?.rights_notice && (
            <p className="field-hint">{job.result.rights_notice}</p>
          )}
          {job.error && (
            <Feedback
              error={
                typeof job.error === "string" ? job.error : job.error.message
              }
            />
          )}{" "}
          {job.status === "succeeded" && job.download_url && (
            <a
              className="button button-dark"
              href={
                job.download_url.startsWith("/v1/")
                  ? `/api${job.download_url}`
                  : job.download_url
              }
            >
              <Download size={16} /> {exportKindLabel(job.kind)} 다운로드
            </a>
          )}
          {canRetryExport(job) && (
            <button
              className="button button-light"
              disabled={busy || readOnly}
              onClick={() => void retryJob()}
            >
              파일 준비 다시 시도
            </button>
          )}
          {job.status === "failed" && job.kind === "production_export" && (
            <p className="field-hint">
              제작 출력은 현재 디자인을 다시 검수하고 새 견적을 확인한 뒤 요청해
              주세요.
            </p>
          )}
          <Link
            className="text-link"
            href={`/app/projects/${project.id}/exports`}
            target="_blank"
          >
            파일 이력 보기
          </Link>
        </div>
      )}

      <div className="form-two-columns">
        <section className="management-card">
          <h3>제조 조건</h3>
          <p className="field-hint">
            등록 구조의 검토 적용은 제조 승인 도면 선택과 별개입니다.
          </p>
          <button className="text-link" onClick={onOpenStructures}>
            등록 구조 검토 열기
          </button>
          <label className="field">
            도면 버전
            <select
              value={template}
              onChange={(e) => setTemplate(e.target.value)}
              disabled={readOnly || registeredStructure}
            >
              {registeredStructure ? (
                <option value={template}>현재 등록 구조 · 검토 전용</option>
              ) : (
                <option value="">현재 데모 구조</option>
              )}
              {templates.data?.items
                .filter(
                  (t) =>
                    t.status === "approved" &&
                    (!t.geometry_template_id ||
                      t.geometry_template_id === project.template_id),
                )
                .map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            승인된 인쇄 프로필
            <select
              value={profile}
              onChange={(e) => setProfile(e.target.value)}
              disabled={readOnly}
            >
              <option value="">선택되지 않음</option>
              {profiles.data?.items.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            포장 소재
            <input
              value={material}
              maxLength={120}
              onChange={(e) => setMaterial(e.target.value)}
              disabled={readOnly}
              placeholder="제조사와 합의한 소재"
            />
          </label>
          <Feedback error={templates.error || profiles.error} />
          <button
            className="button button-light"
            onClick={() => void saveSettings()}
            disabled={busy || readOnly}
          >
            제조 조건 저장
          </button>
          <p className="field-hint">
            제조사 증빙으로 승인된 도면·프로필만 제작용 검수에 사용할 수
            있습니다. 미연결 상태는 검수 결과에 표시됩니다.
          </p>
        </section>
        <section className="management-card">
          <h3>모든 면 직접 확인</h3>
          <p className="field-hint">
            3D와 각 인쇄면에서 방향, 문구, 가공 위치를 확인한 뒤 표시하세요.
            디자인을 변경하면 확인 상태가 초기화됩니다.
          </p>
          <div className="face-review-list">
            {scene.faces.map((f) => (
              <div key={f.id}>
                <label className="compact-check">
                  <input
                    type="checkbox"
                    checked={reviewed.includes(f.id)}
                    disabled={readOnly}
                    onChange={(e) =>
                      onCommit(
                        {
                          ...scene,
                          reviewed_face_ids: e.target.checked
                            ? [...reviewed, f.id]
                            : reviewed.filter((id) => id !== f.id),
                        },
                        true,
                      )
                    }
                  />
                  {f.name}
                </label>
                <button
                  className="text-link"
                  onClick={() => onFaceSelect(f.id)}
                >
                  면 열기
                </button>
              </div>
            ))}
          </div>
          {requiredFields.length > 0 && (
            <>
              <h4>표시사항 직접 확인</h4>
              {requiredFields.map((field) => (
                <label className="compact-check" key={field}>
                  <input
                    type="checkbox"
                    disabled={readOnly}
                    checked={scene.confirmed_fields?.includes(field) || false}
                    onChange={(e) =>
                      onCommit(
                        {
                          ...scene,
                          confirmed_fields: e.target.checked
                            ? [...(scene.confirmed_fields || []), field]
                            : (scene.confirmed_fields || []).filter(
                                (k) => k !== field,
                              ),
                        },
                        true,
                      )
                    }
                  />
                  {fieldNames[field] || field}
                </label>
              ))}
            </>
          )}
        </section>
      </div>
      <section className="management-card">
        <div className="management-section-heading">
          <h3>
            <ShieldCheck size={18} /> 출력 전 검수
          </h3>
          <select
            aria-label="출력 종류"
            value={kind}
            onChange={(e) => setKind(e.target.value as "review" | "production")}
          >
            <option value="review">검토용 PDF</option>
            <option value="production">제작용 번들</option>
          </select>
        </div>
        <p className="field-hint">
          검토용은 제작 파일을 대신하지 않습니다. 제작용은
          승인·크레딧·표시사항·바코드·전면 확인 조건을 서버에서 검증합니다.
        </p>
        <button
          className="button button-dark"
          disabled={busy || readOnly}
          onClick={() => void inspect()}
        >
          {busy ? (
            <LoaderCircle size={16} className="spin" />
          ) : (
            <ShieldCheck size={16} />
          )}{" "}
          현재 디자인 검수
        </button>
        {preflight && (
          <div className="preflight-results">
            <strong>
              {(
                kind === "review"
                  ? preflight.review_allowed
                  : preflight.production_allowed
              )
                ? "선택한 출력 조건을 통과했습니다."
                : "출력 전에 아래 항목을 확인해 주세요."}
            </strong>
            {preflight.issues.filter(
              (issue) => kind === "production" || issue.scope !== "production",
            ).length === 0 ? (
              <p>
                <Check size={15} /> 선택한 출력의 검수 항목을 통과했습니다.
              </p>
            ) : (
              preflight.issues
                .filter(
                  (issue) =>
                    kind === "production" || issue.scope !== "production",
                )
                .map((issue, i) => (
                  <div
                    className={`preflight-issue ${issue.severity}`}
                    key={`${issue.code}-${i}`}
                  >
                    <span className="pill">
                      {issue.severity === "error" ? "차단" : "안내"} ·{" "}
                      {issue.scope === "production" ? "제작" : "검토"}
                    </span>
                    <p>
                      {issue.field
                        ? `${fieldNames[issue.field] || issue.field}: `
                        : ""}
                      {issue.message}
                    </p>
                    {issue.face_id && (
                      <button
                        className="text-link"
                        onClick={() =>
                          onFaceSelect(issue.face_id!, issue.object_id)
                        }
                      >
                        해당 레이어·면 확인
                      </button>
                    )}
                  </div>
                ))
            )}
            {kind === "review" &&
              preflight.issues.some(
                (issue) => issue.scope === "production",
              ) && (
                <details className="production-requirements">
                  <summary>
                    실제 제작 전에 필요한 조건{" "}
                    {
                      preflight.issues.filter(
                        (issue) => issue.scope === "production",
                      ).length
                    }
                    개 보기
                  </summary>
                  <p className="field-hint">
                    아래 조건은 제작용 출력에 적용되며, 검토용 PDF 생성을 막지
                    않습니다.
                  </p>
                  {preflight.issues
                    .filter((issue) => issue.scope === "production")
                    .map((issue, i) => (
                      <div
                        className="preflight-issue warning"
                        key={`${issue.code}-${i}`}
                      >
                        <p>
                          {issue.field
                            ? `${fieldNames[issue.field] || issue.field}: `
                            : ""}
                          {issue.message}
                        </p>
                        {issue.face_id && (
                          <button
                            className="text-link"
                            onClick={() =>
                              onFaceSelect(issue.face_id!, issue.object_id)
                            }
                          >
                            해당 레이어·면 확인
                          </button>
                        )}
                      </div>
                    ))}
                </details>
              )}
            <button
              className="button button-orange"
              disabled={
                busy ||
                readOnly ||
                !(kind === "review"
                  ? preflight.review_allowed
                  : preflight.production_allowed)
              }
              onClick={() => void exportFile()}
            >
              <Download size={16} />
              {kind === "review"
                ? "검토용 PDF 만들기 · 무료"
                : "제작용 크레딧 견적 확인"}
            </button>
          </div>
        )}
        {quote && (
          <div className="quote-confirmation">
            <h3>{quote.credit_total} 크레딧</h3>
            <p>
              사용 가능 {quote.balance_before} → 예약 후 {quote.balance_after}
            </p>
            <p className="field-hint">
              동일 조건의 재출력 여부는 서버에서 판정합니다.
            </p>
            <button
              className="button button-orange"
              disabled={
                busy ||
                readOnly ||
                new Date(quote.expires_at).getTime() < Date.now()
              }
              onClick={() => void exportFile(true)}
            >
              견적 확인하고 제작용 출력
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
