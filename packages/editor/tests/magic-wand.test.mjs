import test from "node:test";
import assert from "node:assert/strict";
import { floodFill, traceOutline, simplify, fitToBudget, wandSelection } from "../src/magic-wand.ts";

/** Build RGBA pixels from an ASCII picture; each distinct character is a distinct colour. */
function picture(rows) {
  const width = rows[0].length, height = rows.length;
  const palette = { ".": [255, 255, 255], "#": [10, 20, 30], "o": [200, 40, 40], "~": [14, 24, 34] };
  const data = new Uint8ClampedArray(width * height * 4);
  rows.forEach((row, y) => [...row].forEach((cell, x) => {
    const [r, g, b] = palette[cell];
    const at = (y * width + x) * 4;
    data[at] = r; data[at + 1] = g; data[at + 2] = b; data[at + 3] = 255;
  }));
  return { data, width, height };
}

const SQUARE = [
  "........",
  ".####...",
  ".####...",
  ".####...",
  ".####...",
  "........",
];

test("flood fill takes the touching region of one colour and stops at a different one", () => {
  const { data, width, height } = picture(SQUARE);
  const mask = floodFill(data, width, height, 2, 2, 0);
  assert.equal(mask.reduce((a, b) => a + b, 0), 16);
  assert.equal(mask[2 * width + 2], 1);
  assert.equal(mask[0], 0);
});

test("tolerance decides whether a near colour joins the region", () => {
  // "~" differs from "#" by 4 per channel.
  const near = picture(["....", ".#~.", ".#~.", "...."]);
  const strict = floodFill(near.data, near.width, near.height, 1, 1, 0);
  const loose = floodFill(near.data, near.width, near.height, 1, 1, 8);
  assert.equal(strict.reduce((a, b) => a + b, 0), 2);
  assert.equal(loose.reduce((a, b) => a + b, 0), 4);
});

test("a separate blob of the same colour is not selected, because the fill is contiguous", () => {
  const split = picture(["........", ".##..##.", ".##..##.", "........"]);
  const mask = floodFill(split.data, split.width, split.height, 1, 1, 0);
  assert.equal(mask.reduce((a, b) => a + b, 0), 4);
});

test("a click outside the image, or on nothing, selects nothing", () => {
  const { data, width, height } = picture(SQUARE);
  assert.equal(floodFill(data, width, height, -1, 0, 0).reduce((a, b) => a + b, 0), 0);
  assert.equal(floodFill(data, width, height, 99, 99, 0).reduce((a, b) => a + b, 0), 0);
});

test("the outline walks the region's border once and stays on filled pixels", () => {
  const { data, width, height } = picture(SQUARE);
  const mask = floodFill(data, width, height, 2, 2, 0);
  const outline = traceOutline(mask, width, height);
  assert.equal(outline.length, 12); // a 4x4 block has 12 border pixels
  for (const [x, y] of outline) assert.equal(mask[y * width + x], 1, `${x},${y} is outside the region`);
  const seen = new Set(outline.map((p) => p.join(",")));
  assert.equal(seen.size, outline.length, "no pixel is visited twice");
  assert.deepEqual(outline[0], [1, 1], "starts at the top-left pixel of the region");
});

test("an empty mask and a single pixel do not produce an area", () => {
  assert.deepEqual(traceOutline(new Uint8Array(16), 4, 4), []);
  const dot = new Uint8Array(16); dot[5] = 1;
  assert.ok(traceOutline(dot, 4, 4).length < 3);
});

test("simplify drops points on a straight run and keeps the corners", () => {
  const line = [[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]];
  assert.deepEqual(simplify(line, 0.1), [[0, 0], [4, 0]]);
  const corner = [[0, 0], [2, 0], [4, 0], [4, 2], [4, 4]];
  assert.deepEqual(simplify(corner, 0.1), [[0, 0], [4, 0], [4, 4]]);
});

test("simplify keeps a bulge that is further out than the tolerance", () => {
  const bulge = [[0, 0], [2, 3], [4, 0]];
  assert.equal(simplify(bulge, 1).length, 3);
  assert.equal(simplify(bulge, 5).length, 2);
});

test("fitToBudget never returns more points than the budget", () => {
  const circle = Array.from({ length: 400 }, (_, i) => {
    const a = (i / 400) * Math.PI * 2;
    return [100 + 90 * Math.cos(a), 100 + 90 * Math.sin(a)];
  });
  for (const budget of [3, 10, 50, 200]) {
    const fitted = fitToBudget(circle, budget);
    assert.ok(fitted.length <= budget, `${fitted.length} > ${budget}`);
    assert.ok(fitted.length >= 3);
  }
  assert.equal(fitToBudget(circle, 500).length, 400, "a fitting outline is left alone");
});

test("a shape with no straight runs still fits the budget", () => {
  // Saw teeth: every point is a corner, so simplification alone cannot reach a tiny budget.
  const saw = Array.from({ length: 200 }, (_, i) => [i, i % 2 ? 6 : 0]);
  assert.ok(fitToBudget(saw, 8).length <= 8);
});

test("wandSelection returns normalized points inside the image", () => {
  const { data, width, height } = picture(SQUARE);
  const selection = wandSelection(data, width, height, 2, 2, { tolerance: 0 });
  assert.equal(selection.pixels, 16);
  assert.ok(selection.points.length >= 3);
  for (const [x, y] of selection.points) {
    assert.ok(x >= 0 && x <= 1, `x ${x}`);
    assert.ok(y >= 0 && y <= 1, `y ${y}`);
  }
  // The 4x4 block spans columns 1..4 of 8 and rows 1..4 of 6.
  const xs = selection.points.map((p) => p[0]), ys = selection.points.map((p) => p[1]);
  assert.ok(Math.min(...xs) >= 1 / 8 && Math.max(...xs) <= 5 / 8);
  assert.ok(Math.min(...ys) >= 1 / 6 && Math.max(...ys) <= 5 / 6);
});

test("wandSelection refuses a region too thin to enclose an area", () => {
  const dot = picture(["...", ".#.", "..."]);
  assert.equal(wandSelection(dot.data, dot.width, dot.height, 1, 1, { tolerance: 0 }), null);
});

test("a ring-shaped region is selected with its hole, because one polygon has no holes", () => {
  // The server rasterizes a single closed ring, so the pixel in the middle cannot be excluded.
  // Selecting the frame therefore covers the whole 3x3, which is what the outline traces.
  const ring = picture(["...", ".#.", "..."]);
  const selection = wandSelection(ring.data, ring.width, ring.height, 0, 0, { tolerance: 0 });
  assert.ok(selection.points.length >= 3);
  assert.equal(selection.pixels, 8, "the frame itself is 8 pixels");
  const xs = selection.points.map((p) => p[0]), ys = selection.points.map((p) => p[1]);
  assert.equal(Math.min(...xs), 1 / 6);
  assert.equal(Math.max(...xs), 5 / 6);
  assert.equal(Math.min(...ys), 1 / 6);
  assert.equal(Math.max(...ys), 5 / 6);
});

test("wandSelection honours the point budget the server shares across shapes", () => {
  const size = 120;
  const data = new Uint8ClampedArray(size * size * 4);
  for (let y = 0; y < size; y += 1) for (let x = 0; x < size; x += 1) {
    const inside = Math.hypot(x - 60, y - 60) < 50;
    const at = (y * size + x) * 4;
    data[at] = inside ? 10 : 250; data[at + 1] = inside ? 20 : 250; data[at + 2] = inside ? 30 : 250; data[at + 3] = 255;
  }
  const selection = wandSelection(data, size, size, 60, 60, { tolerance: 0, maxPoints: 40 });
  assert.ok(selection.points.length <= 40 && selection.points.length >= 3);
  assert.ok(selection.pixels > 7000, `circle area ${selection.pixels}`);
});
