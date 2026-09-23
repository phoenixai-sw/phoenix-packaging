import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
/** Phoenix AI Co., Ltd. logo, used by context:
 *  - `mark` (default): the orange phoenix bird next to the "phoenix PACKAGE DESIGN" wordmark for headers and sidebars
 *  - `lockup`: the full "Phoenix AI Co., Ltd." logo for footers, sign-in and formal screens
 *  - `mascot`: the friendly phoenix for empty states and welcome moments */
export const phoenixLogo = {
  mark: "/brand/phoenix-mark.png",
  lockup: "/brand/phoenix-ai-logo.png",
  lockupLarge: "/brand/phoenix-ai-logo-large.png",
  mascot: "/brand/phoenix-mascot.png",
} as const;
export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/" className="brand" aria-label="Phoenix Package Design 홈">
      <span className="brand-mark">
        <img src={phoenixLogo.mark} alt="" width={40} height={28} decoding="async" />
      </span>
      <span>
        phoenix<span className="brand-sub">{compact ? "" : "PACKAGE DESIGN"}</span>
      </span>
    </Link>
  );
}
export function CompanyLogo({ size = 96, className = "" }: { size?: number; className?: string }) {
  return (
    <img
      className={`company-logo ${className}`.trim()}
      src={size > 160 ? phoenixLogo.lockupLarge : phoenixLogo.lockup}
      alt="Phoenix AI Co., Ltd."
      width={size}
      height={Math.round(size * 1.2)}
      decoding="async"
    />
  );
}
export function Mascot({ size = 120, className = "" }: { size?: number; className?: string }) {
  return <img className={`phoenix-mascot ${className}`.trim()} src={phoenixLogo.mascot} alt="" width={size} height={Math.round(size * 0.97)} decoding="async" />;
}
export function SiteHeader() {
  return (
    <header className="site-header">
      <div className="header-inner">
        <Brand />
        <nav className="site-nav" aria-label="주 메뉴">
          <Link href="/#how-it-works">만드는 방법</Link>
          <Link href="/#design-concepts">디자인 예시</Link>
          <Link href="/pricing">요금 안내</Link>
          <Link href="/contact">문의</Link>
        </nav>
        <div className="header-actions">
          <Link className="login-link" href="/auth?mode=login">
            로그인
          </Link>
          <Link className="button button-dark button-sm" href="/auth">
            디자인 시작하기 <ArrowUpRight size={16} />
          </Link>
        </div>
      </div>
    </header>
  );
}
export function SiteFooter() {
  return (
    <footer className="site-footer">
      <Brand />
      <p>좋은 제품의 다음 모습, 피닉스 패키징.</p>
      <div className="site-footer-company">
        <CompanyLogo size={72} />
        <span>© {new Date().getFullYear()} Phoenix AI Co., Ltd. · 개발 시험 서비스</span>
      </div>
    </footer>
  );
}
