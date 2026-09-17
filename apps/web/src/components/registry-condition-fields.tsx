"use client";
function parse(value: string): Record<string, unknown> | null {
  try {
    const data = JSON.parse(value);
    return data && typeof data === "object" && !Array.isArray(data)
      ? data
      : null;
  } catch {
    return null;
  }
}
export function RegistryConditionFields({
  kind,
  dimensions,
  requirements,
  onDimensions,
  onRequirements,
  showDimensions,
}: {
  kind: string;
  dimensions: string;
  requirements: string;
  onDimensions: (value: string) => void;
  onRequirements: (value: string) => void;
  showDimensions: boolean;
}) {
  const dims = parse(dimensions),
    rules = parse(requirements);
  function writeDimensions(key: string, value: string) {
    if (!dims) return;
    const next = { ...dims };
    if (value === "") delete next[key];
    else next[key] = Number(value);
    onDimensions(JSON.stringify(next, null, 2));
  }
  function writeRule(key: string, value: unknown) {
    if (rules)
      onRequirements(JSON.stringify({ ...rules, [key]: value }, null, 2));
  }
  return (
    <div className="preparation-tools">
      {showDimensions && (
        <fieldset disabled={!dims} className="registry-fieldset">
          <legend>제조사가 확정한 완성 치수</legend>
          <div className="form-two-columns">
            {[
              ["width_mm", "폭"],
              ["height_mm", "높이"],
              ...(kind === "stand-up-pouch"
                ? [["bottom_mm", "펼친 바닥 폭"]]
                : kind === "folding-box"
                  ? [["depth_mm", "상자 깊이"]]
                  : []),
            ].map(([key, label]) => (
              <label className="field" key={key}>
                {label} mm
                <input
                  required
                  type="number"
                  step="0.1"
                  min={1}
                  value={
                    typeof dims?.[key] === "number" ? (dims[key] as number) : ""
                  }
                  onChange={(e) => writeDimensions(key, e.target.value)}
                />
              </label>
            ))}
          </div>
          <p className="field-hint">
            승인 증빙 도면의 치수와 같아야 합니다. 다른 구조로 변경할 때는 아래
            고급 치수에서 불필요한 바닥·깊이 값을 지우세요.
          </p>
        </fieldset>
      )}
      <fieldset disabled={!rules} className="registry-fieldset">
        <legend>제조사 인쇄 요구사항</legend>
        <div className="form-two-columns">
          <label className="field">
            도련 mm
            <input
              type="number"
              min={0}
              max={50}
              step="0.1"
              value={Number(rules?.bleed_mm ?? 0)}
              onChange={(e) => writeRule("bleed_mm", Number(e.target.value))}
            />
          </label>
          <label className="field">
            최소 원본 이미지 해상도 PPI
            <input
              type="number"
              min={72}
              max={2400}
              value={Number(rules?.min_ppi ?? 150)}
              onChange={(e) => writeRule("min_ppi", Number(e.target.value))}
            />
          </label>
          <label className="field">
            색상 모드
            <select
              value={String(rules?.color_space ?? "RGB")}
              onChange={(e) => writeRule("color_space", e.target.value)}
            >
              <option value="RGB">RGB</option>
              <option value="CMYK">CMYK · ICC 출력 조건 등록 필요</option>
            </select>
          </label>
          <label className="field">
            PDF 표준
            <select
              value={String(rules?.pdf_standard ?? "PDF")}
              onChange={(e) => writeRule("pdf_standard", e.target.value)}
            >
              <option value="PDF">일반 PDF</option>
              <option value="PDF/X-1a">PDF/X-1a · 현재 미지원</option>
              <option value="PDF/X-4">PDF/X-4 · 현재 미지원</option>
            </select>
          </label>
          <label className="field">
            글꼴 처리
            <select
              value={String(rules?.font_mode ?? "embedded")}
              onChange={(e) => writeRule("font_mode", e.target.value)}
            >
              <option value="embedded">글꼴 포함(임베드)</option>
              <option value="outlined">윤곽선 변환 · ICC 출력 조건 등록 필요</option>
            </select>
          </label>
        </div>
        <p className="field-hint">
          제조사의 실제 요구사항을 기록하세요. CMYK·글꼴 윤곽선·프로필별 도련은
          ‘ICC · CMYK 출력 조건 등록’에서 ICC와 함께 등록한 인쇄 프로필을
          프로젝트에 연결해야 합니다. 일반 RGB 제작 경로는 글꼴 포함·면별
          페이지·도련 0mm이며, 기본 검토용 PDF의 3mm 도련과는 별개입니다.
          PDF/X는 지원하지 않습니다. 조건 등록이나 시험 성공은 제조사 승인을
          대신하지 않으며 별도 증빙 검토가 필요합니다.
        </p>
      </fieldset>
      {(!dims || !rules) && (
        <p className="alert alert-error">
          고급 JSON 형식을 먼저 수정하면 기본 입력을 사용할 수 있습니다.
        </p>
      )}
    </div>
  );
}
