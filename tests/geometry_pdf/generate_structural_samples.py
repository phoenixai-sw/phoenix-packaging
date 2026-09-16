"""Reproducible demo all-face/net PDF artifacts with orientation markers."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from services.api.geometry import new_scene,barcode_geometry
from services.api.exporters import export_review_pdf

if __name__=="__main__":
    folder=Path(__file__).parent/"artifacts"
    for kind,extra in (("stand-up-pouch",{"bottom_mm":60}),("folding-box",{"depth_mm":60})):
        scene=new_scene(kind,160,230,**extra)
        colors=["#E6EBDC","#EBDCCF","#EADAB1","#D9E2E3","#EDD4C5","#D5E2D5"]
        for index,face in enumerate(scene["faces"]):
            face["background"]=colors[index]
            y=38 if face["height_mm"]>100 else 17
            face["objects"]=[{"id":face["id"]+"-label","type":"text","face_id":face["id"],"x_mm":16,"y_mm":y,"width_mm":face["width_mm"]-32,"height_mm":19,"text":face["name"]+" ↑\n"+face["id"].upper()+" / 좌 → 우","font_size_pt":12,"color":"#234D3D"}]
        spec=barcode_geometry("0123456789012")
        scene["faces"][0]["objects"].append({"id":"code","type":"barcode","face_id":"front","x_mm":30,"y_mm":120,"width_mm":spec["width_mm"],"height_mm":spec["height_mm"],"barcode_value":spec["value"]})
        if kind=="stand-up-pouch":scene["holes"]=[{"id":"hang","face_id":"front","center_x_mm":60,"center_y_mm":22,"diameter_mm":6}]
        manifest=export_review_pdf({"id":"structural-qa","revision":1,"scene":scene},folder/f"{kind}-review.pdf")
        print(kind,len(manifest["pages"]),manifest["sha256"])
