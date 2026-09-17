"""Render the actual browser QA evidence into a small Korean PDF manual."""
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
FONT=ROOT/'fixtures/fonts'
pdfmetrics.registerFont(TTFont('KR',str(FONT/'NotoSansKR-Regular.ttf')))
pdfmetrics.registerFont(TTFont('KRB',str(FONT/'NotoSansKR-Bold.ttf')))
W,H=595.276,841.89
target=ROOT/'output/pdf/spec-completion-exports-manual.pdf'
target.parent.mkdir(parents=True,exist_ok=True)
c=canvas.Canvas(str(target),pagesize=(W,H))
c.setTitle('Phoenix Packaging - ZIP 및 등록 구조 실제 진행 매뉴얼')
c.setAuthor('Phoenix Packaging')
pages=[
 ('원본 ZIP을 받았습니다','저장본 15 · 원본 이미지 2개 · 글꼴 2개 · 0크레딧','editable-zip.png',[
  '검수와 출력에서 편집용 프로젝트 ZIP을 누릅니다. 현재 변경을 저장한 뒤 해당 저장본을 고정해 묶습니다.',
  '실제 12파일·13.7 MiB 결과의 해시와 scene.json 일치를 확인했습니다. 파일 이력에서 다시 받을 수 있습니다.',
  '제조 승인 파일이 아니며, 현재 앱에는 ZIP 재가져오기 화면이 없습니다.']),
 ('독립 실링 구조를 등록했습니다','좌 8 · 우 12 · 상 6 · 하 14 mm / 자체 시험 자료','structure-registered.png',[
  '관리자의 등록 구조 검토 공개 메뉴에서 예시 JSON을 서버 검증하고 출처·사용권·등록 주체를 기록했습니다.',
  '로컬 시험 구조로 명시해 공개했습니다. 등록 및 공개는 실제 제조사 승인을 뜻하지 않습니다.',
  '사용자는 같은 포장 종류의 구조만 선택할 수 있습니다. 고정 치수와 허용 범위를 서버가 검사합니다.']),
 ('충돌을 찾아 직접 수정했습니다','실링 변경과 규격 축소 시 안전영역 검사','structure-collision.png',[
  '230×310mm 봉투의 문구 X16·폭198mm는 끝이214mm였습니다. 새 구조의 오른쪽 안전선은213mm입니다.',
  '해당 면·객체 확인을 눌러 앞면3개·뒷면2개 문구 폭을196mm로 조정했습니다. 문구 내용은 유지했습니다.',
  '사진은 폭228mm 미리보기에서 같은 충돌을 재현한 화면입니다. 축소를 적용하지 않았고230mm를 유지했습니다.']),
 ('검토한 구조를 적용했습니다','새 저장본 생성 · 기존 인쇄 확인 해제','structure-preview-valid.png',[
  '수정 후 저장하고 구조 미리보기를 다시 실행하자 통과했습니다. 앞뒤 안전영역은200×280mm입니다.',
  '적용 버튼으로 새 저장본을 만들었습니다. 기존 객체의mm크기는 유지하고 인쇄 프로필·확인 체크는 해제합니다.',
  '등록 구조와 해석 도형은 스냅샷에 고정합니다. 과거 결과를 새 등록 데이터로 덮어쓰지 않습니다.']),
 ('바코드와 PDF까지 확인했습니다','샘플 EAN-13 배치 · 검토 PDF 2페이지','structure-pdf-back.png',[
  '샘플9520000000011을 새 안전영역 안 X13·Y259.15mm에 배치했습니다. 정식 상품 번호 발급은 아닙니다.',
  '검토 PDF는236×316mm 2페이지로 생성됐습니다. 재단230×310mm에 사방3mm 검토 도련이 포함됩니다.',
  '한글·실링·안전선·바코드를 렌더로 확인했습니다. 새 구조의 구멍·지퍼·노치와 제조 출력은 아직 제한됩니다.']),
 ('AI 지시에 면과 여백을 반영합니다','견적 확인까지만 실행 · 이번 검증 유료 생성 없음','ai-layout-context.png',[
  '서버가 현재 면의 비율과 텍스트·바코드 위치, 연결된 브랜드 색을 생성 지시에 고정합니다.',
  '실제 견적에 스탠드 파우치·앞면·문구자리1곳이 표시됐습니다. 생성 결과의 색과 여백은 별도로 확인해야 합니다.',
  '이 문서는 로컬 기능 검증 기록입니다. 전체 작업지시서 완료, 실제 PG 또는 최종 제조본 완료를 뜻하지 않습니다.'])
]
def paragraph(txt,y):
    c.setFont('KR',10.5);c.setFillColor(HexColor('#35473d'))
    line=''
    for char in txt:
        if pdfmetrics.stringWidth(line+char,'KR',10.5)>W-88:
            c.drawString(44,y,line);y-=17;line=''
        line+=char
    if line:c.drawString(44,y,line);y-=17
    return y-8
for i,(title,subtitle,picture,notes) in enumerate(pages,1):
    c.setFillColor(HexColor('#f7f5ef'));c.rect(0,0,W,H,fill=1,stroke=0)
    c.setFillColor(HexColor('#304b3c'));c.rect(0,H-12,W,12,fill=1,stroke=0)
    c.setFont('KRB',11);c.drawString(44,H-48,'PHOENIX PACKAGING / 실제 진행 매뉴얼')
    c.setFont('KRB',22);c.drawString(44,H-88,title)
    c.setFont('KR',11);c.setFillColor(HexColor('#697568'));c.drawString(44,H-113,subtitle)
    p=ROOT/'docs/images/spec-completion-exports'/picture
    with Image.open(p) as image:iw,ih=image.size
    maxw,maxh=W-88,435;scale=min(maxw/iw,maxh/ih)
    dw,dh=iw*scale,ih*scale
    top=H-136
    c.drawImage(ImageReader(str(p)),(W-dw)/2,top-dh,width=dw,height=dh,mask='auto')
    y=top-450
    for note in notes:y=paragraph(note,y)
    assert y>43,(i,y)
    c.setStrokeColor(HexColor('#d6ddce'));c.line(44,42,W-44,42)
    c.setFillColor(HexColor('#697568'));c.setFont('KR',9)
    c.drawString(44,26,'2026-09-18 · 자체 QA · 제조 승인 아님')
    c.drawRightString(W-44,26,f'{i} / {len(pages)}')
    c.showPage()
c.save();print(target)
