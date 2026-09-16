import { createHmac } from "node:crypto";
import { isIP } from "node:net";

const challengePath = "/v1/auth/google/challenge";

/** Only the Vercel runtime may attest its platform-provided client address. */
export function authRateHeaders(
  headers: Headers,
  environment: { VERCEL?: string; WORKER_SECRET?: string } = {
    VERCEL: process.env.VERCEL,
    WORKER_SECRET: process.env.WORKER_SECRET,
  },
  now = Date.now(),
): Record<string, string> | null {
  if (environment.VERCEL !== "1") return null;
  const secret = environment.WORKER_SECRET;
  // Never fall back to caller-controlled X-Forwarded-For or take its first item.
  // https://vercel.com/docs/headers/request-headers#x-vercel-forwarded-for
  const address = headers.get("x-vercel-forwarded-for")?.trim();
  if (!secret || !address || !isIP(address)) {
    throw new Error("Trusted authentication proxy identity is unavailable");
  }
  const ip = isIP(address) === 6
    ? new URL(`http://[${address}]/`).hostname.slice(1, -1)
    : address;
  const digest = (value: string) => createHmac("sha256", secret).update(value).digest("hex");
  const key = digest(`phoenix-auth-client-v1\n${ip}`);
  const timestamp = String(Math.floor(now / 1000));
  const signature = digest(`phoenix-auth-rate-v1\n${timestamp}\nGET\n${challengePath}\n${key}`);
  return {
    "x-phoenix-auth-rate-key": key,
    "x-phoenix-auth-rate-timestamp": timestamp,
    "x-phoenix-auth-rate-signature": signature,
  };
}
