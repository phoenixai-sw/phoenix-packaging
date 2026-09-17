import test from "node:test";
import assert from "node:assert/strict";
import {compareScenes} from "../src/revisions.ts";
const image={id:"image",type:"image",face_id:"front",asset_id:"original",x_mm:10,y_mm:10,width_mm:30,height_mm:40,rotation_deg:0,z_index:0};
const scene={schema_version:"1.0",active_face_id:"front",faces:[{id:"front",name:"앞면",width_mm:100,height_mm:150,background:"#fff",objects:[image]},{id:"back",name:"뒷면",width_mm:100,height_mm:150,background:"#fff",objects:[]}]};
test("revision comparison covers crop, added/removed objects, face colors and structural changes without changing originals",()=>{const next=structuredClone(scene);next.faces[0].objects[0].crop={x:.1,y:.2,width:.5,height:.6};next.faces[1].objects.push({...image,id:"new",face_id:"back"});next.faces[1].background="#000";next.holes=[{id:"hole",face_id:"front",center_x_mm:50,center_y_mm:15,diameter_mm:6}];const diff=compareScenes(scene,next);assert.deepEqual(diff.map(c=>c.label),["자르기","배경색","레이어 추가","걸이 구멍"]);assert.equal(diff[2].face_id,"back");assert.equal(scene.faces[0].objects[0].crop,undefined);assert.equal(compareScenes(next,scene).find(c=>c.object_id==="new").label,"레이어 삭제");});
