"use client";
import { createContext, useContext, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  ArrowUpRight,
  FolderOpen,
  LayoutGrid,
  LoaderCircle,
  LogOut,
  Plus,
  CircleHelp,
  ChevronDown,
  Palette,
  Package,
  Users,
  Wallet,
  ShieldCheck,
  Images,
} from "lucide-react";
import { Brand } from "./brand";
import { api, ApiError, errorMessage, Session } from "@/lib/api";
import { canEdit } from "@/lib/business";
const SessionContext = createContext<Session | null>(null);
export const useSession = () => useContext(SessionContext);
export function Workspace({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState("");
  const router = useRouter();
  const pathname = usePathname();
  const [retry, setRetry] = useState(0);
  const [switching, setSwitching] = useState(false);
  useEffect(() => {
    let active = true;
    setError("");
    api<Session>("/me")
      .then((s) => {
        if (active) setSession(s);
      })
      .catch((e) => {
        if (!active) return;
        if (e instanceof ApiError && e.status === 401)
          router.replace(
            `/auth?next=${encodeURIComponent(pathname + window.location.search)}`,
          );
        else setError(errorMessage(e));
      });
    return () => {
      active = false;
    };
  }, [router, retry, pathname]);
  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
      router.replace("/");
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  async function switchTeam(tenant_id: string) {
    if (tenant_id === session?.tenant.id) return;
    setSwitching(true);
    setError("");
    try {
      await api<Session>("/team/switch", {
        method: "POST",
        body: JSON.stringify({ tenant_id }),
      });
      window.location.assign("/app");
    } catch (e) {
      setError(errorMessage(e));
      setSwitching(false);
    }
  }
  if (!session)
    return (
      <div className="workspace-loading">
        <Brand />
        {error ? (
          <div className="empty-state">
            <p role="alert">{error}</p>
            <button
              className="button button-dark"
              onClick={() => setRetry((n) => n + 1)}
            >
              다시 연결
            </button>
          </div>
        ) : (
          <div className="loading-state">
            <LoaderCircle className="spin" /> 작업 공간을 열고 있어요.
          </div>
        )}
      </div>
    );
  if (pathname.includes("/editor"))
    return (
      <SessionContext.Provider value={session}>
        {children}
      </SessionContext.Provider>
    );
  return (
    <SessionContext.Provider value={session}>
      <div className="workspace-shell">
        <aside className="workspace-sidebar">
          <Brand />
          <div className="workspace-switch">
            <span className="workspace-avatar">
              {session.user.name?.slice(0, 1) || "P"}
            </span>
            <div>
              <strong>{session.tenant?.name || "내 작업 공간"}</strong>
              <small>
                {session.user.role === "owner"
                  ? "소유자"
                  : session.user.role === "editor"
                    ? "편집자"
                    : "열람자"}
              </small>
            </div>
            <ChevronDown size={14} />
          </div>
          {(session.memberships?.length || 0) > 1 && (
            <label className="workspace-team-select">
              팀 전환
              <select
                aria-label="팀 전환"
                value={session.tenant.id}
                disabled={switching}
                onChange={(e) => void switchTeam(e.target.value)}
              >
                {session.memberships?.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {canEdit(session) && (
            <Link href="/app/projects/new" className="button button-orange">
              <Plus size={17} /> 새 프로젝트
            </Link>
          )}
          <nav className="workspace-nav" aria-label="작업 공간 메뉴">
            <Link className={pathname === "/app" ? "active" : ""} href="/app">
              <LayoutGrid size={18} /> 내 프로젝트
            </Link>
            <Link
              href="/app/projects/new"
              className={pathname === "/app/projects/new" ? "active" : ""}
            >
              <FolderOpen size={18} /> 포장 선택하기
            </Link>
            {[
              { href: "/app/brands", label: "브랜드", icon: Palette },
              { href: "/app/products", label: "상품과 변형", icon: Package },
              { href: "/app/assets", label: "이미지 보관함", icon: Images },
              { href: "/app/team", label: "팀과 작업 공간", icon: Users },
              { href: "/app/billing", label: "구독과 크레딧", icon: Wallet },
            ].map((item) => (
              <Link
                href={item.href}
                key={item.href}
                className={pathname.startsWith(item.href) ? "active" : ""}
              >
                <item.icon size={18} />
                {item.label}
              </Link>
            ))}
            {session.user.is_admin && (
              <Link
                href="/admin"
                className={pathname.startsWith("/admin") ? "active" : ""}
              >
                <ShieldCheck size={18} /> 운영 관리
              </Link>
            )}
            <Link href="/pricing">
              <ArrowUpRight size={18} /> 요금 안내
            </Link>
          </nav>
          <div className="sidebar-guide">
            <CircleHelp size={21} />
            <h4>처음이신가요?</h4>
            <p>포장을 선택하고 상품 정보를 입력하면 시작할 수 있어요.</p>
            <Link href="/app/help">
              만드는 방법 살펴보기 <ArrowUpRight size={14} />
            </Link>
          </div>
          <div className="sidebar-account">
            <span className="account-avatar">
              {session.user.name?.slice(0, 1)}
            </span>
            <div>
              <strong>{session.user.name}</strong>
              <small>{session.user.email}</small>
            </div>
            <button
              className="icon-button"
              aria-label="로그아웃"
              title="로그아웃"
              onClick={logout}
            >
              <LogOut size={17} />
            </button>
          </div>
        </aside>
        <div className="workspace-main">
          <header className="workspace-topbar">
            <span>나의 디자인 스튜디오</span>
            <span className="pill">
              <span className="status-dot" /> 개발 시험 서비스
            </span>
          </header>
          {error && <div className="alert alert-error">{error}</div>}
          {children}
        </div>
      </div>
    </SessionContext.Provider>
  );
}
