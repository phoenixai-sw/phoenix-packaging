import test from "node:test";
import assert from "node:assert/strict";
import { compareTextScenes, textRevisionHistory } from "../src/text-history.ts";

const text = (id, value, patch = {}) => ({ id, type: "text", face_id: "front", text: value,
  x_mm: 15, y_mm: 50, width_mm: 100, height_mm: 20, rotation_deg: 0, z_index: 1,
  font_size_pt: 18, font_weight: 400, color: "#ffffff", ...patch });
const scene = (front, back = []) => ({ schema_version: "1.0", active_face_id: "front", faces: [
  { id: "front", name: "앞면", width_mm: 240, height_mm: 330, background: "#ffffff", objects: front },
  { id: "back", name: "뒷면", width_mm: 240, height_mm: 330, background: "#ffffff", objects: back },
] });

test("history preserves exact before/after Korean text and limits the comparison to changed faces", () => {
  const before = scene([text("weight", "300g\n 37.5g × 4개입")], [text("ingredients", "소고기 100%, 글리세린, 소금", { face_id: "back" })]);
  const after = scene([text("weight", "37.5g × 4개입")], before.faces[1].objects);
  const changes = compareTextScenes(before, after);
  assert.equal(changes.length, 1);
  assert.equal(changes[0].face_id, "front");
  assert.equal(changes[0].before, "300g\n 37.5g × 4개입");
  assert.equal(changes[0].after, "37.5g × 4개입");
  assert.equal(before.faces[0].objects[0].text, "300g\n 37.5g × 4개입");
});

test("adding and removing layers are distinct from an empty editable text string", () => {
  const changes = compareTextScenes(scene([text("old", "삭제")]), scene([text("new", "")]));
  assert.equal(changes[0].kind, "removed");
  assert.equal(changes[0].after, undefined);
  assert.equal(changes[1].kind, "added");
  assert.equal(changes[1].after, "");
});

test("bold and position-only edits are recorded while legacy omitted regular weight is equivalent", () => {
  const legacy = scene([text("title", "오리스틱", { font_weight: undefined })]);
  assert.deepEqual(compareTextScenes(legacy, scene([text("title", "오리스틱")])), []);
  const changes = compareTextScenes(legacy, scene([text("title", "오리스틱", { font_weight: 700, y_mm: 60 })]));
  assert.equal(changes[0].before, changes[0].after);
  assert.deepEqual(changes[0].properties, ["글자 굵기", "Y 위치"]);
});

test("record order is newest first, missing snapshots are marked and non-text saves are not invented as text edits", () => {
  const base = scene([text("title", "기존")]);
  const changed = scene([text("title", "새 이름")]);
  const latest = scene([text("title", "새 이름"), { id: "image", type: "image", asset_id: "new-artwork" }]);
  const revisions = [
    { id: "6", number: 6, created_at: "2026-09-17T02:00:00Z", scene: latest },
    { id: "1", number: 1, created_at: "2026-09-17T00:00:00Z", scene: base },
    { id: "5", number: 5, created_at: "2026-09-17T01:00:00Z", scene: changed },
  ];
  const entries = textRevisionHistory(revisions);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].revision.number, 5);
  assert.equal(entries[0].previous_number, 1);
  assert.equal(entries[0].has_gap, true);
  assert.deepEqual(revisions.map((revision) => revision.number), [6, 1, 5]);
});
