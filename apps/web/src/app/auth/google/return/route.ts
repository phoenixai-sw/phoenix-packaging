import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/**
 * Redirect-mode Google Sign-In endpoint. Google form-posts the ID token here after a full-page
 * sign-in (used where popups are blocked, e.g. embedded browsers). The page below finishes the
 * same challenge-bound JSON login as the popup flow from our own origin, so the challenge cookie
 * and the nonce kept in sessionStorage are both available. Nothing is stored server-side.
 */
export async function POST(request: Request) {
  const form = await request.formData();
  const credential = form.get("credential");
  const ok = typeof credential === "string" && /^[A-Za-z0-9_.\-]{20,4096}$/.test(credential);
  const payload = JSON.stringify({ credential: ok ? credential : "" }).replace(/</g, "\\u003c");
  const html = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="robots" content="noindex"><title>로그인 확인 중</title>
<style>body{font-family:NotoSansKR,Arial,sans-serif;background:#fffaf3;color:#242d26;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}main{max-width:420px;padding:32px;text-align:center}a{color:#f0733f}</style></head>
<body><main><p id="status">Google 계정을 확인하고 있어요…</p><p id="retry" hidden><a href="/auth">로그인 화면으로 돌아가기</a></p></main>
<script>
(async () => {
  const data = ${payload};
  const status = document.getElementById("status"), retry = document.getElementById("retry");
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem("phoenix-google-redirect") || "null"); } catch {}
  const fail = (message) => { status.textContent = message; retry.hidden = false; };
  if (!data.credential || !saved || typeof saved.csrf_token !== "string") return fail("로그인 요청이 만료되었습니다. 로그인 화면에서 다시 시작해 주세요.");
  try {
    const response = await fetch("/api/v1/auth/google", { method: "POST", credentials: "same-origin",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ credential: data.credential, csrf_token: saved.csrf_token, acquisition: saved.acquisition ?? null }) });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) return fail(body.message || "로그인을 완료하지 못했습니다.");
    sessionStorage.removeItem("phoenix-google-redirect");
    const next = typeof saved.next === "string" && /^\\/(app|admin)(\\/|$)/.test(saved.next) ? saved.next : "/app";
    location.replace(next);
  } catch { fail("네트워크 오류로 로그인을 완료하지 못했습니다."); }
})();
</script></body></html>`;
  return new NextResponse(html, { status: ok ? 200 : 400, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store, private" } });
}
