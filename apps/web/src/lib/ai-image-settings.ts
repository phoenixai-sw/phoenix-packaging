export type ImageModel = "gpt-image-2.5-sunburst" | "gpt-image-2.5-flare";
export type ImageQuality = "low" | "medium" | "high" | "xhigh" | "max" | "auto";
export type ImageMode = "generate" | "edit";
export type ImageSelection = {
  projectId: string;
  faceId: string;
  mode: ImageMode;
  model: ImageModel;
  quality: ImageQuality;
  prompt: string;
  count: number;
  reference: string;
};
export type ImageSettings = {
  model: string;
  quality: string;
  requested_quality?: string;
  output_size?: string;
  output_width_px?: number;
  output_height_px?: number;
  output_effective_ppi?: number | null;
  output_experimental?: boolean;
};
export const DEFAULT_IMAGE_MODEL: ImageModel = "gpt-image-2.5-sunburst";
export const DEFAULT_IMAGE_QUALITY: ImageQuality = "high";
export const IMAGE_MODELS: ImageModel[] = [
  DEFAULT_IMAGE_MODEL,
  "gpt-image-2.5-flare",
];
export const IMAGE_QUALITIES: ImageQuality[] = [
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
  "auto",
];

export function initialImageSettings(capabilities: {
  defaults?: { model?: string; quality?: string };
  models: Array<{ id: string; enabled: boolean }>;
  qualities: Array<{ id: string; enabled: boolean }>;
}): { model: ImageModel; quality: ImageQuality } {
  const availableModels = IMAGE_MODELS.filter((id) =>
    capabilities.models.some((item) => item.id === id && item.enabled),
  );
  const availableQualities = IMAGE_QUALITIES.filter((id) =>
    capabilities.qualities.some((item) => item.id === id && item.enabled),
  );
  const model =
    availableModels.find((id) => id === capabilities.defaults?.model) ??
    availableModels.find((id) => id === DEFAULT_IMAGE_MODEL) ??
    availableModels[0] ??
    DEFAULT_IMAGE_MODEL;
  const quality =
    availableQualities.find((id) => id === capabilities.defaults?.quality) ??
    availableQualities.find((id) => id === DEFAULT_IMAGE_QUALITY) ??
    availableQualities[0] ??
    DEFAULT_IMAGE_QUALITY;
  return { model, quality };
}

export function imageTier(quality: ImageQuality): "standard" | "high" {
  return quality === "xhigh" || quality === "max" || quality === "auto"
    ? "high"
    : "standard";
}

export function imageAction(mode: ImageMode, quality: ImageQuality): string {
  return `image.${mode}.${imageTier(quality)}`;
}

export function imageQuoteBody(
  selection: ImageSelection,
  baseRevision: number,
) {
  return {
    project_id: selection.projectId,
    base_revision: baseRevision,
    action: imageAction(selection.mode, selection.quality),
    model: selection.model,
    quality: selection.quality,
    requested_units: selection.mode === "edit" ? 1 : selection.count,
    prompt: selection.prompt,
    face_id: selection.faceId,
    ...(selection.mode === "edit"
      ? { reference_asset_id: selection.reference }
      : {}),
  };
}

export function imageSelectionKey(selection: ImageSelection): string {
  return JSON.stringify([
    selection.projectId,
    selection.faceId,
    selection.mode,
    selection.model,
    selection.quality,
    selection.prompt,
    selection.count,
    selection.reference,
  ]);
}

export type ImageQuoteTicket = { sequence: number; inputKey: string };

// A→B→A is still a changed request. A version, rather than only an input hash,
// also discards older responses when a new request repeats the same selection.
export function createImageQuoteGate() {
  let sequence = 0;
  let inputKey = "";
  return {
    update(selection: ImageSelection) {
      const next = imageSelectionKey(selection);
      if (next !== inputKey) {
        inputKey = next;
        sequence += 1;
      }
    },
    invalidate() {
      sequence += 1;
    },
    begin(): ImageQuoteTicket {
      return { sequence: ++sequence, inputKey };
    },
    accepts(ticket: ImageQuoteTicket): boolean {
      return ticket.sequence === sequence && ticket.inputKey === inputKey;
    },
  };
}

export function imageQuoteMatches(
  quote: {
    action: string;
    requested_units: number;
    image_settings?: ImageSettings;
  },
  selection: ImageSelection,
): boolean {
  return (
    quote.action === imageAction(selection.mode, selection.quality) &&
    quote.requested_units ===
      (selection.mode === "edit" ? 1 : selection.count) &&
    quote.image_settings?.model === selection.model &&
    quote.image_settings?.quality === selection.quality
  );
}

export function imageModelLabel(model?: string): string {
  if (model === "gpt-image-2.5-sunburst") return "Sunburst";
  if (model === "gpt-image-2.5-flare") return "Flare";
  return model || "모델 기록 없음";
}

export function imageQualityLabel(quality?: string | null): string {
  return quality === "auto"
    ? "Auto (자동)"
    : quality
      ? quality.toUpperCase()
      : "미제공";
}

export function creditBalanceLabel(balance?: number | null): string {
  return typeof balance === "number" && Number.isFinite(balance)
    ? `${balance.toLocaleString("ko-KR")} 크레딧`
    : "조회하지 못함";
}
