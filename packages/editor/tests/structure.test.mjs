import test from "node:test";
import assert from "node:assert/strict";
import { hangerPreset } from "../src/structure.ts";

test("hanger preset uses the opening header center and keeps the complete hole inside its permitted region", () => {
  const regions = {
    header: { x_mm: 0, y_mm: 0, width_mm: 240, height_mm: 30 },
    hole_allowed: { x_mm: 20, y_mm: 12, width_mm: 200, height_mm: 10 },
  };
  assert.deepEqual(hangerPreset(240, regions), { center_x_mm: 120, center_y_mm: 15, diameter_mm: 6 });
  assert.deepEqual(hangerPreset(240, { hole_allowed: { x_mm: 20, y_mm: 16, width_mm: 200, height_mm: 12 } }),
    { center_x_mm: 120, center_y_mm: 22, diameter_mm: 6 });
});

test("an altered header cannot preset the hole beyond the allowed area or into a face with no room", () => {
  const regions = {
    header: { x_mm: 0, y_mm: 0, width_mm: 240, height_mm: 10 },
    hole_allowed: { x_mm: 20, y_mm: 12, width_mm: 200, height_mm: 10 },
  };
  assert.equal(hangerPreset(240, regions).center_y_mm, 15);
  assert.equal(hangerPreset(240, { hole_allowed: null }), null);
  assert.equal(hangerPreset(240, { hole_allowed: { ...regions.hole_allowed, height_mm: 5 } }), null);
});
