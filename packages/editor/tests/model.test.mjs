import test from "node:test";
import assert from "node:assert/strict";
import {
  safeWarnings,
  addText,
  updateObject,
  moveLayer,
  containImage,
  initialImagePlacement,
  faceSafeRegion,
  sendLayerToBack,
  applyImageBackground,
} from "../src/model.ts";
const object = (changes = {}) => ({
  id: "label",
  type: "text",
  face_id: "front",
  x_mm: 15,
  y_mm: 15,
  width_mm: 35,
  height_mm: 15,
  rotation_deg: 0,
  z_index: 2,
  text: "높은 단백질 함량",
  font_id: "NotoSansKR",
  font_size_pt: 20,
  ...changes,
});

test("contain keeps the original ratio centered inside portrait and shallow faces without cropping", () => {
  const portrait = containImage({ x_mm: 0, y_mm: 0, width_mm: 240, height_mm: 330 }, 1024, 1024);
  assert.deepEqual(portrait, { x_mm: 0, y_mm: 45, width_mm: 240, height_mm: 240, rotation_deg: 0 });
  const exact = containImage({ x_mm: 0, y_mm: 0, width_mm: 240, height_mm: 330 }, 2432, 3344);
  assert.deepEqual(exact, { x_mm: 0, y_mm: 0, width_mm: 240, height_mm: 330, rotation_deg: 0 });
  const shallow = containImage({ x_mm: 10, y_mm: 10, width_mm: 220, height_mm: 10 }, 1000, 2000);
  assert.deepEqual(shallow, { x_mm: 117.5, y_mm: 10, width_mm: 5, height_mm: 10, rotation_deg: 0 });
  assert.throws(() => containImage({ x_mm: 0, y_mm: 0, width_mm: 240, height_mm: 330 }, 0, 1000));
});

test("uploads on 30 and 40mm bottoms remain positive, proportional and inside the actual safe area", () => {
  for (const height of [30, 40]) {
    const bottom = { ...face([]), id: "bottom", width_mm: 60, height_mm: height };
    const scene = { schema_version: "1.0", active_face_id: "bottom", template_kind: "stand-up-pouch", faces: [bottom] };
    const safe = faceSafeRegion(scene, bottom);
    const placement = initialImagePlacement(safe, 1920, 1080);
    assert.ok(placement.width_mm > 0 && placement.height_mm > 0);
    assert.ok(Math.abs(placement.width_mm / placement.height_mm - 1920 / 1080) < 0.00002);
    assert.ok(placement.x_mm >= safe.x_mm && placement.y_mm >= safe.y_mm);
    assert.ok(placement.x_mm + placement.width_mm <= safe.x_mm + safe.width_mm + 0.0001);
    assert.ok(placement.y_mm + placement.height_mm <= safe.y_mm + safe.height_mm + 0.0001);
  }
});

test("starter text fits minimum bottom and box-side safe regions while keeping editable text", () => {
  for (const [kind, id, width, height, margin] of [["stand-up-pouch", "bottom", 60, 30, 10], ["folding-box", "left", 30, 80, 5]]) {
    const target = { ...face([]), id, width_mm: width, height_mm: height };
    const scene = { schema_version: "1.0", active_face_id: id, template_kind: kind, faces: [target] };
    const safe = { x_mm: margin, y_mm: margin, width_mm: width - 2 * margin, height_mm: height - 2 * margin };
    const { scene: added, id: objectId } = addText(scene, id, safe);
    const text = added.faces[0].objects.find((item) => item.id === objectId);
    assert.equal(text.text, "새로운 문구");
    assert.equal(safeWarnings(added.faces[0], safe).length, 0);
    assert.ok(text.font_size_pt >= 4 && text.font_size_pt <= 24);
    assert.ok(text.font_size_pt * 4.824 * 25.4 / 72 <= text.width_mm);
    assert.ok(text.font_size_pt * 1.2 * 25.4 / 72 <= text.height_mm);
  }
});

test("sending a layer all the way back preserves other layers' relative order and the other face", () => {
  const original = { schema_version: "1.0", active_face_id: "front", faces: [face([
    object({ id: "bottom", z_index: -10000 }), object({ id: "middle", z_index: 7 }), object({ id: "top", z_index: 10000 }),
  ]), { ...face([object({ id: "back-item", face_id: "back" })]), id: "back" }] };
  const changed = sendLayerToBack(original, "top");
  assert.deepEqual(changed.faces[0].objects.map((item) => item.id), ["top", "bottom", "middle"]);
  assert.deepEqual(changed.faces[0].objects.map((item) => item.z_index), [0, 1, 2]);
  assert.equal(changed.faces[0].objects[0].x_mm, original.faces[0].objects[2].x_mm);
  assert.strictEqual(changed.faces[1], original.faces[1]);
  assert.equal(original.faces[0].objects[2].z_index, 10000);
});

test("reapplying a contained AI background replaces it once and preserves uploaded originals and text", () => {
  const original = { schema_version: "1.0", active_face_id: "front", faces: [face([
    object(), object({ id: "reference", type: "image", asset_id: "original", z_index: 5 }),
    object({ id: "old-background", type: "image", asset_id: "old", locked: true, x_mm: 0, y_mm: 0, width_mm: 230, height_mm: 310, z_index: -1 }),
  ]), { ...face([]), id: "back" }] };
  const first = applyImageBackground(original, "front", { id: "generated-1", width_px: 1024, height_px: 1024 });
  const second = applyImageBackground(first, "front", { id: "generated-2", width_px: 1536, height_px: 1024 });
  const images = second.faces[0].objects.filter((item) => item.type === "image");
  assert.equal(images.length, 2);
  assert.equal(images[0].id, "ai-background-front");
  assert.equal(images[0].asset_id, "generated-2");
  assert.ok(Math.abs(images[0].width_mm / images[0].height_mm - 1.5) < 0.000002);
  assert.equal(images[1].asset_id, "original");
  assert.equal(second.faces[0].objects.find((item) => item.id === "label").text, original.faces[0].objects[0].text);
  assert.strictEqual(second.faces[1], original.faces[1]);
  assert.equal(first.faces[0].objects[0].asset_id, "generated-1");
});
const face = (objects) => ({
  id: "front",
  name: "앞면",
  width_mm: 230,
  height_mm: 310,
  background: "#ffffff",
  objects,
});
test("text safe area is 15mm, not the 10mm sealing boundary; rotated corners are checked", () => {
  assert.equal(safeWarnings(face([object()])).length, 0);
  assert.equal(safeWarnings(face([object({ x_mm: 12 })])).length, 1);
  assert.equal(safeWarnings(face([object({ rotation_deg: 90 })])).length, 1);
});
test("structural safe regions apply to both text and barcodes on narrow folded faces", () => {
  const safe = { x_mm: 5, y_mm: 5, width_mm: 50, height_mm: 80 };
  const narrow = { ...face([]), width_mm: 60, height_mm: 90 };
  assert.equal(
    safeWarnings({ ...narrow, objects: [object({ x_mm: 5, y_mm: 5 })] }, safe)
      .length,
    0,
  );
  assert.equal(
    safeWarnings(
      { ...narrow, objects: [object({ type: "barcode", x_mm: 4, y_mm: 5 })] },
      safe,
    ).length,
    1,
  );
  assert.equal(
    safeWarnings(
      {
        ...narrow,
        objects: [object({ type: "barcode", x_mm: 5, y_mm: 5, width_mm: 51 })],
      },
      safe,
    ).length,
    1,
  );
  assert.equal(
    safeWarnings(
      {
        ...narrow,
        objects: [
          object({
            type: "image",
            x_mm: 0,
            y_mm: 0,
            width_mm: 60,
            height_mm: 90,
          }),
        ],
      },
      safe,
    ).length,
    0,
  );
});
test("full bleed imagery allowed through 3mm but important text never bypasses safe area", () => {
  assert.equal(
    safeWarnings(
      face([
        object({
          type: "image",
          x_mm: -3,
          y_mm: -3,
          width_mm: 236,
          height_mm: 316,
        }),
      ]),
    ).length,
    0,
  );
  assert.equal(
    safeWarnings(face([object({ type: "image", x_mm: -3.01, y_mm: 0 })]))
      .length,
    1,
  );
  assert.equal(
    safeWarnings(
      face([
        object({ x_mm: -3, print_enabled: false }),
        object({ x_mm: -3, visible: false }),
      ]),
    ).length,
    0,
  );
});
test("Korean composition output, combining characters, spaces and linebreaks persist without normalization", () => {
  const text = "높은 단백질 함량\nJeju 100 g · 한글\n  두 칸  ";
  const original = {
    schema_version: "1.0",
    active_face_id: "front",
    faces: [face([object()]), { ...face([]), id: "back", name: "뒷면" }],
  };
  const changed = updateObject(original, "label", { text });
  assert.equal(changed.faces[0].objects[0].text, text);
  assert.equal(original.faces[0].objects[0].text, "높은 단백질 함량");
  assert.equal(
    JSON.parse(JSON.stringify(changed)).faces[0].objects[0].text,
    text,
  );
});
test("new text uses matching PDF line height and moving a layer preserves geometry and front/back independence", () => {
  const original = {
    schema_version: "1.0",
    active_face_id: "front",
    faces: [
      face([object(), object({ id: "background", z_index: 0, type: "shape" })]),
      {
        ...face([object({ id: "back-label", face_id: "back" })]),
        id: "back",
        name: "뒷면",
      },
    ],
  };
  const added = addText(original, "front");
  assert.equal(
    added.scene.faces[0].objects.find((o) => o.id === added.id).line_height,
    1.2,
  );
  const changed = moveLayer(original, "label", -1);
  assert.equal(
    changed.faces[0].objects.find((o) => o.id === "label").z_index,
    0,
  );
  assert.equal(changed.faces[0].objects.find((o) => o.id === "label").x_mm, 15);
  assert.deepEqual(changed.faces[1], original.faces[1]);
});
