"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, errorMessage } from "@/lib/api";
import { Feedback } from "./management";
import type { Project } from "@editor/model";
type Check = {
  key: string;
  label: string;
  status: "pass" | "needs_input" | "blocked";
  detail: string;
};
type Registration = {
  barcode: string;
  holder_name: string;
  registration_reference: string;
  source_url?: string;
  confirmed_at: string;
  verification: string;
};
type BarcodeRegistration = {
  variant_id: string;
  name: string;
  barcode: string;
  updated_at: string;
  registration: Registration | null;
  duplicate_variants: Array<{ id: string; name: string }>;
  verification_links: { koreannet: string; verified_by_gs1: string };
};
type Preparation = {
  base_revision: number;
  barcode: { variant_id?: string; code: string; checks: Check[] };
  manufacturing: {
    template: { name?: string; status?: string };
    profile: { name?: string; status?: string };
    material?: string;
    checks: Check[];
  };
  notice: string;
};
function Checks({ items }: { items: Check[] }) {
  return (
    <div className="readiness-checks">
      {items.map((c) => (
        <div key={c.key} className={`readiness-check ${c.status}`}>
          <span className="pill">
            {c.status === "pass"
              ? "확인됨"
              : c.status === "blocked"
                ? "제작 차단"
                : "정보 필요"}
          </span>
          <div>
            <strong>{c.label}</strong>
            <p>{c.detail}</p>
          </div>
        </div>
      ))}
    </div>
  );
}
export function PrintPreparationTools({
  project,
  saveCurrent,
  readOnly,
  isAdmin,
  onBindings,
  onExports,
  onStructure,
}: {
  project: Project;
  saveCurrent: () => Promise<number>;
  readOnly: boolean;
  isAdmin: boolean;
  onBindings: () => void;
  onExports: () => void;
  onStructure: (code: string) => void;
}) {
  const [data, setData] = useState<Preparation>(),
    [registration, setRegistration] = useState<BarcodeRegistration>();
  const [code, setCode] = useState(""),
    [holder, setHolder] = useState(""),
    [reference, setReference] = useState(""),
    [url, setUrl] = useState("");
  const [prefix, setPrefix] = useState(""),
    [item, setItem] = useState(""),
    [prefixConfirmed, setPrefixConfirmed] = useState(false),
    [rights, setRights] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  async function load() {
    setBusy(true);
    setError("");
    try {
      if (!readOnly) await saveCurrent();
      const result = await api<Preparation>(
        `/projects/${project.id}/print-preparation`,
      );
      setData(result);
      if (result.barcode.variant_id) {
        const row = await api<BarcodeRegistration>(
          `/variants/${result.barcode.variant_id}/barcode-registration`,
        );
        setRegistration(row);
        setCode(row.barcode || "");
        setHolder(row.registration?.holder_name || "");
        setReference(row.registration?.registration_reference || "");
        setUrl(row.registration?.source_url || "");
        setRights(false);
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, [project.id]);
  async function compose(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<{ value: string; notice: string }>(
        "/barcodes/gtin13/compose",
        {
          method: "POST",
          body: JSON.stringify({
            company_prefix: prefix,
            item_reference: item,
            registered_prefix_confirmed: prefixConfirmed,
          }),
        },
      );
      setCode(result.value);
      setRights(false);
      setNotice(`체크 숫자를 계산했습니다: ${result.value}. ${result.notice}`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!registration) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<BarcodeRegistration>(
        `/variants/${registration.variant_id}/barcode-registration`,
        {
          method: "PUT",
          body: JSON.stringify({
            barcode: code,
            holder_name: holder,
            registration_reference: reference,
            source_url: url || null,
            confirmed_rights: rights,
            expected_updated_at: registration.updated_at,
          }),
        },
      );
      setRegistration(result);
      setData(
        await api<Preparation>(`/projects/${project.id}/print-preparation`),
      );
      setRights(false);
      setNotice(
        "상품 번호와 사용 권한 확인 기록을 저장했습니다. 디자인의 샘플 바코드는 자동 교체되지 않습니다. 아래 배치 메뉴에서 등록한 번호로 교체하세요.",
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="preparation-tools">
      <div className="alert alert-info">
        {data?.notice ||
          "인쇄 준비에 필요한 상품 번호와 제조사 자료를 확인합니다. 이 화면은 최종 제작 승인서를 대신하지 않습니다."}
      </div>
      <Feedback error={error} notice={notice} />
      <button
        className="button button-light button-sm"
        disabled={busy}
        onClick={() => void load()}
      >
        최신 저장본으로 준비 상태 새로고침
      </button>
      <div className="image-tools-columns">
        <section>
          <h3>상품 바코드 등록</h3>
          <p className="field-hint">
            GS1에서 배정받은 번호를 기록하는 도구입니다. 임의 번호를 정식 상품
            번호로 발급하거나 소유권을 자동 인증하지 않습니다.
          </p>
          {data && <Checks items={data.barcode.checks} />}
          {!registration ? (
            <button className="button button-dark" onClick={onBindings}>
              상품 변형 먼저 연결
            </button>
          ) : (
            <>
              <p>
                <strong>연결 상품 · {registration.name}</strong>
              </p>
              <div className="inline-actions">
                <a
                  className="text-link"
                  href={registration.verification_links.koreannet}
                  target="_blank"
                  rel="noreferrer"
                >
                  코리안넷 번호 등록 안내 ↗
                </a>
                <a
                  className="text-link"
                  href={registration.verification_links.verified_by_gs1}
                  target="_blank"
                  rel="noreferrer"
                >
                  Verified by GS1 외부 확인 ↗
                </a>
              </div>
              <details className="production-requirements">
                <summary>업체코드 + 상품 참조번호로 체크 숫자 계산</summary>
                <form onSubmit={compose}>
                  <div className="form-row">
                    <label className="field">
                      배정받은 업체코드
                      <input
                        inputMode="numeric"
                        pattern="[0-9]{6,11}"
                        required
                        value={prefix}
                        onChange={(e) => {
                          setPrefix(e.target.value);
                          setPrefixConfirmed(false);
                        }}
                      />
                    </label>
                    <label className="field">
                      상품 참조번호
                      <input
                        inputMode="numeric"
                        pattern="[0-9]{1,6}"
                        required
                        value={item}
                        onChange={(e) => setItem(e.target.value)}
                      />
                    </label>
                  </div>
                  <p className="field-hint">
                    두 값은 합계 12자리여야 합니다. 앞의 0도 그대로 입력하세요.
                    상품별 참조번호 배정은 발급기관의 규칙을 따르세요.
                  </p>
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={prefixConfirmed}
                      onChange={(e) => setPrefixConfirmed(e.target.checked)}
                    />{" "}
                    정식 배정받은 업체코드임을 확인했습니다.
                  </label>
                  <button
                    className="button button-light"
                    disabled={busy || readOnly || !prefixConfirmed}
                  >
                    체크 숫자 계산 · 번호 발급 아님
                  </button>
                </form>
              </details>
              <form className="stack-form" onSubmit={save}>
                <label className="field">
                  정식 EAN-13 상품 번호
                  <input
                    required
                    inputMode="numeric"
                    pattern="[0-9]{13}"
                    maxLength={13}
                    value={code}
                    onChange={(e) => {
                      setCode(e.target.value);
                      setRights(false);
                    }}
                  />
                </label>
                <label className="field">
                  번호 보유 기업명
                  <input
                    required
                    maxLength={160}
                    value={holder}
                    onChange={(e) => {
                      setHolder(e.target.value);
                      setRights(false);
                    }}
                  />
                </label>
                <label className="field">
                  번호 배정·사용 권한 근거
                  <textarea
                    required
                    minLength={3}
                    maxLength={2000}
                    rows={3}
                    value={reference}
                    onChange={(e) => {
                      setReference(e.target.value);
                      setRights(false);
                    }}
                    placeholder="등록 문서명·등록일·상품과의 연결 근거. 비밀번호나 비밀키는 입력하지 마세요."
                  />
                </label>
                <label className="field">
                  확인 자료 주소 · 선택
                  <input
                    type="url"
                    pattern="https://.*"
                    value={url}
                    onChange={(e) => {
                      setUrl(e.target.value);
                      setRights(false);
                    }}
                    placeholder="https://"
                  />
                </label>
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={rights}
                    onChange={(e) => setRights(e.target.checked)}
                  />{" "}
                  이 상품의 번호 사용 권한과 배정 근거를 확인했습니다.
                </label>
                <button
                  className="button button-dark"
                  disabled={busy || readOnly || !rights}
                >
                  중복 확인하고 등록 기록 저장
                </button>
              </form>
              {registration.registration && (
                <p className="field-hint">
                  최근 확인{" "}
                  {new Date(
                    registration.registration.confirmed_at,
                  ).toLocaleString("ko-KR")}{" "}
                  · 사용자 확인 기록(외부 인증 아님)
                </p>
              )}
              {registration.duplicate_variants.length > 0 && (
                <Feedback
                  error={`같은 번호의 상품: ${registration.duplicate_variants.map((v) => v.name).join(", ")}`}
                />
              )}
              <button
                className="button button-light"
                onClick={() => onStructure(registration.barcode)}
              >
                바코드 배치·샘플 교체 메뉴
              </button>
            </>
          )}
        </section>
        <section>
          <h3>제조 규격 준비</h3>
          {data && (
            <>
              <dl className="preparation-summary">
                <dt>도면</dt>
                <dd>{data.manufacturing.template.name || "미연결"}</dd>
                <dt>인쇄 조건</dt>
                <dd>{data.manufacturing.profile.name || "미연결"}</dd>
                <dt>소재</dt>
                <dd>{data.manufacturing.material || "미지정"}</dd>
              </dl>
              <Checks items={data.manufacturing.checks} />
            </>
          )}
          <button className="button button-dark" onClick={onExports}>
            도면·소재·인쇄 조건 선택 / 출력 검수
          </button>
          <p className="field-hint">
            제조사에 최종 치수·칼선·소재·가공 조건·도련·색상 모드·해상도·PDF
            형식·글꼴 조건을 확인하고 승인 자료를 받아야 합니다. 현재 기본
            검토용 PDF는 RGB이며 제조사별 PDF/X·CMYK 출력을 보장하지 않습니다.
          </p>
          {isAdmin ? (
            <Link className="text-link" href="/admin" target="_blank">
              운영 관리에서 제조사 도면·프로필·승인 근거 등록 ↗
            </Link>
          ) : (
            <p className="field-hint">
              확정 자료는 운영 관리자에게 전달해 도면·인쇄 프로필 등록과 승인을
              요청하세요.
            </p>
          )}
          <p className="field-hint">
            구멍·지퍼·절취 홈 등 가공은 구조 검토용입니다. 실제 제조 승인과 제작
            출력 지원이 확인되기 전에는 최종 인쇄본으로 사용하지 마세요.
          </p>
        </section>
      </div>
    </div>
  );
}
