"use client";
import { useEffect, useState, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  LoaderCircle,
  Mail,
  ShieldCheck,
} from "lucide-react";
import { Brand } from "@/components/brand";
import { packagingMedia } from "@/lib/media";
import { api, errorMessage, Session } from "@/lib/api";
type Mode = "register" | "login" | "forgot" | "reset" | "verify";
function AuthForm() {
  const params = useSearchParams();
  const router = useRouter();
  const [mode, setMode] = useState<Mode>(() => {
    const m = params.get("mode");
    return ["login", "reset", "verify"].includes(m || "")
      ? (m as Mode)
      : "register";
  });
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [mailAvailable, setMailAvailable] = useState<boolean | null>(null);
  const destination = params.get("next");
  const next =
    destination?.startsWith("/app") && !destination.startsWith("//")
      ? destination
      : "/app";
  useEffect(() => {
    if (mode === "register" || mode === "login")
      api<Session>("/me")
        .then(() => router.replace(next))
        .catch(() => {});
  }, [mode, next, router]);
  useEffect(() => {
    api<{ email_verification_available: boolean }>("/config")
      .then((config) => setMailAvailable(config.email_verification_available))
      .catch(() => setMailAvailable(false));
  }, []);
  function switchMode(value: Mode) {
    setMode(value);
    setError("");
    setNotice("");
    setPassword("");
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (mode === "forgot") {
        const result = await api<{ message: string }>(
          "/auth/request-password-reset",
          { method: "POST", body: JSON.stringify({ email }) },
        );
        setNotice(result.message);
      } else if (mode === "verify") {
        await api("/auth/verify-email", {
          method: "POST",
          body: JSON.stringify({ token: params.get("token") }),
        });
        setNotice("이메일 확인을 완료했습니다. 작업 공간에서 계속해 주세요.");
      } else if (mode === "reset") {
        await api("/auth/reset-password", {
          method: "POST",
          body: JSON.stringify({ token: params.get("token"), password }),
        });
        setNotice("비밀번호를 변경했습니다. 새 비밀번호로 로그인해 주세요.");
        setPassword("");
      } else {
        await api<Session>(
          mode === "login" ? "/auth/login" : "/auth/register",
          {
            method: "POST",
            body: JSON.stringify({
              email,
              password,
              ...(mode === "register" ? { name } : {}),
            }),
          },
        );
        router.push(next);
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const titles = {
    register: "좋은 시작을 함께해요.",
    login: "다시 만나 반가워요.",
    forgot: "비밀번호를 잊으셨나요?",
    reset: "새 비밀번호를 정해 주세요.",
    verify: "이메일 주소를 확인해요.",
  };
  const descriptions = {
    register: "우리 브랜드를 위한 첫 번째 패키지 프로젝트.",
    login: "이어 만들고 싶은 패키지가 기다리고 있어요.",
    forgot: "가입한 이메일로 재설정 링크를 보내드립니다.",
    reset: "12자 이상의 안전한 비밀번호를 입력하세요.",
    verify: "아래 버튼을 누르면 이메일 확인이 완료됩니다.",
  };
  const basic = mode === "login" || mode === "register";
  return (
    <div className="auth-form-wrap">
      <Link href="/" className="text-link muted">
        <ArrowLeft size={16} /> 홈으로 돌아가기
      </Link>
      <div className="eyebrow">YOUR NEXT CHAPTER</div>
      <h1>{titles[mode]}</h1>
      <p className="auth-intro">{descriptions[mode]}</p>
      {basic ? (
        <div className="auth-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={mode === "register"}
            className={mode === "register" ? "active" : ""}
            onClick={() => switchMode("register")}
          >
            회원가입
          </button>
          <button
            role="tab"
            aria-selected={mode === "login"}
            className={mode === "login" ? "active" : ""}
            onClick={() => switchMode("login")}
          >
            로그인
          </button>
        </div>
      ) : (
        <div className="auth-mode-spacer" />
      )}
      <form onSubmit={submit}>
        {mode === "register" && (
          <label className="field">
            이름
            <input
              autoComplete="name"
              name="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="홍길동"
              minLength={2}
              maxLength={80}
              required
            />
          </label>
        )}
        {(basic || mode === "forgot") && (
          <label className="field">
            이메일
            <input
              type="email"
              autoComplete="email"
              name="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="hello@yourbrand.com"
              required
            />
          </label>
        )}
        {(basic || mode === "reset") && !notice && (
          <label className="field">
            {mode === "reset" ? "새 비밀번호" : "비밀번호"}
            <input
              type="password"
              autoComplete={
                mode === "login" ? "current-password" : "new-password"
              }
              name="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={
                mode === "login"
                  ? "비밀번호를 입력하세요"
                  : "12자 이상으로 입력하세요"
              }
              minLength={mode === "login" ? 1 : 12}
              maxLength={128}
              required
            />
          </label>
        )}
        {error && (
          <div className="alert alert-error" role="alert">
            {error}
          </div>
        )}
        {notice && (
          <div className="alert alert-info" role="status">
            <CheckCircle2 size={17} />
            {notice}
          </div>
        )}
        {mode === "forgot" && mailAvailable === false ? (
          <div className="alert alert-info">
            <Mail size={17} />
            <span>
              이메일 발송 서비스 연결을 준비하고 있습니다. 현재 배포에서는
              비밀번호 재설정 메일을 보낼 수 없습니다.
            </span>
          </div>
        ) : (
          !notice && (
            <button
              className="button button-dark full-width"
              disabled={busy || (mode === "forgot" && mailAvailable === null)}
            >
              {busy ? (
                <LoaderCircle className="spin" size={18} />
              ) : (
                <>
                  {
                    {
                      register: "무료로 시작하기",
                      login: "로그인",
                      forgot: "재설정 링크 받기",
                      reset: "비밀번호 변경",
                      verify: "이메일 확인 완료하기",
                    }[mode]
                  }
                  <ArrowRight size={18} />
                </>
              )}
            </button>
          )
        )}
        {notice && mode === "verify" && (
          <Link className="button button-dark full-width" href="/app">
            작업 공간으로 이동 <ArrowRight size={18} />
          </Link>
        )}
        {mode === "login" && (
          <button
            className="auth-text-button"
            type="button"
            onClick={() => switchMode("forgot")}
          >
            비밀번호를 잊으셨나요?
          </button>
        )}
        {!basic && mode !== "verify" && (
          <button
            className="auth-text-button"
            type="button"
            onClick={() => switchMode("login")}
          >
            로그인으로 돌아가기
          </button>
        )}
        {basic && (
          <p className="auth-disclaimer">
            현재 개발 시험 서비스입니다. 결제 없이 프로젝트를 만들고 편집할 수
            있습니다.
            {mailAvailable === false && (
              <>
                <br />
                이메일 확인 및 비밀번호 재설정은 연결 준비 중입니다.
              </>
            )}
          </p>
        )}
      </form>
      <div className="auth-secure">
        <ShieldCheck size={17} /> 내 작업은 내 작업 공간에 안전하게 저장됩니다.
      </div>
    </div>
  );
}
export default function Auth() {
  return (
    <main className="auth-page">
      <section className="auth-visual auth-visual-photographic">
        <img
          className="auth-background-image"
          src={packagingMedia.matcha}
          alt="차분한 그린 컬러의 말차 패키지 디자인 콘셉트"
          width={1024}
          height={1024}
          decoding="async"
        />
        <Brand />
        <div className="auth-visual-copy">
          <span className="eyebrow">SMALL BRAND. BIG POSSIBILITIES.</span>
          <h2>
            제품의 좋은 점이
            <br />
            첫눈에 전해지도록.
          </h2>
          <p>아이디어가 패키지가 되는 곳.</p>
        </div>
        <span className="auth-visual-foot">
          PHOENIX PACKAGING STUDIO · AI DESIGN CONCEPT
        </span>
      </section>
      <section className="auth-content">
        <Suspense
          fallback={
            <div className="loading-state">
              <LoaderCircle className="spin" /> 계정 화면을 준비하고 있어요.
            </div>
          }
        >
          <AuthForm />
        </Suspense>
      </section>
    </main>
  );
}
