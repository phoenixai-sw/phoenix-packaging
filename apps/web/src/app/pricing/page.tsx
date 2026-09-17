import Link from "next/link";
import { ArrowUpRight, Check, Info } from "lucide-react";
import { SiteHeader, SiteFooter } from "@/components/brand";
import pricing from "../../../../../config/pricing.seed.json";
const formatNumber = (amount: number) => amount.toLocaleString("ko-KR");
const planCopy: Record<string, { desc: string; features: string[] }> = {
  starter: {
    desc: "하나의 브랜드를 차근차근",
    features: [
      "브랜드의 첫 패키지 작업",
      "한글 문구와 이미지 편집",
      "검토용 파일 준비",
    ],
  },
  pro: {
    desc: "함께 만드는 브랜드의 다음 단계",
    features: [
      "팀이 함께 사용하는 크레딧",
      "더 많은 제품의 디자인 작업",
      "제조사 승인 조건의 제작 출력",
    ],
  },
  partner: {
    desc: "여러 고객사의 패키지를 한곳에",
    features: [
      "고객사별 작업 공간",
      "브랜드별 프로젝트 관리",
      "반복 작업과 상품 확장",
    ],
  },
};
const plans = pricing.plans.map((plan) => ({ ...plan, ...planCopy[plan.id] }));
const sampleCredits =
  pricing.actions["image.generate.standard"] * 3 +
  pricing.actions["image.edit.standard"] +
  pricing.actions["export.production.first"];
export default function Pricing() {
  return (
    <>
      <SiteHeader />
      <main className="pricing-page section-wrap">
        <div className="pricing-intro">
          <div className="eyebrow">ROOM FOR YOUR NEXT IDEA</div>
          <h1>브랜드의 속도에 맞는 선택.</h1>
          <p>무료 체험으로 시작하세요. 유료 요금제와 충전은 준비 중입니다.</p>
        </div>
        <div className="alert alert-info pricing-notice">
          <Info size={18} />
          <span>
            아래는 유료 서비스 개시를 위한 요금안입니다. 현재 구독 결제와 추가
            충전은 열리지 않았습니다. 가입 시 체험 크레딧을 지급하며, AI 작업은
            실행 전에 사용량과 잔액을 확인합니다.
          </span>
        </div>
        <div className="pricing-grid">
          {plans.map((plan) => (
            <article
              key={plan.name}
              className={`pricing-card ${plan.name === "Pro" ? "featured" : ""}`}
            >
              <span className="eyebrow">{plan.name}</span>
              <h2>{plan.desc}</h2>
              <div className="plan-price">
                ₩{formatNumber(plan.monthly_inc_vat)}
                <small>/ 월</small>
              </div>
              <p className="price-tax">부가세 포함</p>
              <div className="plan-allowance">
                <strong>{formatNumber(plan.credits)} 크레딧</strong>
                <span>
                  {plan.seats}명{plan.seats > 1 ? " 공용" : ""}
                </span>
              </div>
              <ul>
                {plan.features.map((item) => (
                  <li key={item}>
                    <Check size={16} />
                    {item}
                  </li>
                ))}
              </ul>
              <Link
                className={`button full-width ${plan.name === "Pro" ? "button-dark" : "button-light"}`}
                href="/auth"
              >
                체험 계정 시작하기 <ArrowUpRight size={17} />
              </Link>
            </article>
          ))}
        </div>
        <section className="pricing-details">
          <div>
            <h2>작업 전에 확인하는 크레딧.</h2>
            <p>
              Low·Medium·High 품질은 생성 1장{" "}
              {pricing.actions["image.generate.standard"]}크레딧, 수정 1장{" "}
              {pricing.actions["image.edit.standard"]}크레딧입니다.
              XHigh·Max·Auto 품질은 생성 1장{" "}
              {pricing.actions["image.generate.high"]}크레딧, 수정 1장{" "}
              {pricing.actions["image.edit.high"]}크레딧입니다. Sunburst와 Flare
              모델에 따른 추가 요금은 없습니다. Auto는 선택한 모델이 품질을
              결정하며 장당 {pricing.actions["image.generate.high"]}크레딧으로
              고정됩니다. 자동 품질에 따른 비용 절감을 보장하지 않습니다.
            </p>
            <p>
              최초 제작 파일 준비 {pricing.actions["export.production.first"]}
              크레딧을 기준으로 합니다. 검토용 PDF는{" "}
              {pricing.actions["export.review"]}크레딧, 동일 조건의 제작 파일
              재출력은 {pricing.actions["export.production.repeat"]}
              크레딧입니다.
            </p>
            <p>
              예: 표준 시안 3장 + 수정 1장 + 최초 제작 파일 1건 ={" "}
              {formatNumber(sampleCredits)}크레딧입니다. 실제 AI 생성·수정은
              Google 로그인을 마친 허용 계정에서 사용할 수 있습니다. 제작용
              출력은 제조사 도면·인쇄 프로필 승인과 검수를 통과해야 하며, 현재는
              검토용 PDF를 제공합니다.
            </p>
          </div>
          <div>
            <h2>체험은 지금, 추가 충전은 준비 중.</h2>
            <p>
              추가 충전{" "}
              {pricing.topups
                .map(
                  (topup) =>
                    `${formatNumber(topup.credits)}크레딧 ${formatNumber(topup.inc_vat)}원 (${topup.expires_months}개월)`,
                )
                .join(" · ")}
              . 모두 부가세 포함인 충전 요금안이며, 결제 개시 후 구매일을
              기준으로 유효기간을 적용합니다.
            </p>
            <p>
              가입하면 체험 크레딧 {pricing.trial.credits}개를 지급하며,
              유효기간은 {pricing.trial.expires_days}일입니다. 체험 크레딧은
              표준 이미지 생성·수정에 사용할 수 있습니다.
              {!pricing.trial.auto_conversion &&
                " 자동 결제로 전환되지 않습니다."}
            </p>
            <p>
              유료 구독 개시 후 월 지급 크레딧은 다음 결제 주기에 만료되며
              이월되지 않습니다.
            </p>
          </div>
        </section>
        <div className="pricing-bottom">
          <h3>지금은, 첫 번째 디자인에 집중해 보세요.</h3>
          <p>
            계정을 만들고 실제 프로젝트 저장, 한글 편집, 검토용 PDF까지
            경험하세요.
          </p>
          <Link className="button button-orange" href="/auth">
            무료로 시작 <ArrowUpRight size={18} />
          </Link>
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
