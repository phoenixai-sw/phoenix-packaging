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
} from "lucide-react";
import { Pouch } from "@/components/pouch";
import { api, errorMessage } from "@/lib/api";
import type { Project } from "@editor/model";
export default function NewProject() {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [width, setWidth] = useState("230");
  const [height, setHeight] = useState("310");
  const [unit, setUnit] = useState("mm");
  const [brand, setBrand] = useState("");
  const [product, setProduct] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const widthMM = Number(width) * (unit === "cm" ? 10 : 1);
  const heightMM = Number(height) * (unit === "cm" ? 10 : 1);
  function next(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    if (
      !Number.isFinite(widthMM) ||
      widthMM < 60 ||
      widthMM > 600 ||
      !Number.isFinite(heightMM) ||
      heightMM < 80 ||
      heightMM > 800
    ) {
      setError("폭은 60–600 mm, 높이는 80–800 mm 범위로 입력해 주세요.");
      return;
    }
    setStep(2);
  }
  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const project = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim() || `${product.trim()} 패키지`,
          product_name: product.trim(),
          brand_name: brand.trim(),
          width_mm: widthMM,
          height_mm: heightMM,
          template_id: "three-side-seal",
          description: description.trim(),
        }),
      });
      router.push(`/app/projects/${project.id}/editor`);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }
  return (
    <main className="new-project-content">
      <Link href="/app" className="text-link muted">
        <ArrowLeft size={16} /> 내 프로젝트
      </Link>
      <div className="page-heading">
        <div className="eyebrow">LET’S MAKE SOMETHING GOOD</div>
        <h1>어떤 제품을 담을까요?</h1>
        <p>우리 제품에 꼭 맞는 패키지를 함께 만들어 봐요.</p>
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
              <div className="template-option selected">
                <div className="mini-pouch" />
                <div>
                  <strong>3면 실링 봉투</strong>
                  <p>차, 분말, 스낵 등 가볍게 담는 제품에</p>
                  <span className="pill">데모 구조 · 제조사 미승인</span>
                </div>
                <span className="selected-check">
                  <Check size={15} />
                </span>
              </div>
              <div className="template-option disabled">
                <div className="mini-pouch stand" />
                <div>
                  <strong>스탠드형 봉투</strong>
                  <p>바닥이 있어 세워 두는 제품에</p>
                </div>
                <span className="pill">준비 중</span>
              </div>
              <h2 className="dimensions-title">
                <Ruler size={21} /> 완성 규격
              </h2>
              <div className="dimension-fields">
                <label className="field">
                  폭
                  <input
                    type="number"
                    step="0.1"
                    min={unit === "cm" ? 6 : 60}
                    max={unit === "cm" ? 60 : 600}
                    required
                    value={width}
                    onChange={(e) => setWidth(e.target.value)}
                  />
                </label>
                <span>×</span>
                <label className="field">
                  높이
                  <input
                    type="number"
                    step="0.1"
                    min={unit === "cm" ? 8 : 80}
                    max={unit === "cm" ? 80 : 800}
                    required
                    value={height}
                    onChange={(e) => setHeight(e.target.value)}
                  />
                </label>
                <label className="field unit-field">
                  단위
                  <select
                    value={unit}
                    onChange={(e) => {
                      const newUnit = e.target.value;
                      setWidth(
                        String(Number(width) * (newUnit === "cm" ? 0.1 : 10)),
                      );
                      setHeight(
                        String(Number(height) * (newUnit === "cm" ? 0.1 : 10)),
                      );
                      setUnit(newUnit);
                    }}
                  >
                    <option value="mm">mm</option>
                    <option value="cm">cm</option>
                  </select>
                </label>
              </div>
              <p className="field-hint">
                시험 규격: 폭 60–600 mm · 높이 80–800 mm
              </p>
              <div className="alert alert-info">
                <Info size={17} />
                <span>
                  실링 10 mm, 안전 여백 5 mm는 데모 설정입니다. 실제 제조사
                  규격이 아니며 검토용으로만 사용하세요.
                </span>
              </div>
              {error && (
                <div className="alert alert-error" role="alert">
                  {error}
                </div>
              )}
              <button className="button button-dark full-width">
                상품 정보 입력하기 <ArrowRight size={18} />
              </button>
            </form>
          ) : (
            <form onSubmit={create}>
              <h2>제품의 이야기를 들려주세요.</h2>
              <p className="form-description">
                입력한 상품명과 브랜드명은 편집기에서 자유롭게 바꿀 수 있어요.
              </p>
              <label className="field">
                브랜드명
                <input
                  value={brand}
                  onChange={(e) => setBrand(e.target.value)}
                  placeholder="예: 작은 일상"
                  required
                  maxLength={120}
                />
              </label>
              <label className="field">
                상품명
                <input
                  value={product}
                  onChange={(e) => setProduct(e.target.value)}
                  placeholder="예: 제주 말차"
                  required
                  maxLength={160}
                />
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
                  rows={3}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="제품의 특징이나 담고 싶은 이야기를 적어두세요."
                  maxLength={2000}
                />
              </label>
              <p className="field-hint">
                기본 시안을 제공합니다. 현재 실제 AI 이미지 생성은 연결되지
                않았습니다.
              </p>
              {error && (
                <div className="alert alert-error" role="alert">
                  {error}
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
                <button className="button button-dark" disabled={busy}>
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
            <Pouch title={product || undefined} />
          </div>
          <div className="preview-dimensions">
            <span>
              {widthMM} × {heightMM} mm
            </span>
            <small>3면 실링 봉투 · 앞면 / 뒷면</small>
          </div>
          <p>형태를 설명하기 위한 예시 이미지입니다.</p>
        </aside>
      </div>
    </main>
  );
}
