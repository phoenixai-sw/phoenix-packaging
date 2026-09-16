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
          <p>월 구독으로 시작하고, 필요한 만큼 크레딧을 더하세요.</p>
        </div>
        <div className="alert alert-info pricing-notice">
          <Info size={18} />
          <span>
            아래는 초기 운영 정책 기준의 요금입니다. 결제 가능 여부와 시험
            모드는 계정의 구독 화면에서 확인하세요. AI 작업은 실행 전에 크레딧
            견적을 확인합니다.
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
                계정에서 요금제 확인 <ArrowUpRight size={17} />
              </Link>
            </article>
          ))}
        </div>
        <section className="pricing-details">
          <div>
            <h2>크레딧, 이렇게 사용할 예정이에요.</h2>
            <p>
              표준 시안 생성 1장 {pricing.actions["image.generate.standard"]}
              크레딧, 수정 1장 {pricing.actions["image.edit.standard"]}크레딧,
              최초 제작 파일 준비 {pricing.actions["export.production.first"]}
              크레딧을 기준으로 합니다. 검토용 PDF는{" "}
              {pricing.actions["export.review"]}크레딧, 같은 항목 재출력은{" "}
              {pricing.actions["export.production.repeat"]}크레딧입니다.
            </p>
            <p>
              예: 표준 시안 3장 + 수정 1장 + 최초 제작 파일 1건 ={" "}
              {formatNumber(sampleCredits)}크레딧. 실제 생성·제작용 출력은 외부
              연동과 제조사 승인 후 제공됩니다.
            </p>
          </div>
          <div>
            <h2>필요할 때 더하고, 투명하게 확인.</h2>
            <p>
              추가 충전{" "}
              {pricing.topups
                .map(
                  (topup) =>
                    `${formatNumber(topup.credits)}크레딧 ${formatNumber(topup.inc_vat)}원 (${topup.expires_months}개월)`,
                )
                .join(" · ")}
              . 모두 부가세 포함이며 유효기간은 구매일을 기준으로 합니다.
            </p>
            <p>
              월 지급 크레딧은 다음 결제 주기에 만료되며 이월되지 않습니다. 출시
              예정 체험은 {pricing.trial.credits}크레딧·
              {pricing.trial.expires_days}일입니다.
              {!pricing.trial.auto_conversion &&
                " 자동 결제로 전환되지 않습니다."}
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
