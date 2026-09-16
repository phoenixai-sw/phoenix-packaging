"""Reproduce the visually checked two-page review artifact."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.api.geometry import default_scene
from services.api.exporters import export_review_pdf


def text(identifier, face, x, y, width, height, content, size=18, color="#173E34", align="left"):
    return {"id":identifier,"type":"text","face_id":face,"x_mm":x,"y_mm":y,"width_mm":width,"height_mm":height,
            "text":content,"font_size_pt":size,"font_id":"NotoSansKR","color":color,"align":align,"z_index":3}


def shape(identifier, face, x, y, width, height, color, kind="rect", opacity=1):
    return {"id":identifier,"type":"shape","face_id":face,"x_mm":x,"y_mm":y,"width_mm":width,"height_mm":height,
            "color":color,"shape":kind,"opacity":opacity,"z_index":1}


def sample_scene():
    scene = default_scene()
    front, back = scene["faces"]
    front["objects"] = [
        text("f-brand","front",23,25,184,12,"PHOENIX FOODS",17,align="center"),
        text("f-title","front",23,49,184,40,"매일의 고소함\n단백질 한 봉지",34,align="center"),
        shape("f-block","front",0,104,230,154,"#173E34"),
        shape("f-circle-1","front",24,136,72,72,"#DAB77F","ellipse"),
        shape("f-circle-2","front",82,136,72,72,"#EAD6AA","ellipse"),
        shape("f-circle-3","front",140,136,65,65,"#F5E8CB","ellipse"),
        text("f-protein","front",25,117,180,14,"높은 단백질 함량",23,"#F9F5E8",align="center"),
        text("f-en","front",25,221,180,15,"Protein 20g · 100%",20,"#F9F5E8",align="center"),
        text("f-note","front",25,268,180,17,"한글 · English · 숫자 123 · 특수문자 & ()",12,align="center"),
        text("f-demo","front",25,283,180,10,"문구·배치 검증을 위한 가상 상품입니다.",10,align="center"),
    ]
    back["objects"] = [
        text("b-brand","back",23,25,184,12,"PHOENIX FOODS",17),
        text("b-title","back",23,48,184,25,"제품 표시사항",30),
        shape("b-rule","back",23,81,184,.5,"#173E34"),
        text("b-desc","back",23,93,184,28,"고객이 입력한 한글은 이미지와 분리된\n편집 가능한 텍스트로 보존됩니다.",18),
        text("b-details","back",23,134,184,69,"상품명: 단백질 한 봉지\n내용량: 20g\n원재료: 대두 & 우유 (국산)\n보관 방법: 서늘하고 건조한 곳",17),
        shape("b-info-bg","back",23,216,184,46,"#E1E6D9"),
        text("b-info","back",29,225,172,32,"확인 필요\n표시사항·영양정보·원재료는 가상 예시입니다.\n실제 상품의 확정 문구를 입력해 주세요.",12),
        text("b-footer","back",23,279,184,13,"230 × 310 mm / 앞면·뒷면 / 검토용",11),
    ]
    return scene


if __name__ == "__main__":
    output = Path(__file__).parent / "artifacts" / "review-sample.pdf"
    manifest = export_review_pdf({"id":"phoenix-review-sample","revision":1,"scene":sample_scene()}, output)
    print(f"Created {output}: {len(manifest['pages'])} pages; SHA-256 {manifest['sha256']}")
