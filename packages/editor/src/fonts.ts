import type { components } from "../../contracts/api.generated";
import type { SceneObject } from "./model";

export type FontAssetData = components["schemas"]["FontData"];
const loaded = new Map<string, FontAssetData>();
const pending = new Map<string, Promise<FontAssetData>>();
export const fontFamily = (object: Pick<SceneObject, "font_asset_id">) =>
  object.font_asset_id ? `PhoenixFont_${loaded.get(object.font_asset_id)?.sha256 ?? "unavailable"}` : "NotoSansKREditor";
export const fontData = (id?: string | null) => id ? loaded.get(id) : undefined;

export async function loadFontAsset(id: string): Promise<FontAssetData> {
  if (!/^[a-f0-9-]{36}$/i.test(id)) throw new Error("글꼴 식별자를 확인해 주세요.");
  if (pending.has(id)) return pending.get(id)!;
  const task = (async () => {
    const metadata = await fetch(`/api/v1/fonts/${id}`, { credentials: "same-origin", cache: "no-store" });
    const json = await metadata.json();
    if (!metadata.ok) throw new Error(json.message || "브랜드 글꼴을 읽을 권한이 없습니다.");
    const value = json.data as FontAssetData;
    if (value.id !== id || !/^[a-f0-9]{64}$/.test(value.sha256)) throw new Error("글꼴 원본 정보를 확인하지 못했습니다.");
    if (loaded.get(id)?.sha256 === value.sha256) return value;
    const response = await fetch(`/api/v1/fonts/${id}/content`, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error("브랜드 글꼴 원본을 불러오지 못했습니다.");
    const bytes = await response.arrayBuffer();
    const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(b => b.toString(16).padStart(2, "0")).join("");
    if (digest !== value.sha256 || bytes.byteLength !== value.byte_size) throw new Error("글꼴 원본 검사값이 달라 표시하지 않았습니다.");
    const face = new FontFace(`PhoenixFont_${digest}`, bytes, { weight: String(value.weight), style: "normal" });
    await face.load(); document.fonts.add(face); loaded.set(id, value);
    return value;
  })();
  pending.set(id, task);
  try { return await task; } finally { pending.delete(id); }
}

export async function loadObjectFonts(objects: Array<{ font_asset_id?: string | null }>) {
  await Promise.all([
    document.fonts.load('400 16px "NotoSansKREditor"'),
    document.fonts.load('700 16px "NotoSansKREditor"'),
    ...[...new Set(objects.flatMap(o => o.font_asset_id ? [o.font_asset_id] : []))].map(loadFontAsset),
  ]);
}

/** Same character advances, baseline and wrapping as the non-shaping PDF renderer. */
export function drawEditableText(ctx: CanvasRenderingContext2D, object: SceneObject, scale: number) {
  const custom = fontData(object.font_asset_id);
  if (object.font_asset_id && !custom) throw new Error("브랜드 글꼴을 불러오는 중입니다.");
  const size = (object.font_size_pt ?? 18) * 25.4 / 72 * scale;
  const spacing = (object.letter_spacing ?? 0) * 25.4 / 72 * scale;
  ctx.font = `${object.font_weight ?? 400} ${size}px "${fontFamily(object)}"`;
  ctx.fontKerning = "none"; ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  ctx.fillStyle = object.color ?? "#172c28";
  const measure = (text: string) => [...text].reduce((sum, char) => sum + ctx.measureText(char).width, 0) + Math.max(0, [...text].length - 1) * spacing;
  const lines = wrapTextCharacters(object.text ?? "", object.width_mm * scale, measure);
  lines.forEach((line, index) => {
    let x = object.align === "center" ? (object.width_mm * scale - measure(line)) / 2 : object.align === "right" ? object.width_mm * scale - measure(line) : 0;
    const y = (custom?.ascent_ratio ?? .88) * size + index * size * (object.line_height ?? 1.2);
    for (const char of line) { ctx.fillText(char, x, y); x += ctx.measureText(char).width + spacing; }
  });
}

export function wrapTextCharacters(text: string, width: number, measure: (text: string) => number) {
  const lines: string[] = [];
  for (const raw of text.replaceAll("\r\n", "\n").replaceAll("\r", "\n").split("\n")) {
    let paragraph = "", column = 0;
    for (const char of raw) {
      const value = char === "\t" ? " ".repeat(4 - column % 4) : char;
      paragraph += value; column += [...value].length;
    }
    let line = "";
    for (const char of paragraph) {
      if (line && measure(line + char) > width) { lines.push(line); line = char; }
      else line += char;
    }
    lines.push(line);
  }
  return lines;
}
