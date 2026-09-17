"""Reproduce a labelled basic-specification review PDF and visual QA pages."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from generate_sample import sample_scene
from services.api.geometry import barcode_geometry
from services.api.exporters import export_review_pdf
from services.api.exporters.public_profiles import BASIC_REVIEW_PROFILE_ID


def generate():
    scene=sample_scene()
    for face in scene["faces"]: face["background"]="#f5edda"
    block=next(obj for obj in scene["faces"][0]["objects"] if obj["id"]=="f-block")
    block.update(x_mm=-3,width_mm=236)
    back=scene["faces"][1]
    back["objects"]=[obj for obj in back["objects"] if obj["id"] not in {"b-info-bg","b-info"}]
    barcode=barcode_geometry("0123456789012")
    back["objects"].append({"id":"spec-ean13","type":"barcode","face_id":"back","x_mm":70,"y_mm":220,
        "width_mm":barcode["width_mm"],"height_mm":barcode["height_mm"],"module_mm":.33,"bar_height_mm":22.85,
        "barcode_value":barcode["value"],"z_index":5})
    output=Path(__file__).parent/"artifacts"/"basic-spec-review.pdf"
    manifest=export_review_pdf({"id":"basic-spec-test-fixture","revision":1,"review_profile_id":BASIC_REVIEW_PROFILE_ID,"scene":scene},output)
    import pypdfium2 as pdfium
    document=pdfium.PdfDocument(output)
    try:
        for index in range(len(document)):
            page=document[index];bitmap=page.render(scale=1.5)
            try: bitmap.to_pil().save(output.with_name(f"basic-spec-review-{index+1}.png"))
            finally: bitmap.close();page.close()
    finally: document.close()
    print({"file":str(output),"pages":len(manifest["pages"]),"review_profile_id":manifest["review_profile_id"],
           "sha256":manifest["sha256"],"checks":manifest["pdf_verification"]})


if __name__=="__main__": generate()
