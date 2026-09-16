"use client";
import { useState } from "react";
import { Mail, LoaderCircle } from "lucide-react";
import { useSession } from "./workspace";
import { useApiData } from "@/lib/business";
import { api, errorMessage } from "@/lib/api";
export function AccountVerification() {
  const session = useSession();
  const config = useApiData<{ email_verification_available: boolean }>(
    "/config",
  );
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function request() {
    setBusy(true);
    try {
      await api("/auth/request-verification", { method: "POST" });
      setMessage("인증 메일을 보냈습니다. 받은 메일의 링크를 확인해 주세요.");
    } catch (e) {
      setMessage(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  if (session?.user.email_verified !== false) return null;
  return (
    <div className="account-verification">
      <Mail size={17} />
      <div>
        <strong>이메일 인증</strong>
        <p>
          {message ||
            (config.data?.email_verification_available
              ? "실제 AI 생성과 계정 보호를 위해 이메일을 인증해 주세요."
              : "이메일 발송 서비스 연결을 준비하고 있습니다. 인증 메일은 연결 후 요청할 수 있습니다.")}
        </p>
      </div>
      {config.data?.email_verification_available && (
        <button
          className="button button-light button-sm"
          disabled={busy}
          onClick={() => void request()}
        >
          {busy ? (
            <LoaderCircle size={14} className="spin" />
          ) : (
            "인증 메일 받기"
          )}
        </button>
      )}
    </div>
  );
}
