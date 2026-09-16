"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Info,
  LoaderCircle,
  Package,
  Ruler,
  Box,
} from "lucide-react";
import { Pouch } from "@/components/pouch";
import { api, errorMessage } from "@/lib/api";
import {
  useApiData,
  canEdit,
  type BrandData,
  type ProductData,
  type WorkspaceData,
} from "@/lib/business";
import { useSession } from "@/components/workspace";
import type { Project } from "@editor/model";
type Template = {
  id: string;
  geometry_template_id?: string;
  approved_dimensions?: {
    width_mm: number;
    height_mm: number;
    bottom_mm?: number;
    depth_mm?: number;
  };
  name: string;
  description?: string;
  approval_status?: string;
  status: string;
  faces?: string[];
  default_width_mm?: number;
  default_height_mm?: number;
  min_width_mm?: number;
  max_width_mm?: number;
  min_height_mm?: number;
  max_height_mm?: number;
};
function faceCount(template?: Template) {
  const kind = template?.geometry_template_id || template?.id;
  return (
    template?.faces?.length ||
    (kind === "folding-box" ? 6 : kind === "stand-up-pouch" ? 3 : 2)
  );
}
export default function NewProject() {
  const router = useRouter();
  const session = useSession();
  const templates = useApiData<{ items: Template[] }>("/templates");
  const brands = useApiData<{ items: BrandData[] }>("/brands");
  const products = useApiData<{ items: ProductData[] }>("/products");
  const workspaces = useApiData<{ items: WorkspaceData[] }>("/workspaces");
  const [step, setStep] = useState(1);
  const [templateId, setTemplateId] = useState("three-side-seal");
  const [width, setWidth] = useState("230");
  const [height, setHeight] = useState("310");
  const [extra, setExtra] = useState("80");
  const [unit, setUnit] = useState("mm");
  const [brand, setBrand] = useState("");
  const [brandId, setBrandId] = useState("");
  const [product, setProduct] = useState("");
  const [variantId, setVariantId] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [createdProject, setCreatedProject] = useState<string>();
  const template = templates.data?.items.find((t) => t.id === templateId);
  const factor = unit === "cm" ? 10 : 1;
  const widthMM = Number(width) * factor,
    heightMM = Number(height) * factor,
    extraMM = Number(extra) * factor;
  const canonicalTemplate = template?.geometry_template_id || templateId;
  const geometryInput = {
    template_id: canonicalTemplate,
    width_mm: widthMM,
    height_mm: heightMM,
    ...(canonicalTemplate === "stand-up-pouch"
      ? { bottom_mm: extraMM }
      : canonicalTemplate === "folding-box"
        ? { depth_mm: extraMM }
        : {}),
  };
  async function next(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api("/geometry/validate", {
        method: "POST",
        body: JSON.stringify(geometryInput),
      });
      setStep(2);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          ...geometryInput,
          name: name.trim() || `${product.trim()} 패키지`,
          product_name: product.trim(),
          brand_name: brand.trim(),
          description,
          brand_id: brandId || null,
          product_variant_id: variantId || null,
          workspace_id: workspaceId || null,
        }),
      });
      setCreatedProject(result.id);
      if (template?.status === "approved")
        await api(`/projects/${result.id}/settings`, {
          method: "PATCH",
          body: JSON.stringify({
            base_revision: result.base_revision,
            template_version_id: template.id,
          }),
        });
      router.push(`/app/projects/${result.id}/editor`);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }
  function chooseVariant(id: string) {
    setVariantId(id);
    for (const p of products.data?.items || []) {
      const v = p.variants.find((v) => v.id === id);
      if (v) {
        setProduct(p.name);
        setDescription(p.description || "");
        setBrandId(p.brand_id || "");
        setBrand(
          brands.data?.items.find((b) => b.id === p.brand_id)?.name || "",
        );
        break;
      }
    }
  }
  function chooseTemplate(t: Template) {
    setTemplateId(t.id);
    if (t.status === "approved" && t.approved_dimensions) {
      setUnit("mm");
      setWidth(String(t.approved_dimensions.width_mm));
      setHeight(String(t.approved_dimensions.height_mm));
      setExtra(
        String(
          t.approved_dimensions.bottom_mm ||
            t.approved_dimensions.depth_mm ||
            80,
        ),
      );
    }
  }
  if (!canEdit(session))
    return (
      <div className="empty-state">
        <h2>열람 권한으로 이용 중입니다.</h2>
        <p>프로젝트를 만들려면 소유자에게 편집 권한을 요청해 주세요.</p>
        <Link href="/app" className="button button-light">
          프로젝트로 돌아가기
        </Link>
      </div>
    );
  return (
    <main className="new-project-content">
      <Link href="/app" className="text-link muted">
        <ArrowLeft size={16} /> 내 프로젝트
      </Link>
      <div className="page-heading">
        <div className="eyebrow">LET’S MAKE SOMETHING GOOD</div>
        <h1>어떤 제품을 담을까요?</h1>
        <p>형태와 규격을 정하고 우리 브랜드의 이야기를 더하세요.</p>
      </div>
      <div className="creation-progress">
        <span className={step === 1 ? "active" : "complete"}>
          <b>{step > 1 ? <Check size={14} /> : 1}</b> 포장과 규격
        </span>
        <i />
        <span className={step === 2 ? "active" : ""}>
          <b>2</b> 상품 정보
        </span>
        <i />
        <span>
          <b>3</b> 디자인 편집
        </span>
      </div>
      <div className="creation-layout">
        <section className="creation-form">
          {step === 1 ? (
            <form onSubmit={next}>
              <h2>
                <Package size={21} /> 포장 형태 선택
              </h2>
              {templates.loading ? (
                <div className="loading-state">
                  포장 종류를 확인하고 있어요.
                </div>
              ) : (
                templates.data?.items.map((t) => (
                  <button
                    type="button"
                    key={t.id}
                    onClick={() => chooseTemplate(t)}
                    className={`template-option template-option-button ${templateId === t.id ? "selected" : ""}`}
                  >
                    <div
                      className={`mini-pouch ${t.id === "folding-box" ? "mini-box" : t.id === "stand-up-pouch" ? "stand" : ""}`}
                    />
                    <div>
                      <strong>{t.name}</strong>
                      <p>{t.description || `${faceCount(t)}개의 인쇄면`}</p>
                      <span className="pill">
                        {t.status === "approved"
                          ? "제조사 승인"
                          : "데모 구조 · 제조사 미승인"}
                      </span>
                    </div>
                    {templateId === t.id && (
                      <span className="selected-check">
                        <Check size={15} />
                      </span>
                    )}
                  </button>
                ))
              )}
              <h2 className="dimensions-title">
                <Ruler size={21} /> 완성 규격
              </h2>
              <div className="dimension-fields">
                <label className="field">
                  폭
                  <input
                    type="number"
                    required
                    min={1}
                    step="0.1"
                    value={width}
                    onChange={(e) => setWidth(e.target.value)}
                  />
                </label>
                <span>×</span>
                <label className="field">
                  높이
                  <input
                    type="number"
                    required
                    min={1}
                    step="0.1"
                    value={height}
                    onChange={(e) => setHeight(e.target.value)}
                  />
                </label>
                <label className="field unit-field">
                  단위
                  <select
                    value={unit}
                    onChange={(e) => {
                      const n = e.target.value;
                      const scale = n === "cm" ? 0.1 : 10;
                      setWidth(String(Number(width) * scale));
                      setHeight(String(Number(height) * scale));
                      setExtra(String(Number(extra) * scale));
                      setUnit(n);
                    }}
                  >
                    <option value="mm">mm</option>
                    <option value="cm">cm</option>
                  </select>
                </label>
              </div>
              {canonicalTemplate !== "three-side-seal" && (
                <label className="field extra-dimension">
                  {canonicalTemplate === "folding-box"
                    ? "상자 깊이"
                    : "펼친 바닥 거싯 폭"}{" "}
                  ({unit})
                  <input
                    type="number"
                    required
                    min={1}
                    step="0.1"
                    value={extra}
                    onChange={(e) => setExtra(e.target.value)}
                  />
                </label>
              )}
              <p className="field-hint">
                {template?.min_width_mm
                  ? `폭 ${template.min_width_mm}–${template.max_width_mm} mm · 높이 ${template.min_height_mm}–${template.max_height_mm} mm`
                  : "입력한 규격과 가공 영역을 서버에서 확인합니다."}
              </p>
              <div className="alert alert-info">
                <Info size={17} />
                <span>
                  데모 구조의 가공 치수는 시험용입니다. 제조사 승인 상태와 출력
                  검수를 통과한 조건에서만 제작용 파일이 열립니다.
                </span>
              </div>
              {(error || templates.error) && (
                <div className="alert alert-error" role="alert">
                  {error || templates.error}
                </div>
              )}
              <button
                className="button button-dark full-width"
                disabled={busy || !template}
              >
                {busy ? (
                  <LoaderCircle className="spin" size={17} />
                ) : (
                  <>
                    상품 정보 입력하기 <ArrowRight size={18} />
                  </>
                )}
              </button>
            </form>
          ) : (
            <form onSubmit={create}>
              <h2>제품의 이야기를 들려주세요.</h2>
              <label className="field">
                등록한 상품 변형
                <select
                  value={variantId}
                  onChange={(e) => chooseVariant(e.target.value)}
                >
                  <option value="">직접 입력</option>
                  {products.data?.items.flatMap((p) =>
                    p.variants.map((v) => (
                      <option key={v.id} value={v.id}>
                        {p.name} · {v.name}
                      </option>
                    )),
                  )}
                </select>
              </label>
              <label className="field">
                등록한 브랜드
                <select
                  value={brandId}
                  onChange={(e) => {
                    setBrandId(e.target.value);
                    setBrand(
                      brands.data?.items.find((b) => b.id === e.target.value)
                        ?.name || "",
                    );
                  }}
                >
                  <option value="">직접 입력</option>
                  {brands.data?.items.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="form-two-columns">
                <label className="field">
                  브랜드명
                  <input
                    required
                    value={brand}
                    onChange={(e) => setBrand(e.target.value)}
                    maxLength={120}
                  />
                </label>
                <label className="field">
                  상품명
                  <input
                    required
                    value={product}
                    onChange={(e) => setProduct(e.target.value)}
                    maxLength={160}
                  />
                </label>
              </div>
              <label className="field">
                작업 공간
                <select
                  value={workspaceId}
                  onChange={(e) => setWorkspaceId(e.target.value)}
                >
                  <option value="">기본 작업 공간</option>
                  {workspaces.data?.items.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                프로젝트 이름 <span className="optional">선택</span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={
                    product ? `${product} 패키지` : "내 첫 패키지 프로젝트"
                  }
                  maxLength={160}
                />
              </label>
              <label className="field">
                상품 메모 <span className="optional">선택</span>
                <textarea
                  rows={2}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  maxLength={2000}
                />
              </label>
              {error && (
                <div className="alert alert-error" role="alert">
                  {error}
                </div>
              )}
              {createdProject && (
                <div className="alert alert-info">
                  프로젝트는 생성되었습니다. 제조 조건 저장에 문제가 있었다면
                  편집기에서 다시 선택할 수 있습니다.{" "}
                  <Link
                    className="text-link"
                    href={`/app/projects/${createdProject}/editor`}
                  >
                    생성된 프로젝트 열기
                  </Link>
                </div>
              )}
              <div className="form-actions">
                <button
                  type="button"
                  className="button button-light"
                  onClick={() => setStep(1)}
                  disabled={busy}
                >
                  이전
                </button>
                <button
                  className="button button-dark"
                  disabled={busy || !!createdProject}
                >
                  {busy ? (
                    <LoaderCircle className="spin" size={18} />
                  ) : (
                    <>
                      프로젝트 만들기 <ArrowRight size={18} />
                    </>
                  )}
                </button>
              </div>
            </form>
          )}
        </section>
        <aside className="creation-preview">
          <span className="eyebrow">YOUR PACKAGE STARTS HERE</span>
          <div className="creation-pouch">
            {canonicalTemplate === "folding-box" ? (
              <Box size={165} strokeWidth={0.6} />
            ) : (
              <Pouch title={product || undefined} />
            )}
          </div>
          <div className="preview-dimensions">
            <span>
              {widthMM} × {heightMM}
              {canonicalTemplate !== "three-side-seal"
                ? ` × ${extraMM}`
                : ""}{" "}
              mm
            </span>
            <small>
              {template?.name} · {faceCount(template)}개 면
            </small>
          </div>
          <p>
            형태를 설명하기 위한 예시입니다. 실제 도면은 편집기에서 확인하세요.
          </p>
        </aside>
      </div>
    </main>
  );
}
