import test from "node:test";
import assert from "node:assert/strict";
import {
  validateImageRegion,
  regionFromPoints,
  regionPlacement,
  assertImageSnapshot,
  applyImageText,
  imageRegionPixels,
  textRemovalInputKey,
} from "../src/image-tools.ts";
test("OCR includes fractional boundary pixels just like the server edit region", () => {
  assert.deepEqual(
    imageRegionPixels({ x: 0.1, y: 0.2, width: 0.3, height: 0.4 }, 13, 17),
    { left: 1, top: 3, width: 5, height: 8 },
  );
  assert.deepEqual(
    imageRegionPixels(
      { x: 0.99, y: 0.99, width: 0.01, height: 0.01 },
      100,
      100,
    ),
    { left: 99, top: 99, width: 1, height: 1 },
  );
});
const source = {
  id: "source",
  type: "image",
  face_id: "front",
  asset_id: "original",
  x_mm: 10,
  y_mm: 20,
  width_mm: 100,
  height_mm: 200,
  rotation_deg: 90,
  z_index: 2,
  visible: true,
  print_enabled: true,
};
const scene = {
  schema_version: "1.0",
  active_face_id: "front",
  faces: [
    {
      id: "front",
      width_mm: 200,
      height_mm: 300,
      background: "#ffffff",
      name: "앞면",
      objects: [source],
    },
  ],
};
const region = { x: 0.1, y: 0.2, width: 0.3, height: 0.4 };
test("image regions clamp reverse drags and reject invalid numeric/outside bounds", () => {
  assert.deepEqual(regionFromPoints({ x: 1.2, y: 0.8 }, { x: 0.2, y: -0.2 }), {
    x: 0.2,
    y: 0,
    width: 0.8,
    height: 0.8,
  });
  for (const invalid of [
    { ...region, width: 0 },
    { ...region, x: -0.1 },
    { ...region, height: 1 },
    { ...region, x: NaN },
  ])
    assert.throws(() => validateImageRegion(invalid));
});
test("source pixel region maps through clockwise rotation to shared scene mm", () => {
  const p = regionPlacement(source, region);
  assert.ok(Math.abs(p.x_mm + 30) < 1e-8);
  assert.ok(Math.abs(p.y_mm - 30) < 1e-8);
  assert.equal(p.width_mm, 30);
  assert.equal(p.height_mm, 80);
  assert.equal(p.rotation_deg, 90);
});
test("OCR source ROI maps into cropped and rotated image without moving the source", () => {
  const cropped = { ...source, crop: { x: 0.2, y: 0.1, width: 0.5, height: 0.8 } };
  const p = regionPlacement(cropped, { x: 0.3, y: 0.3, width: 0.2, height: 0.2 });
  assert.ok(Math.abs(p.x_mm + 40) < 1e-8);
  assert.ok(Math.abs(p.y_mm - 40) < 1e-8);
  assert.equal(p.width_mm, 40);
  assert.equal(p.height_mm, 50);
  assert.throws(() => regionPlacement(cropped, region), /잘라서/);
});
test("image text replacement refuses a locked source", () => {
  const locked = { ...scene, faces: [{ ...scene.faces[0], objects: [{ ...source, locked: true }] }] };
  assert.throws(() => applyImageText(locked, "source", region,
    { text: "수정", font_size_pt: 18, font_weight: 400, color: "#000000" },
    { assetId: "new" }, { text: "text", cover: "cover" }), /잠긴/);
});
test("cleanup and replacement text are one immutable edit; original source and undo snapshot remain intact", () => {
  const before = JSON.stringify(scene);
  const next = applyImageText(
    scene,
    "source",
    region,
    {
      text: "오리고기 100%\n37.5 g × 4",
      font_size_pt: 18,
      font_weight: 700,
      color: "#123456",
    },
    { assetId: "derived" },
    { text: "replacement", cover: "cover" },
  );
  assert.equal(JSON.stringify(scene), before);
  assert.equal(next.faces[0].objects[0].asset_id, "derived");
  assert.equal(next.faces[0].objects[1].text, "오리고기 100%\n37.5 g × 4");
  assert.equal(next.faces[0].objects[1].font_weight, 700);
  assert.equal(next.faces[0].objects[1].rotation_deg, 90);
});
test("free covering keeps original asset and adds explicit shape; empty text means removal only", () => {
  const next = applyImageText(
    scene,
    "source",
    region,
    { text: "", font_size_pt: 18, font_weight: 400, color: "#000000" },
    { coverColor: "#ffffff" },
    { text: "replacement", cover: "cover" },
  );
  assert.equal(next.faces[0].objects.length, 2);
  assert.equal(next.faces[0].objects[0].asset_id, "original");
  assert.equal(next.faces[0].objects[1].type, "shape");
  assert.equal(next.faces[0].objects[1].fill, "#ffffff");
});
test("stale geometry, asset replacement, or revision stops applying generated output", () => {
  assert.doesNotThrow(() =>
    assertImageSnapshot(scene, JSON.stringify(scene), 8, 8),
  );
  assert.throws(() => assertImageSnapshot(scene, JSON.stringify(scene), 9, 8));
  assert.throws(() =>
    assertImageSnapshot(
      { ...scene, active_face_id: "back" },
      JSON.stringify(scene),
      8,
      8,
    ),
  );
});
test("invalid font input cannot leave an unsavable partial replacement in the scene", () => {
  const before = JSON.stringify(scene);
  assert.throws(() =>
    applyImageText(
      scene,
      "source",
      region,
      { text: "교체", font_size_pt: 0, font_weight: 400, color: "#000000" },
      { assetId: "derived" },
      { text: "replacement", cover: "cover" },
    ),
  );
  assert.equal(JSON.stringify(scene), before);
});
test("a completed paid cleanup remains reusable after replacement wording or style changes", () => {
  const originalInput = {
    assetId: "original",
    region,
    sourceText: "37.59",
    text: "37.59",
    fontSize: 18,
    weight: 400,
    color: "#000000",
  };
  const revisedInput = {
    ...originalInput,
    text: "37.5g × 4개입",
    fontSize: 22,
    weight: 700,
    color: "#f86848",
  };
  assert.equal(
    textRemovalInputKey(originalInput),
    textRemovalInputKey(revisedInput),
  );
  const next = applyImageText(
    scene,
    "source",
    region,
    {
      text: revisedInput.text,
      font_size_pt: revisedInput.fontSize,
      font_weight: revisedInput.weight,
      color: revisedInput.color,
    },
    { assetId: "already-paid-cleanup" },
    { text: "replacement", cover: "cover" },
  );
  assert.equal(next.faces[0].objects[0].asset_id, "already-paid-cleanup");
  assert.equal(next.faces[0].objects[1].text, revisedInput.text);
  assert.equal(next.faces[0].objects[1].font_size_pt, 22);
  assert.equal(next.faces[0].objects[1].font_weight, 700);
  assert.equal(next.faces[0].objects[1].color, "#f86848");
  for (const changed of [
    { ...originalInput, sourceText: "다른 원문" },
    { ...originalInput, assetId: "different-source" },
    { ...originalInput, region: { ...region, width: 0.4 } },
  ])
    assert.notEqual(
      textRemovalInputKey(originalInput),
      textRemovalInputKey(changed),
    );
});
import { detectedLineRegion, estimateFontSizePt, sampleTextColors } from "../src/image-tools.ts";
test("detected OCR lines become padded regions clamped to the crop", () => {
  const region = detectedLineRegion({ x0: 100, y0: 200, x1: 500, y1: 240 }, 1000, 1000);
  assert.deepEqual(region, { x: 0.09, y: 0.19, width: 0.42, height: 0.06 });
  const clamped = detectedLineRegion({ x0: 0, y0: 0, x1: 300, y1: 40 }, 1000, 1000, { x: 0.05, y: 0.02, width: 0.5, height: 0.5 });
  assert.equal(clamped.x, 0.05);
  assert.equal(clamped.y, 0.02);
  assert.throws(() => detectedLineRegion({ x0: 0, y0: 0, x1: 1, y1: 1 }, 0, 10));
});
test("font size estimate refills the line box and stays within schema limits", () => {
  assert.equal(estimateFontSizePt(6), 12.5);
  assert.equal(estimateFontSizePt(0.1), 4);
  assert.equal(estimateFontSizePt(500), 400);
});
test("colour sampling picks dark letters on a light cover and inverts for light text", () => {
  const light = [];
  for (let i = 0; i < 90; i++) light.push(250, 240, 220, 255);
  for (let i = 0; i < 10; i++) light.push(20, 40, 60, 255);
  assert.deepEqual(sampleTextColors(light), { text: "#14283c", cover: "#faf0dc" });
  const dark = [];
  for (let i = 0; i < 90; i++) dark.push(10, 20, 30, 255);
  for (let i = 0; i < 10; i++) dark.push(240, 240, 240, 255);
  const sampled = sampleTextColors(dark);
  assert.equal(sampled.cover, "#0a141e");
  assert.equal(sampled.text, "#f0f0f0");
  assert.deepEqual(sampleTextColors([0, 0, 0, 0]), { text: "#172d26", cover: "#fff3de" });
});
test("replacing text keeps locked layers untouched and stacks cover/text on top", () => {
  const scene = {
    faces: [{ id: "front", width_mm: 100, height_mm: 100, background: "#fff", objects: [
      { id: "bg", type: "image", face_id: "front", asset_id: "a", x_mm: 0, y_mm: 0, width_mm: 100, height_mm: 100, rotation_deg: 0, z_index: 3, locked: true, visible: true, print_enabled: true, opacity: 1 },
      { id: "img", type: "image", face_id: "front", asset_id: "b", x_mm: 10, y_mm: 10, width_mm: 50, height_mm: 50, rotation_deg: 0, z_index: 7, visible: true, print_enabled: true, opacity: 1 },
    ] }],
  };
  const next = applyImageText(scene, "img", { x: 0.1, y: 0.1, width: 0.5, height: 0.2 },
    { text: "새 문구", font_size_pt: 12, font_weight: 700, color: "#112233" }, { coverColor: "#ffffff" }, { text: "t", cover: "c" });
  const objects = next.faces[0].objects;
  assert.deepEqual(objects.find((o) => o.id === "bg"), scene.faces[0].objects[0]);
  assert.equal(objects.find((o) => o.id === "img").z_index, 7);
  assert.equal(objects.find((o) => o.id === "c").z_index, 8);
  assert.equal(objects.find((o) => o.id === "t").z_index, 9);
});
