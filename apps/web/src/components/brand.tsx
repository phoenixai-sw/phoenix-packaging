import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/" className="brand" aria-label="Phoenix Packaging 홈">
      <span className="brand-mark">
        <svg viewBox="0 0 32 32" aria-hidden="true">
          <path
            d="M6 4h10l-5 9h9l-4 7H8L6 4Zm12 0h8l-6 11h-8l6-11Zm-9 18h9l-4 8H6l3-8Z"
            fill="currentColor"
          />
        </svg>
      </span>
      <span>
        phoenix<span className="brand-sub">{compact ? "" : "PACKAGING"}</span>
      </span>
    </Link>
  );
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
      <span>© {new Date().getFullYear()} Phoenix AI · 개발 시험 서비스</span>
    </footer>
  );
}
