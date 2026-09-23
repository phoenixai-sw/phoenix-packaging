/** Magic-wand selection: pick the colour region under a click and hand back a polygon.
 *
 *  The artwork this runs on is flat vector-style illustration, so a colour region is usually the
 *  shape a customer means. The server takes one closed ring of normalized points with a hard budget
 *  shared by every marked shape, so a traced boundary — easily tens of thousands of pixels — has to
 *  be simplified before it can be sent. Everything here is pure so it can be tested without a DOM.
 */

/** 4-connected flood fill from (x, y), matching colours within `tolerance` of the seed.
 *  `tolerance` is the largest per-channel difference accepted, 0–255. Returns 1 per selected pixel. */
export function floodFill(
  data: Uint8ClampedArray,
  width: number,
  height: number,
  x: number,
  y: number,
  tolerance: number,
): Uint8Array {
  const mask = new Uint8Array(width * height);
  if (x < 0 || y < 0 || x >= width || y >= height) return mask;
  const seed = (y * width + x) * 4;
  const [sr, sg, sb, sa] = [data[seed], data[seed + 1], data[seed + 2], data[seed + 3]];
  const limit = Math.max(0, Math.min(255, tolerance));
  const matches = (index: number) => {
    const at = index * 4;
    return Math.abs(data[at] - sr) <= limit && Math.abs(data[at + 1] - sg) <= limit
      && Math.abs(data[at + 2] - sb) <= limit && Math.abs(data[at + 3] - sa) <= limit;
  };
  // An explicit stack; recursion overflows on any region worth selecting.
  const stack = [y * width + x];
  mask[stack[0]] = 1;
  while (stack.length) {
    const index = stack.pop()!;
    const column = index % width;
    if (column > 0 && !mask[index - 1] && matches(index - 1)) { mask[index - 1] = 1; stack.push(index - 1); }
    if (column < width - 1 && !mask[index + 1] && matches(index + 1)) { mask[index + 1] = 1; stack.push(index + 1); }
    if (index >= width && !mask[index - width] && matches(index - width)) { mask[index - width] = 1; stack.push(index - width); }
    if (index < mask.length - width && !mask[index + width] && matches(index + width)) { mask[index + width] = 1; stack.push(index + width); }
  }
  return mask;
}

const NEIGHBOURS = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];

/** Moore-neighbour trace of the mask's outer boundary, clockwise from its top-left pixel.
 *  Only the outer ring is returned: the server rasterizes one closed polygon, so a hole inside the
 *  region cannot be represented and is selected along with it. */
export function traceOutline(mask: Uint8Array, width: number, height: number): number[][] {
  let start = -1;
  for (let i = 0; i < mask.length; i += 1) if (mask[i]) { start = i; break; }
  if (start < 0) return [];
  const filled = (x: number, y: number) => x >= 0 && y >= 0 && x < width && y < height && !!mask[y * width + x];
  const startPoint: [number, number] = [start % width, Math.floor(start / width)];
  const outline: number[][] = [startPoint];
  let [cx, cy] = startPoint;
  // Entered from the left, because nothing above or to the left of the start pixel is filled.
  let from = 4;
  const steps = width * height * 4;
  for (let step = 0; step < steps; step += 1) {
    let moved = false;
    for (let turn = 1; turn <= 8; turn += 1) {
      const direction = (from + turn) % 8;
      const [dx, dy] = NEIGHBOURS[direction];
      if (!filled(cx + dx, cy + dy)) continue;
      from = (direction + 4 + 1) % 8; // re-enter facing back the way we came
      cx += dx; cy += dy;
      moved = true;
      break;
    }
    if (!moved) break; // a single isolated pixel
    if (cx === startPoint[0] && cy === startPoint[1]) break;
    outline.push([cx, cy]);
  }
  return outline;
}

function distanceToSegment(p: number[], a: number[], b: number[]): number {
  const [px, py] = p, [ax, ay] = a, [bx, by] = b;
  const dx = bx - ax, dy = by - ay;
  const lengthSquared = dx * dx + dy * dy;
  if (!lengthSquared) return Math.hypot(px - ax, py - ay);
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lengthSquared));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** Ramer–Douglas–Peucker: drop points that sit within `epsilon` of the line they lie on. */
export function simplify(points: number[][], epsilon: number): number[][] {
  if (points.length < 3 || epsilon <= 0) return points.slice();
  const keep = new Uint8Array(points.length);
  keep[0] = 1;
  keep[points.length - 1] = 1;
  const ranges: [number, number][] = [[0, points.length - 1]];
  while (ranges.length) {
    const [first, last] = ranges.pop()!;
    let worst = 0, at = -1;
    for (let i = first + 1; i < last; i += 1) {
      const distance = distanceToSegment(points[i], points[first], points[last]);
      if (distance > worst) { worst = distance; at = i; }
    }
    if (at < 0 || worst <= epsilon) continue;
    keep[at] = 1;
    ranges.push([first, at], [at, last]);
  }
  return points.filter((_, i) => keep[i]);
}

/** Simplify just enough to fit `maxPoints`, keeping the outline as faithful as the budget allows. */
export function fitToBudget(points: number[][], maxPoints: number): number[][] {
  if (points.length <= maxPoints) return points.slice();
  let epsilon = 0.5;
  for (let attempt = 0; attempt < 24; attempt += 1) {
    const reduced = simplify(points, epsilon);
    if (reduced.length <= maxPoints) return reduced;
    epsilon *= 1.6;
  }
  // Nothing about the shape is simplifiable enough; keep an evenly spaced subset.
  const step = points.length / maxPoints;
  return Array.from({ length: maxPoints }, (_, i) => points[Math.floor(i * step)]);
}

export type WandSelection = { points: number[][]; pixels: number };

/** Select the colour region at (x, y) and return it as normalized 0–1 polygon points.
 *  Returns null when the region is too small or too thin to enclose an area. */
export function wandSelection(
  data: Uint8ClampedArray,
  width: number,
  height: number,
  x: number,
  y: number,
  { tolerance = 32, maxPoints = 600 }: { tolerance?: number; maxPoints?: number } = {},
): WandSelection | null {
  const mask = floodFill(data, width, height, Math.round(x), Math.round(y), tolerance);
  let pixels = 0;
  for (let i = 0; i < mask.length; i += 1) pixels += mask[i];
  if (!pixels) return null;
  const outline = traceOutline(mask, width, height);
  if (outline.length < 3) return null;
  const fitted = fitToBudget(outline, Math.max(3, maxPoints));
  if (fitted.length < 3) return null;
  // Pixel centres, so the ring sits on the region rather than half a pixel outside it.
  const points = fitted.map(([px, py]) => [
    Math.min(1, Math.max(0, (px + 0.5) / width)),
    Math.min(1, Math.max(0, (py + 0.5) / height)),
  ]);
  return { points, pixels };
}
