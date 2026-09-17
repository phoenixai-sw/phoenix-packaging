"""Local review fixture through the real platform exporter; no manufacturer approval."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(__file__).parent))
from test_structure_v2 import fixed_box,registered_project
from services.api.exporters import export_review_pdf

project=registered_project(fixed_box(),{"width_mm":160,"height_mm":230,"depth_mm":60})
colors={"front":"#E7ECE2","back":"#E6D8C7","left":"#D7E4E8","right":"#EBDCD6","top":"#D6E1CC","bottom":"#E2DDEB"}
for face in project["scene"]["faces"]:
    face["background"]=colors[face["id"]]
    face["objects"]=[{"id":"label-"+face["id"],"type":"text","face_id":face["id"],"x_mm":10,"y_mm":20,"width_mm":face["width_mm"]-20,"height_mm":min(40,face["height_mm"]-30),
                      "text":face["name"]+"\n등록 구조 검토","font_size_pt":16 if face["width_mm"]>100 else 11,"font_weight":700,"font_id":"NotoSansKR","color":"#173E35"}]
output=ROOT/"output/pdf/registered-structure-review.pdf"
manifest=export_review_pdf(project,output)
print(f"Registered review fixture: {len(manifest['pages'])} pages; geometry {manifest['geometry_hash']}")
