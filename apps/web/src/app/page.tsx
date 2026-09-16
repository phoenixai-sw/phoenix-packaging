import Link from "next/link";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  Download,
  Layers3,
  PencilLine,
  Ruler,
} from "lucide-react";
import { SiteHeader, SiteFooter } from "@/components/brand";
import { BrandFilm } from "@/components/brand-film";
import { packagingMedia } from "@/lib/media";

export default function Home() {
  return (
    <>
      <SiteHeader />
      <main className="editorial-home">
        <section className="editorial-hero section-wrap">
          <div className="editorial-hero-copy">
            <div className="eyebrow">
              <span /> SMALL BRAND, BIG FIRST IMPRESSION.
            </div>
            <h1>
              좋은 제품을,
              <br />더 갖고 싶게.
            </h1>
            <p>
              당신의 정성이 담긴 제품에
              <br />
              그만큼 좋은 첫인상을.
              <br />
              <span>
                포장 디자인부터 한글 편집, 검토 파일까지
                <br className="desktop-br" /> 하나의 작업 공간에서 시작하세요.
              </span>
            </p>
            <div className="editorial-hero-actions">
              <Link className="button button-orange button-lg" href="/auth">
                내 패키지 만들기 <ArrowUpRight size={19} />
              </Link>
              <a className="text-link" href="#design-concepts">
                디자인 둘러보기 <ArrowRight size={16} />
              </a>
            </div>
            <div className="editorial-hero-note">
              <Check size={14} /> 카드 등록 없이 시작 <span>·</span> 한글 직접
              편집
            </div>
          </div>
          <figure className="editorial-hero-art">
            <img
              src={packagingMedia.hero}
              alt="크림, 포레스트 그린, 코럴 컬러의 식품 패키지를 자연광 아래 배치한 디자인 콘셉트"
              width={1536}
              height={1024}
              fetchPriority="high"
              decoding="async"
            />
            <div className="editorial-art-topline">
              <span>PHOENIX CONCEPT STUDIO</span>
              <span>COLLECTION 01</span>
            </div>
            <figcaption>
              <span>
                Every good product
                <br />
                <em>deserves a good package.</em>
              </span>
              <span>AI DESIGN CONCEPT</span>
            </figcaption>
          </figure>
          <div className="hero-bottomline">
            <span>아이디어가 패키지가 되는 곳.</span>
            <a href="#design-concepts">
              SCROLL TO EXPLORE <ArrowRight size={12} />
            </a>
          </div>
        </section>
        <section className="editorial-capabilities" aria-label="현재 제공 기능">
          <div className="section-wrap">
            <span>
              <PencilLine size={19} /> 우리 말 그대로, 한글 편집
            </span>
            <span>
              <Layers3 size={19} /> 모든 인쇄면을 한곳에서
            </span>
            <span>
              <Ruler size={19} /> 실제 mm 규격으로
            </span>
            <span>
              <Download size={19} /> 검토용 PDF로 확인
            </span>
          </div>
        </section>
        <section className="concept-section section-wrap" id="design-concepts">
          <div className="concept-heading">
            <div>
              <span className="eyebrow">A LITTLE INSPIRATION</span>
              <h2>
                담긴 것은 제품.
                <br />
                전해지는 것은 브랜드.
              </h2>
            </div>
            <p>
              자연에서 온 색, 손이 가는 질감.
              <br />
              우리 제품에 어울릴 다음 모습을 상상해 보세요.
            </p>
          </div>
          <div className="concept-grid">
            <Link className="concept-card" href="/app/projects/new">
              <div className="concept-image concept-image-matcha">
                <img
                  src={packagingMedia.matcha}
                  alt="차분한 포레스트 그린과 아이보리로 표현한 프리미엄 말차 패키지 디자인 콘셉트"
                  width={1024}
                  height={1024}
                  loading="lazy"
                  decoding="async"
                />
                <span className="concept-label">디자인 콘셉트</span>
                <span className="concept-open" aria-hidden="true">
                  <ArrowUpRight size={22} />
                </span>
              </div>
              <div className="concept-card-caption">
                <div>
                  <span>01 / TEA & WELLNESS</span>
                  <h3>일상의 속도를 낮추는, 말차.</h3>
                  <p>깊은 그린 · 섬세한 질감 · 차분한 여백</p>
                </div>
                <span className="concept-link">
                  새 프로젝트로 시작 <ArrowUpRight size={15} />
                </span>
              </div>
            </Link>
            <Link
              className="concept-card concept-card-offset"
              href="/app/projects/new"
            >
              <div className="concept-image concept-image-granola">
                <img
                  src={packagingMedia.granola}
                  alt="따뜻한 테라코타와 크림 컬러로 표현한 그래놀라 패키지 디자인 콘셉트"
                  width={1024}
                  height={1024}
                  loading="lazy"
                  decoding="async"
                />
                <span className="concept-label">디자인 콘셉트</span>
                <span className="concept-open" aria-hidden="true">
                  <ArrowUpRight size={22} />
                </span>
              </div>
              <div className="concept-card-caption">
                <div>
                  <span>02 / DAILY GOODNESS</span>
                  <h3>좋은 아침을 담은, 그래놀라.</h3>
                  <p>따뜻한 테라코타 · 자연스러운 빛 · 기분 좋은 시작</p>
                </div>
                <span className="concept-link">
                  새 프로젝트로 시작 <ArrowUpRight size={15} />
                </span>
              </div>
            </Link>
          </div>
          <p className="concept-disclosure">
            AI로 제작한 디자인 콘셉트입니다. 새 프로젝트에서 포장 규격과 문구를
            직접 설정할 수 있습니다.
          </p>
        </section>
        <BrandFilm
          enabled={packagingMedia.filmEnabled}
          source={packagingMedia.film}
          poster={packagingMedia.filmPoster}
        />
        <section id="how-it-works" className="editorial-workflow section-wrap">
          <div className="concept-heading">
            <div>
              <span className="eyebrow">FROM YOUR IDEA, TO YOUR PACKAGE</span>
              <h2>
                복잡한 과정은 덜고,
                <br />
                당신의 제품에 더 가깝게.
              </h2>
            </div>
            <Link className="text-link" href="/app/projects/new">
              첫 프로젝트 시작 <ArrowUpRight size={17} />
            </Link>
          </div>
          <div className="editorial-step-grid">
            {[
              {
                n: "01",
                title: "제품에 맞는 형태",
                text: "봉투의 형태를 고르고 실제 폭과 높이를 설정하세요.",
                icon: <Ruler size={23} />,
              },
              {
                n: "02",
                title: "우리 브랜드의 이야기",
                text: "준비한 이미지와 문구를 더하고, 한글을 직접 다듬으세요.",
                icon: <PencilLine size={23} />,
              },
              {
                n: "03",
                title: "마지막까지 꼼꼼하게",
                text: "각 인쇄면을 확인한 뒤 검토용 PDF로 함께 살펴보세요.",
                icon: <Download size={23} />,
              },
            ].map((step) => (
              <article className="editorial-step" key={step.n}>
                <div>
                  <span>{step.n}</span>
                  {step.icon}
                </div>
                <h3>{step.title}</h3>
                <p>{step.text}</p>
              </article>
            ))}
          </div>
          <div id="formats" className="supported-formats">
            <div>
              <span>현재 지원하는 포장</span>
              <strong>3면 실링 봉투</strong>
              <p>차 · 분말 · 스낵 · 가볍게 담는 제품</p>
            </div>
            <div>
              <span>함께 지원하는 포장</span>
              <strong>스탠드형 · 접이식 상자</strong>
              <p>커피 · 그래놀라 · 세워 두는 제품</p>
            </div>
            <p className="format-status-note">
              현재는 제조사 미승인 데모 구조를 제공합니다.
              <br />
              제작용 출력은 제조사 승인과 검수 통과가 필요합니다.
            </p>
          </div>
        </section>
        <section className="editorial-closing section-wrap">
          <div>
            <span className="eyebrow">LET’S MAKE IT YOURS.</span>
            <h2>
              다음에 눈길을 끄는 제품은,
              <br />
              당신의 것이길.
            </h2>
            <p>첫 번째 패키지 프로젝트를 지금 시작해 보세요.</p>
          </div>
          <Link href="/auth" className="button button-orange button-lg">
            무료로 디자인 시작 <ArrowUpRight size={19} />
          </Link>
          <span className="closing-wordmark" aria-hidden="true">
            phoenix.
          </span>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
