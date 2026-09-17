import type { Face, PlacementRegion, Scene, SceneObject } from "./model.ts";

const mm = (value: number) => Math.round(value * 10000) / 10000;
export type Bounds = {
  left: number;
  top: number;
  right: number;
  bottom: number;
  width: number;
  height: number;
};
export function objectBounds(object: SceneObject): Bounds {
  const angle = (object.rotation_deg * Math.PI) / 180,
    c = Math.cos(angle),
    s = Math.sin(angle);
  const points = [
    [0, 0],
    [object.width_mm, 0],
    [object.width_mm, object.height_mm],
    [0, object.height_mm],
  ].map(([x, y]) => [object.x_mm + x * c - y * s, object.y_mm + x * s + y * c]);
  const left = Math.min(...points.map(([x]) => x)),
    right = Math.max(...points.map(([x]) => x));
  const top = Math.min(...points.map(([, y]) => y)),
    bottom = Math.max(...points.map(([, y]) => y));
  return {
    left,
    top,
    right,
    bottom,
    width: right - left,
    height: bottom - top,
  };
}
export function unionBounds(objects: SceneObject[]): Bounds | null {
  if (!objects.length) return null;
  const boxes = objects.map(objectBounds);
  const left = Math.min(...boxes.map((b) => b.left)),
    right = Math.max(...boxes.map((b) => b.right));
  const top = Math.min(...boxes.map((b) => b.top)),
    bottom = Math.max(...boxes.map((b) => b.bottom));
  return {
    left,
    top,
    right,
    bottom,
    width: right - left,
    height: bottom - top,
  };
}
function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.entries(value)
    .filter(([, v]) => v !== undefined)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`)
    .join(",")}}`;
}
export function assertLockedObjectsUnchanged(
  previous: Scene,
  next: Scene,
  allowLockOnly = false,
) {
  const nextObjects = new Map(
    next.faces.flatMap((f) => f.objects.map((o) => [o.id, o] as const)),
  );
  for (const object of previous.faces
    .flatMap((f) => f.objects)
    .filter((o) => o.locked)) {
    const replacement = nextObjects.get(object.id);
    const comparable =
      allowLockOnly && replacement
        ? { ...replacement, locked: object.locked }
        : replacement;
    if (canonical(object) !== canonical(comparable))
      throw new Error(
        "잠긴 레이어가 포함되어 있습니다. 먼저 해당 레이어의 잠금을 해제해 주세요.",
      );
  }
}
export function setObjectsLocked(
  scene: Scene,
  ids: string[],
  locked: boolean,
): Scene {
  const chosen = new Set(ids);
  return {
    ...scene,
    faces: scene.faces.map((f) => ({
      ...f,
      objects: f.objects.map((o) => (chosen.has(o.id) ? { ...o, locked } : o)),
    })),
  };
}
export function patchObjects(
  scene: Scene,
  patches: Array<{ id: string; patch: Partial<SceneObject> }>,
): Scene {
  const byId = new Map(patches.map((item) => [item.id, item.patch]));
  return {
    ...scene,
    faces: scene.faces.map((f) => ({
      ...f,
      objects: f.objects.map((o) => {
        const patch = byId.get(o.id);
        return patch && !o.locked ? { ...o, ...patch } : o;
      }),
    })),
  };
}
export function deleteObjects(scene: Scene, ids: string[]): Scene {
  const chosen = new Set(ids);
  return {
    ...scene,
    faces: scene.faces.map((f) => ({
      ...f,
      objects: f.objects.filter((o) => !chosen.has(o.id) || o.locked),
    })),
  };
}
export function selectionInRect(face: Face, rect: PlacementRegion): string[] {
  return face.objects
    .filter((o) => o.visible !== false)
    .filter((o) => {
      const b = objectBounds(o);
      return (
        b.left >= rect.x_mm &&
        b.top >= rect.y_mm &&
        b.right <= rect.x_mm + rect.width_mm &&
        b.bottom <= rect.y_mm + rect.height_mm
      );
    })
    .map((o) => o.id);
}
export type Alignment =
  "left" | "center" | "right" | "top" | "middle" | "bottom";
export function alignObjects(
  scene: Scene,
  faceId: string,
  ids: string[],
  alignment: Alignment,
  toFace = false,
): Scene {
  const face = scene.faces.find((f) => f.id === faceId);
  if (!face) return scene;
  const objects = face.objects.filter((o) => ids.includes(o.id) && !o.locked);
  const bounds = toFace
    ? {
        left: 0,
        top: 0,
        right: face.width_mm,
        bottom: face.height_mm,
        width: face.width_mm,
        height: face.height_mm,
      }
    : unionBounds(objects);
  if (!bounds) return scene;
  return patchObjects(
    scene,
    objects.map((o) => {
      const b = objectBounds(o);
      const dx =
        alignment === "left"
          ? bounds.left - b.left
          : alignment === "right"
            ? bounds.right - b.right
            : alignment === "center"
              ? (bounds.left + bounds.right - b.left - b.right) / 2
              : 0;
      const dy =
        alignment === "top"
          ? bounds.top - b.top
          : alignment === "bottom"
            ? bounds.bottom - b.bottom
            : alignment === "middle"
              ? (bounds.top + bounds.bottom - b.top - b.bottom) / 2
              : 0;
      return {
        id: o.id,
        patch: { x_mm: mm(o.x_mm + dx), y_mm: mm(o.y_mm + dy) },
      };
    }),
  );
}
export function distributeObjects(
  scene: Scene,
  faceId: string,
  ids: string[],
  axis: "x" | "y",
): Scene {
  const objects =
    scene.faces
      .find((f) => f.id === faceId)
      ?.objects.filter((o) => ids.includes(o.id) && !o.locked) || [];
  if (objects.length < 3) return scene;
  const start = axis === "x" ? "left" : "top",
    end = axis === "x" ? "right" : "bottom",
    size = axis === "x" ? "width" : "height";
  const sorted = objects
    .map((o) => ({ o, b: objectBounds(o) }))
    .sort((a, b) => a.b[start] - b.b[start] || a.o.id.localeCompare(b.o.id));
  const span = sorted.at(-1)!.b[end] - sorted[0].b[start];
  const gap =
    (span - sorted.reduce((sum, item) => sum + item.b[size], 0)) /
    (sorted.length - 1);
  let cursor = sorted[0].b[start];
  const patches = sorted.map(({ o, b }) => {
    const delta = cursor - b[start];
    cursor += b[size] + gap;
    return {
      id: o.id,
      patch:
        axis === "x"
          ? { x_mm: mm(o.x_mm + delta) }
          : { y_mm: mm(o.y_mm + delta) },
    };
  });
  return patchObjects(scene, patches);
}
export function snapTranslation(
  face: Face,
  ids: string[],
  dx: number,
  dy: number,
  tolerance: number,
  safe?: PlacementRegion,
) {
  const bounds = unionBounds(
    face.objects.filter((o) => ids.includes(o.id) && !o.locked),
  );
  if (!bounds)
    return {
      dx,
      dy,
      guides: [] as Array<{ axis: "x" | "y"; position: number }>,
    };
  const targetsX = [0, face.width_mm / 2, face.width_mm],
    targetsY = [0, face.height_mm / 2, face.height_mm];
  if (safe) {
    targetsX.push(
      safe.x_mm,
      safe.x_mm + safe.width_mm / 2,
      safe.x_mm + safe.width_mm,
    );
    targetsY.push(
      safe.y_mm,
      safe.y_mm + safe.height_mm / 2,
      safe.y_mm + safe.height_mm,
    );
  }
  for (const o of face.objects.filter(
    (o) => !ids.includes(o.id) && o.visible !== false,
  )) {
    const b = objectBounds(o);
    targetsX.push(b.left, (b.left + b.right) / 2, b.right);
    targetsY.push(b.top, (b.top + b.bottom) / 2, b.bottom);
  }
  const choose = (anchors: number[], targets: number[], delta: number) => {
    let best = tolerance,
      correction = 0,
      target: number | undefined;
    for (const value of targets)
      for (const anchor of anchors) {
        const distance = value - anchor - delta;
        if (Math.abs(distance) < best) {
          best = Math.abs(distance);
          correction = distance;
          target = value;
        }
      }
    return { delta: mm(delta + correction), target };
  };
  const x = choose(
      [bounds.left, (bounds.left + bounds.right) / 2, bounds.right],
      targetsX,
      dx,
    ),
    y = choose(
      [bounds.top, (bounds.top + bounds.bottom) / 2, bounds.bottom],
      targetsY,
      dy,
    );
  const guides: Array<{ axis: "x" | "y"; position: number }> = [];
  if (x.target !== undefined) guides.push({ axis: "x", position: x.target });
  if (y.target !== undefined) guides.push({ axis: "y", position: y.target });
  return { dx: x.delta, dy: y.delta, guides };
}
export function validateCrop(crop: NonNullable<SceneObject["crop"]>) {
  if (
    !Object.values(crop).every(Number.isFinite) ||
    crop.x < 0 ||
    crop.y < 0 ||
    crop.width < 0.000001 ||
    crop.height < 0.000001 ||
    crop.x + crop.width > 1 + 1e-9 ||
    crop.y + crop.height > 1 + 1e-9
  )
    throw new Error("자르기 영역을 원본 이미지 안에 지정해 주세요.");
  return crop;
}
export function cropPixels(
  crop: SceneObject["crop"],
  width: number,
  height: number,
) {
  const c = validateCrop(crop || { x: 0, y: 0, width: 1, height: 1 });
  return {
    x: c.x * width,
    y: c.y * height,
    width: c.width * width,
    height: c.height * height,
  };
}
