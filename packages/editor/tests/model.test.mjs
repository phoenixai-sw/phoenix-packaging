import test from "node:test";
import assert from "node:assert/strict";
import {
  safeWarnings,
  addText,
  updateObject,
  moveLayer,
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
