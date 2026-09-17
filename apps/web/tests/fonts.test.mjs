import test from "node:test";
import assert from "node:assert/strict";
import { drawEditableText, loadFontAsset, wrapTextCharacters } from "../../../packages/editor/src/fonts.ts";

test("character layout preserves intentional whitespace, empty lines and exact-fit boundaries", () => {
  assert.deepEqual(wrapTextCharacters("AV A\r\n\r\n한한", 3, text => [...text].length), ["AV ", "A", "", "한한"]);
  assert.deepEqual(wrapTextCharacters("A\tV\t한", 20, text => [...text].length), ["A   V   한"]);
});

test("canvas uses separate character advances, not a kerning or ligature pair measurement", () => {
  const drawn = [];
  const context = { measureText(text) { assert.equal([...text].length, 1); return { width: 2 }; }, fillText(text,x,y) { drawn.push({text,x,y}); } };
  drawEditableText(context, { text:"AVfi",font_size_pt:10,width_mm:50,height_mm:20,font_weight:400,align:"left" }, 1);
  assert.equal(context.fontKerning,"none");
  assert.deepEqual(drawn.map(item => item.x),[0,2,4,6]);
  assert.ok(Math.abs(drawn[0].y-10*25.4/72*.88)<1e-8);
});

test("a mismatching original checksum is never registered or replaced by another font", async () => {
  const id="00000000-0000-4000-8000-000000000001";
  const originalFetch=globalThis.fetch, originalFontFace=globalThis.FontFace;
  let calls=0;
  globalThis.fetch=async () => ++calls===1 ? new Response(JSON.stringify({data:{id,sha256:"0".repeat(64),byte_size:4},request_id:"test"}),{status:200}) : new Response(new Uint8Array([1,2,3,4]));
  globalThis.FontFace=class { constructor() { throw new Error("must not register invalid original"); } };
  try { await assert.rejects(loadFontAsset(id),/검사값/); } finally { globalThis.fetch=originalFetch;globalThis.FontFace=originalFontFace; }
});
