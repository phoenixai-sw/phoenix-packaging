import assert from "node:assert/strict";
import test from "node:test";
import {
  fixedStructureExample,
  separatedStructureExample,
  parseStructureJson,
  structureInitialInputs,
  structureInputError,
  structureIssueLocation,
  structurePreviewCurrent,
  structureSelectionKey,
} from "../src/lib/registered-structures.ts";

test("layout errors resolve both indexed validator fields and collision object IDs", () => {
  const faces = [
    { id: "front", objects: [{ id: "title" }] },
    { id: "back", objects: [{ id: "ingredients" }] },
  ];
  assert.deepEqual(
    structureIssueLocation({ field: "faces.1.objects.0.width_mm" }, faces),
    { faceId: "back", objectId: "ingredients" },
  );
  assert.deepEqual(
    structureIssueLocation({ field: "faces.front.objects.title" }, faces),
    { faceId: "front", objectId: "title" },
  );
  assert.equal(structureIssueLocation({ field: "structure_ref" }, faces), null);
  assert.equal(
    structureIssueLocation({ face_id: "missing", object_id: "title" }, faces),
    null,
  );
});

test("fixed structures use registered dimensions without silently scaling existing project sizes", () => {
  const initial = structureInitialInputs(fixedStructureExample, {
    width_mm: 240,
    height_mm: 330,
  });
  assert.deepEqual(initial, { width_mm: 160, height_mm: 230 });
  assert.equal(structureInputError(fixedStructureExample, initial), null);
  assert.ok(
    structureInputError(fixedStructureExample, {
      width_mm: 240,
      height_mm: 330,
    }),
  );
  assert.ok(
    structureInputError(fixedStructureExample, { ...initial, depth_mm: 30 }),
  );
  initial.width_mm = 100;
  assert.equal(fixedStructureExample.dimensions.width_mm, 160);
});

test("range structures preserve requested dimensions and reject invalid ranges or extra axes", () => {
  const initial = structureInitialInputs(separatedStructureExample, {
    width_mm: 900,
    height_mm: 330,
    depth_mm: 50,
  });
  assert.deepEqual(initial, { width_mm: 900, height_mm: 330 });
  assert.ok(structureInputError(separatedStructureExample, initial));
  assert.equal(
    structureInputError(separatedStructureExample, {
      width_mm: 60,
      height_mm: 800,
    }),
    null,
  );
  for (const width of [NaN, Infinity, 0, 59, 601])
    assert.ok(
      structureInputError(separatedStructureExample, {
        width_mm: width,
        height_mm: 230,
      }),
    );
  assert.ok(
    structureInputError(separatedStructureExample, {
      width_mm: 160,
      height_mm: 230,
      bottom_mm: 30,
    }),
  );
});

test("changing structure, dimensions, scene or saved revision invalidates an apply preview", () => {
  const inputs = { width_mm: 160, height_mm: 230 };
  const key = structureSelectionKey("a", inputs),
    preview = { selectionKey: key, sceneKey: "original", baseRevision: 7 };
  assert.equal(structurePreviewCurrent(preview, key, "original", 7), true);
  assert.equal(structurePreviewCurrent(undefined, key, "original", 7), false);
  assert.equal(
    structurePreviewCurrent(
      preview,
      structureSelectionKey("b", inputs),
      "original",
      7,
    ),
    false,
  );
  assert.equal(
    structurePreviewCurrent(
      preview,
      structureSelectionKey("a", { ...inputs, width_mm: 200 }),
      "original",
      7,
    ),
    false,
  );
  assert.equal(structurePreviewCurrent(preview, key, "new design", 7), false);
  assert.equal(structurePreviewCurrent(preview, key, "original", 8), false);
});

test("admin JSON rejects invalid top-level recipes before server validation", () => {
  assert.equal(
    parseStructureJson(JSON.stringify(separatedStructureExample)).recipe_id,
    "three-side-seal-separated-v1",
  );
  for (const text of [
    "not json",
    "null",
    "[]",
    '{"schema_version":"3.0"}',
    JSON.stringify({ ...fixedStructureExample, recipe_id: "custom-eval" }),
  ])
    assert.throws(() => parseStructureJson(text));
});
