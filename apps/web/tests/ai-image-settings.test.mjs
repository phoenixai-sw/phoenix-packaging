import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_IMAGE_MODEL,
  DEFAULT_IMAGE_QUALITY,
  IMAGE_MODELS,
  IMAGE_QUALITIES,
  createImageQuoteGate,
  creditBalanceLabel,
  imageQuoteBody,
  imageQuoteMatches,
  imageQualityLabel,
  initialImageSettings,
  imageTier,
} from "../src/lib/ai-image-settings.ts";

test("initial selection honors enabled server defaults and falls back after model withdrawal", () => {
  const capabilities = {
    defaults: { model: "gpt-image-2.5-flare", quality: "high" },
    models: IMAGE_MODELS.map((id) => ({ id, enabled: true })),
    qualities: IMAGE_QUALITIES.map((id) => ({ id, enabled: true })),
  };
  assert.deepEqual(initialImageSettings(capabilities), capabilities.defaults);
  const withdrawn = {
    ...capabilities,
    defaults: { model: DEFAULT_IMAGE_MODEL, quality: "max" },
    models: capabilities.models.map((item) => ({
      ...item,
      enabled: item.id.endsWith("flare"),
    })),
    qualities: capabilities.qualities.map((item) => ({
      ...item,
      enabled: item.id === "high",
    })),
  };
  assert.deepEqual(initialImageSettings(withdrawn), {
    model: "gpt-image-2.5-flare",
    quality: "high",
  });
});

const selection = {
  projectId: "project-a",
  faceId: "front",
  mode: "generate",
  model: DEFAULT_IMAGE_MODEL,
  quality: DEFAULT_IMAGE_QUALITY,
  prompt: "차분한 녹색 배경",
  count: 3,
  reference: "asset-a",
};

test("both models use six qualities with the agreed standard and premium actions", () => {
  assert.equal(DEFAULT_IMAGE_MODEL, "gpt-image-2.5-sunburst");
  assert.equal(DEFAULT_IMAGE_QUALITY, "high");
  for (const model of IMAGE_MODELS)
    for (const quality of IMAGE_QUALITIES) {
      const tier = ["xhigh", "max", "auto"].includes(quality)
        ? "high"
        : "standard";
      assert.equal(imageTier(quality), tier);
      for (const mode of ["generate", "edit"]) {
        const body = imageQuoteBody({ ...selection, model, quality, mode }, 17);
        assert.equal(body.action, `image.${mode}.${tier}`);
        assert.equal(body.model, model);
        assert.equal(body.quality, quality);
        assert.equal(body.base_revision, 17);
        assert.equal(body.requested_units, mode === "edit" ? 1 : 3);
        assert.equal(
          body.reference_asset_id,
          mode === "edit" ? "asset-a" : undefined,
        );
        assert.equal(
          body.input_data,
          undefined,
          "model and quality must be top-level",
        );
      }
    }
});

test("every editable setting and project/face invalidates an in-flight quote", () => {
  const changes = {
    projectId: "project-b",
    faceId: "back",
    mode: "edit",
    model: "gpt-image-2.5-flare",
    quality: "max",
    prompt: "밝은 오렌지 배경",
    count: 1,
    reference: "asset-b",
  };
  for (const [field, value] of Object.entries(changes)) {
    const gate = createImageQuoteGate();
    gate.update(selection);
    const ticket = gate.begin();
    assert.equal(gate.accepts(ticket), true);
    gate.update({ ...selection, [field]: value });
    assert.equal(gate.accepts(ticket), false, field);
    gate.update(selection);
    assert.equal(gate.accepts(ticket), false, `${field} changed and reverted`);
  }
});

test("newer identical requests, removed references, and unmount discard late responses", () => {
  const gate = createImageQuoteGate();
  gate.update(selection);
  const first = gate.begin();
  gate.update({ ...selection });
  assert.equal(gate.accepts(first), true, "ordinary render is stable");
  const second = gate.begin();
  assert.equal(gate.accepts(first), false);
  assert.equal(gate.accepts(second), true);
  gate.invalidate();
  assert.equal(gate.accepts(second), false);
});

test("execution requires the quoted action, model, quality and units to match", () => {
  const quote = {
    action: "image.generate.standard",
    requested_units: 3,
    image_settings: { model: DEFAULT_IMAGE_MODEL, quality: "high" },
  };
  assert.equal(imageQuoteMatches(quote, selection), true);
  assert.equal(
    imageQuoteMatches({ ...quote, action: "image.generate.high" }, selection),
    false,
  );
  assert.equal(
    imageQuoteMatches({ ...quote, requested_units: 1 }, selection),
    false,
  );
  assert.equal(
    imageQuoteMatches({ ...quote, image_settings: undefined }, selection),
    false,
  );
  assert.equal(
    imageQuoteMatches(quote, { ...selection, model: "gpt-image-2.5-flare" }),
    false,
  );
  assert.equal(
    imageQuoteMatches(quote, { ...selection, quality: "auto" }),
    false,
  );
});

test("zero balance is a real balance; missing data and automatic quality stay distinct", () => {
  assert.equal(creditBalanceLabel(0), "0 크레딧");
  assert.equal(creditBalanceLabel(undefined), "조회하지 못함");
  assert.equal(creditBalanceLabel(null), "조회하지 못함");
  assert.equal(imageQualityLabel("auto"), "Auto (자동)");
  assert.equal(imageQualityLabel(null), "미제공");
  assert.equal(imageTier("auto"), "high");
});
