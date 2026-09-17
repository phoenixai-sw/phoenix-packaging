import test from "node:test";
import assert from "node:assert/strict";
import {alignObjects,distributeObjects,objectBounds,assertLockedObjectsUnchanged,patchObjects,setObjectsLocked,deleteObjects,snapTranslation,selectionInRect,cropPixels} from "../src/layout.ts";
const obj=(id,x,width=10,extra={})=>({id,type:"shape",face_id:"front",x_mm:x,y_mm:10,width_mm:width,height_mm:10,rotation_deg:0,z_index:0,...extra});
const scene=(objects)=>({schema_version:"1.0",active_face_id:"front",faces:[{id:"front",name:"앞",width_mm:100,height_mm:100,background:"#ffffff",objects}]});
test("rotated bounds are used for alignment, not just the unrotated origin",()=>{
  const s=scene([obj("a",30,20,{rotation_deg:90}),obj("b",60)]);
  const next=alignObjects(s,"front",["a","b"],"left");
  assert.ok(Math.abs(objectBounds(next.faces[0].objects[0]).left-objectBounds(next.faces[0].objects[1]).left)<.0001);
  assert.equal(s.faces[0].objects[1].x_mm,60);
  const center=alignObjects(s,"front",["a"],"center",true);
  const b=objectBounds(center.faces[0].objects[0]);assert.ok(Math.abs((b.left+b.right)/2-50)<.0001);
});
test("distribution preserves endpoints and creates equal gaps with unequal widths",()=>{
  const s=scene([obj("a",10,10),obj("b",25,20),obj("c",80,10)]);
  const next=distributeObjects(s,"front",["a","b","c"],"x");
  assert.deepEqual(next.faces[0].objects.map(o=>o.x_mm),[10,40,80]);
});
test("locked geometry, text, visibility, layer order and deletion are blocked centrally",()=>{
  const s=scene([obj("a",10,10,{locked:true}),obj("b",40)]);
  for(const patch of [{x_mm:20},{visible:false},{z_index:5},{text:"changed"},{locked:false}]){
    const n=structuredClone(s);Object.assign(n.faces[0].objects[0],patch);
    assert.throws(()=>assertLockedObjectsUnchanged(s,n),/잠긴/);
  }
  const unlocked=setObjectsLocked(s,["a"],false);assert.doesNotThrow(()=>assertLockedObjectsUnchanged(s,unlocked,true));
  unlocked.faces[0].objects[0].x_mm=40;assert.throws(()=>assertLockedObjectsUnchanged(s,unlocked,true),/잠긴/);
  assert.equal(deleteObjects(s,["a","b"]).faces[0].objects.length,1);
  assert.equal(patchObjects(s,[{id:"a",patch:{x_mm:55}}]).faces[0].objects[0].x_mm,10);
});
test("snapping moves a selected group together toward face and object anchors",()=>{
  const s=scene([obj("a",10),obj("b",30),obj("c",60)]),f=s.faces[0];
  const snap=snapTranslation(f,["a","b"],19.8,0,1);
  assert.equal(snap.dx,20);assert.ok(snap.guides.some(g=>g.axis==="x"));
  assert.deepEqual(selectionInRect(f,{x_mm:9,y_mm:9,width_mm:32,height_mm:12}),["a","b"]);
});
test("crop uses original normalized coordinates and retains fractional pixel boundaries",()=>{
  assert.deepEqual(cropPixels({x:.1,y:.2,width:.5,height:.6},1000,500),{x:100,y:100,width:500,height:300});
  assert.deepEqual(cropPixels(null,30,20),{x:0,y:0,width:30,height:20});
  assert.throws(()=>cropPixels({x:.8,y:0,width:.5,height:1},100,100),/영역/);
});

 test("reordering an unlocked image does not mutate locked neighbors",async()=>{const {moveLayer,sendLayerToBack}=await import("../src/model.ts");const base={schema_version:"1.0",active_face_id:"front",faces:[{id:"front",name:"앞",width_mm:100,height_mm:150,background:"#fff",objects:[{id:"locked",type:"text",face_id:"front",x_mm:10,y_mm:10,width_mm:10,height_mm:10,rotation_deg:0,z_index:0,locked:true},{id:"image",type:"image",face_id:"front",x_mm:10,y_mm:10,width_mm:10,height_mm:10,rotation_deg:0,z_index:1}]}]};for(const next of [moveLayer(base,"image",-1),sendLayerToBack(base,"image")]){assertLockedObjectsUnchanged(base,next);assert.equal(next.faces[0].objects[0].id,"image");assert.equal(next.faces[0].objects.find(o=>o.id==="locked").z_index,0);}});
