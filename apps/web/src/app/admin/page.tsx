"use client";
import { useState } from "react";
import {
  Check,
  Info,
  Plus,
  LoaderCircle,
  ShieldCheck,
  FileUp,
} from "lucide-react";
import {
  ManagementPage,
  Feedback,
  Loading,
  Dialog,
} from "@/components/management";
import { useApiData, dateTime } from "@/lib/business";
import { api, errorMessage } from "@/lib/api";
import { RegistryConditionFields } from "@/components/registry-condition-fields";
import { RegisteredStructureAdmin } from "@/components/registered-structure-admin";
import { PrintEngineAdmin } from "@/components/print-engine-admin";
import { FinishingApprovalPicker, type FinishingDraft } from "@/components/finishing-approval-picker";
import {
  ProviderBudgetSummary,
  type ProviderBudget,
} from "@/components/provider-budget-summary";
type Version = {
  id: string;
  name: string;
  status: string;
  is_demo: boolean;
  manufacturer?: string;
  source?: string;
  license?: string;
  geometry_template_id?: string;
  approval?: unknown;
  structure_definition?: unknown;
  review_available?: boolean;
};
type Overview = {
  readiness: Array<{
    key: string;
    label: string;
    ready: boolean;
    detail: string;
  }>;
  counts: Record<string, number>;
  audit: Array<{
    id: string;
    action: string;
    created_at: string;
    actor_id?: string;
    details?: unknown;
    entity_id?: string;
  }>;
  provider_costs: Array<Record<string, unknown>>;
  provider_budget?: ProviderBudget;
  intake_stats: Array<{ status: string; category: string; count: number }>;
  intake_metrics?: {
    manufacturer: IntakeMetrics;
    test: IntakeMetrics;
    excluded_legacy_records: number;
  };
};
type IntakeMetrics = {
  total_jobs: number;
  responded_jobs: number;
  technical_pass_jobs: number;
  technical_rejected_jobs: number;
  aesthetic_change_jobs: number;
  pending_jobs: number;
  technical_acceptance_rate: number | null;
};
export default function Admin() {
  const overview = useApiData<Overview>("/admin/overview");
  const versions = useApiData<{ items: Version[] }>("/admin/template-versions");
  const profiles = useApiData<{ items: Version[] }>("/admin/print-profiles");
  const [dialog, setDialog] = useState<
    "version" | "profile" | "review" | "approve" | "revoke" | null
  >(null);
  const [target, setTarget] = useState<Version>();
  const [name, setName] = useState("");
  const [manufacturer, setManufacturer] = useState("");
  const [source, setSource] = useState("");
  const [license, setLicense] = useState("");
  const [material, setMaterial] = useState("");
  const [dimensions, setDimensions] = useState(
    '{"width_mm":160,"height_mm":230}',
  );
  const [family, setFamily] = useState("");
  const [kind, setKind] = useState("three-side-seal");
  const [finishing, setFinishing] = useState<FinishingDraft>();
  const [demo, setDemo] = useState(true);
  const [evidence, setEvidence] = useState("");
  const [notes, setNotes] = useState("");
  const [publicReason, setPublicReason] = useState("");
  const [approver, setApprover] = useState("");
  const [requirements, setRequirements] = useState(
    '{"color_space":"RGB","pdf_standard":"PDF","layout":"face_pages","bleed_mm":0,"font_mode":"embedded","min_ppi":300}',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  function open(type: typeof dialog, item?: Version) {
    setDialog(type);
    setTarget(item);
    setError("");
    setName("");
    setNotes("");
    setPublicReason("");
    setEvidence("");
    setFinishing(undefined);
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (dialog === "approve" && target)
        await api(
          `/admin/${versions.data?.items.some((v) => v.id === target.id) ? "template-versions" : "print-profiles"}/${target.id}/approve`,
          {
            method: "POST",
            body: JSON.stringify({
              evidence_asset_id: evidence,
              notes,
              approved_by_name: approver,
            }),
          },
        );
      else if (dialog === "review" && target)
        await api(
          `/admin/${versions.data?.items.some((v) => v.id === target.id) ? "template-versions" : "print-profiles"}/${target.id}/review`,
          { method: "POST", body: JSON.stringify({ reason: notes }) },
        );
      else if (dialog === "revoke" && target)
        await api(
          `/admin/${versions.data?.items.some((v) => v.id === target.id) ? "template-versions" : "print-profiles"}/${target.id}/revoke`,
          { method: "POST", body: JSON.stringify({ reason: notes, public_reason: publicReason.trim() || null }) },
        );
      else
        await api(
          `/admin/${dialog === "version" ? "template-versions" : "print-profiles"}`,
          {
            method: "POST",
            body: JSON.stringify({
              name,
              manufacturer,
              is_demo: demo,
              geometry_template_id: kind,
              billing_family_key: dialog === "version" ? family : null,
              approved_dimensions: JSON.parse(dimensions),
              ...(dialog === "version" && finishing ? { approved_finishing: finishing.approved_finishing } : {}),
              source,
              license,
              material,
              requirements: JSON.parse(requirements),
            }),
          },
        );
      setDialog(null);
      setNotice(
        "관리 변경을 저장했습니다. 감사 기록과 승인 상태에 반영됩니다.",
      );
      versions.refresh();
      profiles.refresh();
      overview.refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function upload(file: File) {
    setBusy(true);
    try {
      if (file.size > 4 * 1024 * 1024)
        throw new Error("증빙은 4 MiB 이하로 올려 주세요.");
      const body = new FormData();
      body.set("file", file);
      const result = await api<{ id: string }>("/admin/evidence", {
        method: "POST",
        body,
      });
      setEvidence(result.id);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <ManagementPage
      eyebrow="OPERATIONS & READINESS"
      title="운영 관리"
      description="제조사 승인 조합, 인쇄 조건, 비용과 감사 이력을 관리합니다."
      actions={<ShieldCheck size={26} />}
    >
      <Feedback
        error={overview.error || versions.error || profiles.error || error}
        notice={notice}
      />
      {overview.loading ? (
        <Loading />
      ) : (
        overview.data && (
          <>
            <div className="readiness-grid">
              {overview.data.readiness.map((check) => (
                <article
                  key={check.key}
                  className={`readiness-item ${check.ready ? "ready" : "pending"}`}
                >
                  {check.ready ? <Check size={19} /> : <Info size={19} />}
                  <div>
                    <strong>{check.label}</strong>
                    <p>{check.detail}</p>
                  </div>
                </article>
              ))}
            </div>
            <div className="wallet-stats">
              {Object.entries(overview.data.counts).map(([label, value]) => (
                <div key={label}>
                  <span>{label}</span>
                  <strong>{value}</strong>
                </div>
              ))}
            </div>
            <RegisteredStructureAdmin
              items={versions.data?.items || []}
              onAction={(action, id) => {
                const item = versions.data?.items.find(version => version.id === id);
                if (item) open(action, item);
              }}
              onRegistered={(message) => {
                setNotice(message);
                versions.refresh();
                overview.refresh();
              }}
            />
            <PrintEngineAdmin onRegistered={() => { profiles.refresh(); overview.refresh(); }} />
            {(
              [
                {
                  title: "제조사 템플릿",
                  type: "version",
                  items: versions.data?.items.filter(
                    (item) => !item.structure_definition,
                  ),
                },
                {
                  title: "인쇄 프로파일",
                  type: "profile",
                  items: profiles.data?.items,
                },
              ] as const
            ).map((section) => (
              <section className="management-card" key={section.type}>
                <div className="management-section-heading">
                  <h2>{section.title}</h2>
                  <button
                    className="button button-light button-sm"
                    onClick={() => open(section.type)}
                  >
                    <Plus size={15} /> 새 버전 등록
                  </button>
                </div>
                <div className="management-table-wrap">
                  <table className="management-table">
                    <thead>
                      <tr>
                        <th>이름·제조사</th>
                        <th>상태</th>
                        <th>출처·사용권</th>
                        <th>작업</th>
                      </tr>
                    </thead>
                    <tbody>
                      {section.items?.map((item) => (
                        <tr key={item.id}>
                          <td>
                            <strong>{item.name}</strong>
                            <small>
                              {item.manufacturer || "제조사 미확정"}
                            </small>
                          </td>
                          <td>
                            <span className="pill">
                              {item.is_demo ? "데모 / " : ""}
                              {({ draft: "초안", review: "검토 중", approved: "승인", revoked: "철회" } as Record<string, string>)[item.status] ?? item.status}
                            </span>
                          </td>
                          <td>
                            {item.source || "—"}
                            <small>{item.license}</small>
                          </td>
                          <td>
                            {item.status === "approved" ? (
                              <button
                                className="button button-light button-sm"
                                onClick={() => open("revoke", item)}
                              >
                                승인 철회
                              </button>
                            ) : item.status === "draft" ? (
                              <button className="button button-light button-sm" disabled={item.is_demo} onClick={() => open("review", item)}>검토 요청</button>
                            ) : (
                              <button
                                className="button button-light button-sm"
                                disabled={
                                  item.is_demo || item.status !== "review"
                                }
                                onClick={() => open("approve", item)}
                              >
                                증빙 확인 후 승인
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ))}
            <section className="management-card">
              <h2>감사 이력</h2>
              <div className="management-table-wrap">
                <table className="management-table">
                  <thead>
                    <tr>
                      <th>일시</th>
                      <th>동작</th>
                      <th>담당자</th>
                      <th>내용</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overview.data.audit.map((row) => (
                      <tr key={row.id}>
                        <td>{dateTime(row.created_at)}</td>
                        <td>{row.action}</td>
                        <td>{row.actor_id}</td>
                        <td className="json-cell">
                          {JSON.stringify(row.details)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="management-card">
              <h2>공급자 비용과 입고 확인</h2>
              <ProviderBudgetSummary budget={overview.data.provider_budget} />
              <p className="field-hint">
                사용자에게 무료인 실패·취소 작업의 공급자 비용도 함께
                확인합니다.
              </p>
              <div className="management-table-wrap">
                <table className="management-table">
                  <tbody>
                    {overview.data.provider_costs.map((row, index) => (
                      <tr key={index}>
                        <td className="json-cell">{JSON.stringify(row)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="intake-statistics">
                {overview.data.intake_stats.map((row, index) => (
                  <span key={index}>
                    {row.status} · {row.category}: {row.count}건
                  </span>
                ))}
              </div>
              {overview.data.intake_metrics && (
                <>
                  <h3>출력 작업별 기술 접수율</h3>
                  <p className="field-hint">
                    제조사 회신은 작성자가 확인해 기록한 결과입니다. 외부 검증
                    결과를 뜻하지 않습니다. 같은 출력 작업의 중복 기록은 한 번만
                    집계하며, 회신 대기와 미분류 과거 기록은 비율에서
                    제외합니다.
                  </p>
                  <div className="management-table-wrap">
                    <table className="management-table">
                      <thead>
                        <tr>
                          <th>기록 출처</th>
                          <th>회신한 출력 작업</th>
                          <th>기술 통과</th>
                          <th>기술 반려</th>
                          <th>미적 수정</th>
                          <th>회신 대기</th>
                          <th>기술 접수율</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(["manufacturer", "test"] as const).map((source) => {
                          const metric = overview.data!.intake_metrics![source];
                          return (
                            <tr key={source}>
                              <td>
                                {source === "manufacturer"
                                  ? "제조사 회신 · 작성자 기록"
                                  : "내부 시험"}
                              </td>
                              <td>{metric.responded_jobs}</td>
                              <td>{metric.technical_pass_jobs}</td>
                              <td>{metric.technical_rejected_jobs}</td>
                              <td>{metric.aesthetic_change_jobs}</td>
                              <td>{metric.pending_jobs}</td>
                              <td>
                                {metric.technical_acceptance_rate === null
                                  ? "회신 없음"
                                  : `${(metric.technical_acceptance_rate * 100).toFixed(1)}%`}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <p className="field-hint">
                    미적 수정은 기술 반려와 구분합니다. 미분류 과거 기록{" "}
                    {overview.data.intake_metrics.excluded_legacy_records}건은
                    집계에서 제외했습니다.
                  </p>
                </>
              )}
            </section>
          </>
        )
      )}
      {dialog && (
        <Dialog
          title={
            dialog === "approve"
              ? "제조사 증빙을 확인한 승인"
              : dialog === "revoke"
                ? "기존 승인 철회"
                : dialog === "review"
                  ? "제조 조건 검토 요청"
                : dialog === "version"
                  ? "템플릿 버전 등록"
                  : "인쇄 프로파일 등록"
          }
          onClose={() => setDialog(null)}
        >
          <form onSubmit={submit}>
            {dialog === "approve" ? (
              <>
                <p className="dialog-description">
                  {target?.name} · 실제 도면과 인쇄 조건, 사용 허락을 확인한
                  자료를 첨부하세요. 데모는 승인할 수 없습니다.
                </p>
                <label className="field">
                  승인 증빙 (PDF · PNG · JPG)
                  <input
                    type="file"
                    required={!evidence}
                    accept="application/pdf,image/png,image/jpeg"
                    onChange={(e) => {
                      if (e.target.files?.[0]) void upload(e.target.files[0]);
                    }}
                  />
                </label>
                {evidence && (
                  <p className="field-hint">증빙 파일 업로드 완료</p>
                )}
                <label className="field">
                  확인 담당자 이름
                  <input
                    required
                    value={approver}
                    onChange={(e) => setApprover(e.target.value)}
                  />
                </label>
                <label className="field">
                  확인 기록
                  <textarea
                    required
                    minLength={3}
                    maxLength={4000}
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    rows={3}
                  />
                </label>
              </>
            ) : dialog === "review" ? (
              <>
                <p className="dialog-description">{target?.name}을 검토 중으로 전환합니다. 도면·재질·인쇄 조건과 사용 허락을 검토한 뒤 증빙을 첨부해야 승인할 수 있습니다. 검토 요청만으로 제작 출력은 열리지 않습니다.</p>
                <label className="field">검토 요청 사유<textarea required minLength={5} maxLength={1000} value={notes} onChange={e => setNotes(e.target.value)} rows={3} /></label>
              </>
            ) : dialog === "revoke" ? (
              <>
                <p className="dialog-description">
                  철회 후 해당 조합의 새로운 제작 출력은 차단됩니다. 기존
                  결과물은 이력과 함께 보존됩니다.
                </p>
                <label className="field">
                  내부 철회 사유
                  <textarea
                    required
                    minLength={3}
                    maxLength={4000}
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    rows={3}
                  />
                </label>
                <label className="field">고객에게 표시할 사유 (선택)<textarea minLength={5} maxLength={1000} value={publicReason} onChange={e => setPublicReason(e.target.value)} rows={3} /><small>완료된 출력 파일 이력에 표시됩니다. 개인정보나 내부 검토 내용은 입력하지 마세요. 비워 두면 기본 철회 안내가 표시됩니다.</small></label>
              </>
            ) : (
              <>
                <div className="form-two-columns">
                  <label className="field">
                    버전 이름
                    <input
                      required
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                  </label>
                  <label className="field">
                    제조사
                    <input
                      required
                      value={manufacturer}
                      onChange={(e) => setManufacturer(e.target.value)}
                    />
                  </label>
                </div>
                <label className="field">
                  포장 구조
                  <select
                    value={kind}
                    onChange={(e) => { setKind(e.target.value); setFinishing(undefined); }}
                  >
                    <option value="three-side-seal">3면 실링</option>
                    <option value="stand-up-pouch">스탠드형</option>
                    <option value="folding-box">접이식 박스</option>
                  </select>
                </label>
                {dialog === "version" && (
                  <>
                    <label className="field">
                      제조사 구조 패밀리 식별자
                      <input
                        required
                        value={family}
                        onChange={(e) => setFamily(e.target.value)}
                        maxLength={100}
                        placeholder="제조사가 같은 구조로 분류한 고유 코드"
                      />
                    </label>
                    <details className="production-requirements">
                      <summary>고급 치수 JSON</summary>
                      <label className="field">
                        승인할 정확한 치수 (mm)
                        <textarea
                          rows={3}
                          value={dimensions}
                          onChange={(e) => { setDimensions(e.target.value); setFinishing(undefined); }}
                          spellCheck={false}
                        />
                      </label>
                    </details>
                    <p className="field-hint">
                      width_mm, height_mm와 스탠드의 bottom_mm 또는 상자의
                      depth_mm를 입력하세요. 증빙 치수와 일치하는 조건만
                      승인됩니다.
                    </p>
                  </>
                )}
                <label className="field">
                  도면·조건 출처
                  <input
                    required
                    value={source}
                    onChange={(e) => setSource(e.target.value)}
                  />
                </label>
                <label className="field">
                  사용 허락·라이선스
                  <input
                    required
                    value={license}
                    onChange={(e) => setLicense(e.target.value)}
                  />
                </label>
                <label className="field">
                  재질
                  <input
                    value={material}
                    onChange={(e) => setMaterial(e.target.value)}
                  />
                </label>
                <label className="compact-check">
                  <input
                    type="checkbox"
                    checked={demo}
                    onChange={(e) => setDemo(e.target.checked)}
                  />{" "}
                  자체 제작 데모 (승인·제작 출력 불가)
                </label>
                <RegistryConditionFields
                  kind={kind}
                  dimensions={dimensions}
                  requirements={requirements}
                  onDimensions={(value) => { setDimensions(value); setFinishing(undefined); }}
                  onRequirements={setRequirements}
                  showDimensions={dialog === "version"}
                />
                {dialog === "version" && kind !== "folding-box" && <FinishingApprovalPicker key={`${kind}:${dimensions}`} value={finishing} onClear={() => setFinishing(undefined)} onPick={(value) => {
                  setKind(value.geometry_template_id);
                  setDimensions(JSON.stringify(value.approved_dimensions));
                  setFinishing(value);
                }} />}
                <details className="production-requirements">
                  <summary>고급 인쇄 조건 JSON</summary>
                  <label className="field">
                    기술 조건 JSON
                    <textarea
                      rows={5}
                      value={requirements}
                      onChange={(e) => setRequirements(e.target.value)}
                      spellCheck={false}
                    />
                  </label>
                </details>
                <p className="field-hint">
                  지원 capability를 넘는 PDF/X·색상 조건은 승인과 별개로 제작
                  출력이 차단됩니다.
                </p>
              </>
            )}
            <Feedback error={error} />
            <button
              className="button button-dark full-width"
              disabled={busy || (dialog === "approve" && !evidence)}
            >
              {busy ? (
                <LoaderCircle className="spin" size={17} />
              ) : dialog === "approve" ? (
                "증빙 확인 완료·승인"
              ) : dialog === "revoke" ? (
                "승인 철회 확인"
              ) : dialog === "review" ? (
                "검토 요청 기록"
              ) : (
                "버전 등록"
              )}
            </button>
          </form>
        </Dialog>
      )}
    </ManagementPage>
  );
}
