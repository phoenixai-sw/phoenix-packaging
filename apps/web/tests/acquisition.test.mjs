import test from "node:test";
import assert from "node:assert/strict";
import { sanitizeAcquisition, captureAcquisition } from "../src/lib/acquisition.ts";
test("UTM values are bounded allowlist groups and never retain arbitrary URL/email/text",()=>{
  assert.deepEqual(sanitizeAcquisition('?utm_source=google&utm_medium=cpc&utm_campaign=spring_42&email=private@example.com'),{channel:'paid_search',utm_source:'google',utm_medium:'cpc',utm_campaign:'spring_42'});
  assert.deepEqual(sanitizeAcquisition('?utm_source=private@example.com&utm_medium=privateText&utm_campaign=person%40example.com&prompt=secret'),{channel:'other',utm_source:'other',utm_medium:'other'});
  assert.equal(sanitizeAcquisition('?utm_campaign='+('a'.repeat(81))).utm_campaign,undefined);
});
test("first browser-session attribution survives navigation and storage is revalidated",()=>{
  const previousWindow=globalThis.window,previousStorage=globalThis.sessionStorage;const entries=new Map();
  globalThis.sessionStorage={getItem:key=>entries.get(key),setItem:(key,value)=>entries.set(key,value)};
  globalThis.window={location:{search:'?utm_source=naver&utm_medium=organic&utm_campaign=sample'}};
  try{
    assert.equal(captureAcquisition().channel,'organic');window.location.search='?utm_source=google&utm_medium=cpc';assert.equal(captureAcquisition().utm_source,'naver');
    sessionStorage.setItem('phoenix:acquisition:v1',JSON.stringify({utm_source:'private@example.com',utm_medium:'cpc',email:'secret'}));
    assert.deepEqual(captureAcquisition(),{channel:'paid_search',utm_source:'other',utm_medium:'cpc'});
  }finally{globalThis.window=previousWindow;globalThis.sessionStorage=previousStorage;}
});
