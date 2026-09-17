"use client";
import { apiRequest } from "@/lib/api-contract";
import { captureAcquisition } from "@/lib/acquisition";
import { useEffect, useRef, useState, Suspense } from "react";
import Link from "next/link";
import Script from "next/script";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, LoaderCircle, ShieldCheck } from "lucide-react";
import { Brand } from "@/components/brand";
import { packagingMedia } from "@/lib/media";
import { api, errorMessage, Session } from "@/lib/api";

type GoogleIdentity = {
  initialize: (options: {
    client_id: string;
    nonce: string;
    callback: (response: { credential: string }) => void;
    auto_select: boolean;
    ux_mode: "popup";
    itp_support: boolean;
  }) => void;
  renderButton: (
    element: HTMLElement,
    options: {
      theme: "outline";
      size: "large";
      text: "continue_with";
      shape: "rectangular";
      width: number;
      locale: "ko";
    },
  ) => void;
};
type Challenge = {
  client_id: string;
  nonce: string;
  csrf_token: string;
  expires_at: string;
};
function AuthForm() {
  const params = useSearchParams();
  const router = useRouter();
  const [challenge, setChallenge] = useState<Challenge>();
  const [scriptReady, setScriptReady] = useState(false);
  const [scriptFailed, setScriptFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [loading, setLoading] = useState(true);
  const buttonHost = useRef<HTMLDivElement>(null);
  const request = useRef<Promise<Challenge> | undefined>(undefined);
  const credentialBusy = useRef(false);
  const destination = params.get("next");
  const next =
    destination &&
    (destination === "/app" ||
      destination.startsWith("/app/") ||
      destination === "/admin" ||
      destination.startsWith("/admin/"))
      ? destination
      : "/app";
  useEffect(() => {
    apiRequest("get", "/v1/me", {})
      .then(() => router.replace(next))
      .catch(() => {});
    if (params.has("token"))
      window.history.replaceState(
        {},
        "",
        `/auth?next=${encodeURIComponent(next)}`,
      );
  }, [next, router, params]);
  useEffect(() => {
    let active = true;
    setLoading(true);
    request.current ||= api<Challenge>("/auth/google/challenge");
    request.current
      .then((value) => {
        if (active) setChallenge(value);
      })
      .catch((e) => {
        if (active) setError(errorMessage(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [retry]);
  useEffect(() => {
    if (!challenge || !scriptReady || !buttonHost.current) return;
    const google = (
      window as unknown as { google?: { accounts?: { id?: GoogleIdentity } } }
    ).google?.accounts?.id;
    if (!google) {
      setScriptFailed(true);
      setError("Google 로그인 버튼을 불러오지 못했습니다. 다시 시도해 주세요.");
      return;
    }
    let active = true;
    google.initialize({
      client_id: challenge.client_id,
      nonce: challenge.nonce,
      auto_select: false,
      ux_mode: "popup",
      itp_support: true,
      callback: (response) => {
        if (!active || credentialBusy.current) return;
        credentialBusy.current = true;
        setBusy(true);
        setError("");
        apiRequest("post", "/v1/auth/google", {
          body: {
            credential: response.credential,
            csrf_token: challenge.csrf_token,
            acquisition: captureAcquisition(),
          },
        })
          .then(() => {
            if (active) router.replace(next);
          })
          .catch((e) => {
            if (active) setError(errorMessage(e));
          })
          .finally(() => {
            credentialBusy.current = false;
            if (active) setBusy(false);
          });
      },
    });
    buttonHost.current.replaceChildren();
    google.renderButton(buttonHost.current, {
      theme: "outline",
      size: "large",
      text: "continue_with",
      shape: "rectangular",
      width: Math.min(380, Math.max(240, buttonHost.current.clientWidth)),
      locale: "ko",
    });
    return () => {
      active = false;
    };
  }, [challenge, scriptReady, next, router]);
  function restart() {
    if (scriptFailed) {
      window.location.reload();
      return;
    }
    setError("");
    setChallenge(undefined);
    request.current = undefined;
    setRetry((value) => value + 1);
  }
  return (
    <div className="auth-form-wrap">
      <Link href="/" className="text-link muted">
        <ArrowLeft size={16} /> 홈으로 돌아가기
      </Link>
      <div className="eyebrow">YOUR NEXT CHAPTER</div>
      <h1>좋은 시작을 함께해요.</h1>
      <p className="auth-intro">
        Google 계정 하나로, 우리 브랜드의 다음 패키지를.
      </p>
      <div className="google-auth-panel">
        <p>
          처음이라면 작업 공간을 만들고,
          <br />
          다시 오셨다면 저장한 디자인을 이어갑니다.
        </p>
        {challenge && (
          <Script
            src="https://accounts.google.com/gsi/client?hl=ko"
            strategy="afterInteractive"
            onReady={() => setScriptReady(true)}
            onError={() => {
              setScriptFailed(true);
              setError(
                "Google 로그인 서비스를 불러오지 못했습니다. 인터넷 연결을 확인해 주세요.",
              );
            }}
          />
        )}
        {(loading || busy || (challenge && !scriptReady && !error)) && (
          <div className="loading-state" role="status">
            <LoaderCircle className="spin" size={18} />{" "}
            {busy
              ? "Google 계정을 확인하고 있어요."
              : "로그인을 준비하고 있어요."}
          </div>
        )}
        {challenge && (
          <div
            ref={buttonHost}
            className="google-auth-button"
            aria-busy={busy}
            style={{
              pointerEvents: busy ? "none" : undefined,
              opacity: busy ? 0.6 : 1,
            }}
          />
        )}
        {error && (
          <>
            <div className="alert alert-error" role="alert">
              {error}
            </div>
            <button className="text-link" onClick={restart} disabled={busy}>
              {scriptFailed ? "페이지를 새로 불러오기" : "로그인 다시 준비하기"}
            </button>
          </>
        )}
        <p className="auth-disclaimer">
          별도의 비밀번호나 인증 메일 없이 Google에서 계정을 확인합니다. 운영
          관리 권한은 승인된 계정에만 제공됩니다.
        </p>
      </div>
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
