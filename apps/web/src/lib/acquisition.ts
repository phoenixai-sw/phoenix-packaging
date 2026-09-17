export type Acquisition = {
  channel: "direct" | "organic" | "paid_search" | "paid_social" | "referral" | "email" | "other";
  utm_source?: "google" | "naver" | "kakao" | "instagram" | "facebook" | "youtube" | "newsletter" | "partner" | "direct" | "other";
  utm_medium?: "cpc" | "paid_social" | "organic" | "referral" | "email" | "direct" | "other";
  utm_campaign?: string;
};
const key = "phoenix:acquisition:v1";
const sources = new Set(["google","naver","kakao","instagram","facebook","youtube","newsletter","partner","direct","other"]);
const media = new Set(["cpc","paid_social","organic","referral","email","direct","other"]);
export function sanitizeAcquisition(search: string): Acquisition {
  const query = new URLSearchParams(search), source = query.get("utm_source")?.toLowerCase(), medium = query.get("utm_medium")?.toLowerCase(), campaign = query.get("utm_campaign");
  const result: Acquisition = {channel: medium === "cpc" ? "paid_search" : medium === "paid_social" ? "paid_social" : medium === "organic" ? "organic" : medium === "referral" ? "referral" : medium === "email" ? "email" : source || medium ? "other" : "direct"};
  if (source) result.utm_source = (sources.has(source) ? source : "other") as Acquisition["utm_source"];
  if (medium) result.utm_medium = (media.has(medium) ? medium : "other") as Acquisition["utm_medium"];
  if (campaign && /^[A-Za-z0-9_-]{1,80}$/.test(campaign)) result.utm_campaign = campaign;
  return result;
}
export function captureAcquisition(): Acquisition {
  if (typeof window === "undefined") return {channel:"direct"};
  try {
    const previous = sessionStorage.getItem(key);
    if (previous) {
      // Re-sanitize storage rather than trusting locally edited JSON or unknown fields.
      const value = JSON.parse(previous);
      const params = new URLSearchParams();
      for (const field of ["utm_source","utm_medium","utm_campaign"] as const)
        if (typeof value[field] === "string") params.set(field,value[field]);
      return sanitizeAcquisition(params.toString());
    }
    const value = sanitizeAcquisition(window.location.search);
    sessionStorage.setItem(key,JSON.stringify(value));
    return value;
  } catch { return sanitizeAcquisition(window.location.search); }
}
