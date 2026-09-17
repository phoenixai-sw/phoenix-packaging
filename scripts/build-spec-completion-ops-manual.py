"""Create the Korean manual from verified local and production UI evidence.

Run with the bundled PDF Python runtime. This authors a manual, never a package
design or platform export. The PNGs below are unmodified screenshots and renders
of the immutable platform PDFs. The first ten local pages remain unchanged;
two separately labeled appendix pages record the later production verification.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = ROOT / "docs/images/spec-completion-ops"
OUTPUTS = ROOT / "output/ops-ui"
PRODUCTION = ROOT / "output/ops-production"
TARGET = ROOT / "output/pdf/spec-completion-ops-manual.pdf"
W, H, M = 595.276, 841.89, 42
CW = W - 2 * M
BG = colors.HexColor("#F8F6F0")
INK = colors.HexColor("#253C30")
BODY = colors.HexColor("#354239")
MUTED = colors.HexColor("#697268")
LINE = colors.HexColor("#D8DED3")
ACCENT = colors.HexColor("#D9663D")
FONT_DIR = ROOT / "fixtures/fonts"
pdfmetrics.registerFont(TTFont("KR", str(FONT_DIR / "NotoSansKR-Regular.ttf")))
pdfmetrics.registerFont(TTFont("KRB", str(FONT_DIR / "NotoSansKR-Bold.ttf")))
pdfmetrics.registerFontFamily("KR", normal="KR", bold="KRB")
TARGET.parent.mkdir(parents=True, exist_ok=True)
c = canvas.Canvas(str(TARGET), pagesize=(W, H), pageCompression=1)
c.setTitle("Phoenix Packaging - 운영 도구와 출력 실제 사용 매뉴얼")
c.setAuthor("Phoenix Packaging")
c.setSubject("2026-09-18 로컬 수행 기록 10쪽과 운영 실사용 검증 부록 2쪽. 제조 승인 아님.")
ASSETS: set[Path] = set()


def text(value: str, x: float, top: float, size=10.5, bold=False, color=BODY):
    c.setFillColor(color)
    c.setFont("KRB" if bold else "KR", size)
    c.drawString(x, H - top - size, value)


def para(value: str, x: float, top: float, width: float, size=10.5,
         leading=16, color=BODY, bold=False) -> float:
    style = ParagraphStyle(
        "body", fontName="KRB" if bold else "KR", fontSize=size,
        leading=leading, textColor=color, alignment=TA_LEFT,
        wordWrap="CJK", splitLongWords=True,
    )
    p = Paragraph(escape(value).replace("\n", "<br/>"), style)
    _, height = p.wrap(width, 1000)
    assert top + height < H - 46, (value[:40], top, height)
    p.drawOn(c, x, H - top - height)
    return top + height


def image(path: Path, x: float, top: float, width: float, height: float,
          border=True, background=colors.white):
    ASSETS.add(path)
    with Image.open(path) as im:
        iw, ih = im.size
    scale = min(width / iw, height / ih)
    dw, dh = iw * scale, ih * scale
    c.setFillColor(background)
    c.setStrokeColor(LINE)
    c.roundRect(x, H - top - height, width, height, 5, fill=1, stroke=int(border))
    c.drawImage(ImageReader(str(path)), x + (width - dw) / 2,
                H - top - (height + dh) / 2, width=dw, height=dh, mask="auto")


def shot(name: str, top=135, height=287, caption="") -> float:
    image(SCREENSHOTS / name, M, top, CW, height)
    bottom = top + height
    if caption:
        bottom = para(caption, M, bottom + 9, CW, size=8.8, leading=13, color=MUTED)
    return bottom


def steps(items: list[str], top: float, x=M, width=CW) -> float:
    for i, item in enumerate(items, 1):
        c.setFillColor(INK)
        c.circle(x + 8, H - top - 8, 8, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("KRB", 8)
        c.drawCentredString(x + 8, H - top - 11, str(i))
        top = para(item, x + 27, top - 1, width - 27) + 12
    return top


def callout(title: str, value: str, top: float, height: float,
            x=M, width=CW, warning=False):
    c.setFillColor(colors.HexColor("#F3E9DA" if warning else "#EAF0E6"))
    c.roundRect(x, H - top - height, width, height, 7, fill=1, stroke=0)
    text(title, x + 15, top + 10, 10, True, ACCENT if warning else INK)
    end = para(value, x + 15, top + 31, width - 30, size=9.8, leading=15)
    assert end <= top + height - 8, (title, end, top + height)


def start(page: int, title: str, subtitle: str):
    c.setFillColor(BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setFillColor(INK)
    c.rect(0, H - 9, W, 9, fill=1, stroke=0)
    text("PHOENIX PACKAGING", M, 29, 10, True)
    text("운영 실사용 검증 / 배포 후 부록" if page > 10 else "운영 도구와 출력 / 실제 사용 매뉴얼", M + 172, 30, 9, color=MUTED)
    text(title, M, 65, 24, True)
    para(subtitle, M, 102, CW, size=10, leading=15, color=MUTED)
    c.setStrokeColor(LINE)
    c.line(M, 42, W - M, 42)
    text("2026-09-18 · 실제 운영 UI 검증 · 제조 승인 아님" if page > 10 else "2026-09-18 · 격리 로컬 UI 검증 · 제조 승인 아님", M, H - 31, 8.3, color=MUTED)
    c.setFont("KR", 8.5)
    c.setFillColor(MUTED)
    c.drawRightString(W - M, 23, f"부록 {page - 10} / 2" if page > 10 else f"{page:02d} / 10")


def finish():
    c.showPage()


# 1. Scope and orientation: one actual output is the visual anchor.
start(1, "실제로 눌러 보고, 파일까지 확인", "글꼴 · PDF/ZIP · SVG · 정책 · 서비스 · 성과·원가 · 보관")
image(OUTPUTS / "svg-review-1.png", M, 142, 233, 320)
text("이 문서의 기준", 298, 150, 13, True)
y = para("localhost:3001의 격리 QA 공간에서 실제 메뉴로 진행한 순서와 결과를 담았습니다.", 298, 179, 255, 11, 18)
y = para("등록 글꼴 QA 프로젝트\n230 × 310mm / 앞·뒷면\n저장본 2: 글꼴 출력 3종\n저장본 4: SVG 추가 후 PDF·ZIP", 298, y + 22, 255, 10.5, 19)
y = para("왼쪽은 플랫폼 검토 PDF의 실제 첫 페이지입니다. 매뉴얼 작성을 위해 패키지 PDF를 다시 만들지 않았습니다.", 298, y + 23, 255, 10, 16)
text("빠르게 찾아보기", M, 487, 12, True)
items = [
    ("02", "팀 글꼴 등록과 브랜드 연결"),
    ("03", "편집·재열기와 PDF/편집 ZIP"),
    ("04", "CMYK·윤곽선·CUT/FOLD 시험"),
    ("05", "정적 SVG 업로드와 원본 보관"),
    ("06", "요금 정책 게시와 크레딧 정정"),
    ("07", "서비스 신청부터 견적·완료까지"),
    ("08", "미확인 비용과 시간 정정"),
    ("09", "보관·삭제 요청과 취소"),
    ("10", "전달 파일과 확인 범위"),
]
for idx, (number, label) in enumerate(items):
    col, row = divmod(idx, 5)
    x, top = M + col * 268, 518 + row * 27
    text(number, x, top, 10, True, ACCENT)
    text(label, x + 29, top, 10)
callout("이전 최종 디자인과 구분", "이 사본은 글꼴·출력 기능 시험용입니다. 킹콩바이츠 V2 디자인을 대체하지 않으며, 실제 결제·유료 AI 생성·제조사 승인·운영 배포 확인을 수행한 문서가 아닙니다.", 679, 92, warning=True)
finish()

# 2. Font upload / immutable original / permission.
start(2, "같은 글꼴을 브랜드에 연결", "브랜드 보관함 → 팀 글꼴 보관함 → 브랜드 편집")
y = shot("font-brand.png", caption="실제 UI: Noto Sans KR Bold 등록과 브랜드의 허용 글꼴 목록을 확인했습니다.")
y = steps([
    "정적 TTF를 선택하고 출처, 권리자, 라이선스와 웹·편집·인쇄 사용 권한을 기록합니다. ZIP 재배포 권한은 별도로 확인합니다.",
    "브랜드 편집에서 ‘새 문구에 허용할 등록 글꼴’을 선택해 저장합니다. 프로젝트에 해당 브랜드를 연결합니다.",
    "문구 레이어의 속성 → 글꼴에서 등록한 파일을 고릅니다. 파일의 실제 두께를 사용하며 굵기를 임의 합성하지 않습니다.",
], y + 22)
callout("실제 결과", "Noto Sans KR Bold 700을 등록·허용했습니다. 등록 파일과 권리 기록은 불변이며, 브랜드 허용 목록에서 제외해도 기존 저장본의 글꼴은 보존됩니다.", y + 4, 83)
para("지원 한계: 정적 TTF만 지원합니다. 가변·CFF·컬러 글꼴, 누락 글리프·복잡한 셰이핑은 차단합니다. 권리 입력은 사용자 확인 기록입니다.", M, y + 100, CW, size=9.3, leading=14, color=MUTED)
finish()

# 3. Actual editing / readback / export results.
start(3, "저장한 문구를 다시 열고 출력", "속성 → 글꼴 / 저장 → 다시 열기 / 검수와 출력 → 파일 이력")
y = shot("font-editor-reopened.png", caption="실제 UI: 저장 후 다시 연 앞면입니다. 등록 글꼴과 두 줄 문구가 유지됐습니다.")
y = steps([
    "‘오리고기 100%’와 ‘Duck 37.5g × 4개입’을 등록 글꼴 700·27pt로 저장했습니다. 재열기와 3D에서 동일 문구를 확인했습니다.",
    "검수와 출력에서 무료 검토 PDF 또는 편집용 프로젝트 ZIP을 요청합니다. 파일 이력에서 종류·저장본·완료 상태를 확인해 다운로드합니다.",
], y + 22)
rows = [
    ("검토 PDF", "20414a8b…", "2페이지 / 문구·포함 글꼴 확인"),
    ("편집 ZIP", "c2c93400…", "11파일 / TTF·라이선스·장면 해시 확인"),
    ("공통 기준", "저장본 2", "동일 원본 글꼴 SHA / 0크레딧"),
]
for idx, row in enumerate(rows):
    top = y + 6 + idx * 31
    if idx % 2 == 0:
        c.setFillColor(colors.HexColor("#EAF0E6")); c.rect(M, H - top - 31, CW, 31, fill=1, stroke=0)
    text(row[0], M + 10, top + 8, 9.4, True)
    text(row[1], M + 102, top + 8, 9.2)
    text(row[2], M + 212, top + 8, 9.2)
para("편집 ZIP은 장면·원본 이미지·허용된 글꼴을 보관하는 파일입니다. 현재 앱의 ZIP 재가져오기 기능이나 제조 승인 파일을 뜻하지 않습니다.", M, y + 111, CW, size=9.3, leading=14, color=MUTED)
finish()

# 4. Real CMYK/outline output and separated engineering pages.
start(4, "CMYK와 윤곽선은 시험 파일로 확인", "검수와 출력 → CMYK 출력 시험 → 무료 시험 ZIP → 다운로드")
labels = ["아트 / CMYK·윤곽선", "CUT / 재단선", "FOLD / 접는 선"]
names = ["cmyk-art-1.png", "cmyk-cut-1.png", "cmyk-fold-1.png"]
pw = (CW - 24) / 3
for i, (label, name) in enumerate(zip(labels, names)):
    image(OUTPUTS / name, M + i * (pw + 12), 150, pw, 233)
    para(label, M + i * (pw + 12), 396, pw, 9.6, 14, bold=True)
para("실제 시험 PDF의 첫 페이지 3종입니다. 이 삼방 봉투의 면별 FOLD에는 접는 선이 없어 시험 안내만 보이는 것이 정상입니다.", M, 429, CW, 9.2, 14, color=MUTED)
y = steps([
    "같은 저장본 2에서 CMYK 시험 ZIP e2f549f8…을 생성했습니다. 합성 ICC를 쓰는 기능 시험이며 실제 인쇄 색상 교정은 아닙니다.",
    "6개 파일의 해시, 실제 CMYK 변환, 등록 글꼴의 윤곽선과 분리된 아트·CUT·FOLD를 확인했습니다. 글꼴 원본 SHA는 앞의 PDF/ZIP과 같습니다.",
    "검토 PDF 2쪽과 CMYK 아트·CUT·FOLD 각 2쪽, 총 8쪽을 렌더해 확인했습니다. 재단 230×310mm와 사방 3mm 도련을 측정했습니다.",
], 480)
callout("제조 출력의 승인 게이트는 유지", "시험은 0크레딧·검토 전용입니다. PDF/X·별색·화이트·오버프린트·곡선 및 구멍/지퍼/노치의 제작 출력은 미지원 조건으로 차단합니다.", y + 5, 82, warning=True)
finish()

# 5. Static SVG import evidence and immutable-source readback.
start(5, "SVG는 정화 후 이미지로 가져오기", "편집기 → 이미지 또는 브랜드 → 로고 이미지 → 정적 SVG 선택")
y = shot("svg-editor.png", caption="실제 UI: 1200×900px SVG를 90×67.5mm, X70/Y140mm로 배치하고 저장했습니다.")
y = steps([
    "허용된 정적 도형을 정화해 PNG로 변환합니다. 편집기·3D·PDF는 같은 표시 PNG를 사용하고, 정화 SVG 원본은 별도로 보관합니다.",
    "SVG 속 글자는 원래 글꼴로 윤곽선을 만든 뒤 업로드합니다. 플랫폼에서 수정할 문구는 별도 텍스트 레이어로 추가합니다.",
], y + 20)
image(OUTPUTS / "svg-upload-raster.png", M, y + 3, 116, 87)
para("저장본 4의 실제 출력\nPDF febdd1dd…: 2쪽 모두 시각 검수\nZIP 0e658b5f…: SVG·PNG·TTF·장면 해시 일치\n배치 해상도: 338.67ppi / 원본 1200×900px", M + 134, y + 4, CW - 134, 10, 17)
para("스크립트·외부 참조·내장 이미지·애니메이션·필터·SVG 글자는 차단합니다. SVG를 편집 가능한 개별 도형으로 분해하지 않습니다. 1MiB 등 파일·도형·시간 예산을 적용합니다.", M, y + 112, CW, size=9.3, leading=14, color=MUTED)
finish()

# 6. Demonstrated policy and credit changes are strictly local and reverted.
start(6, "변경은 비교하고, 정정은 기록으로", "운영 관리 → 요금·모델 정책 / 크레딧 정정")
y = shot("policy-diff.png", height=262, caption="실제 UI: 게시 전에 공급가·VAT 포함 가격·크레딧 단가의 차이를 확인했습니다.")
image(SCREENSHOTS / "credit-corrections.png", M, y + 20, 247, 149)
para("로컬 지급·회수 이력", M + 266, y + 20, CW - 266, 11, 17, bold=True)
para("표준 전용 3크레딧 지급 → 같은 지급분 3회수. 잔액은 30으로 돌아오고 각각의 사유·원장 기록은 남았습니다.", M + 266, y + 49, CW - 266, 10, 16)
y = steps([
    "현재 정책으로 초안을 만들고 변경 사유와 차이를 확인해 게시합니다. 기존 주문·유효 구독의 동의 가격은 당시 스냅샷을 유지합니다.",
    "Starter 공급가 49,000→51,000원, 표준 10→11크레딧을 로컬에서 확인한 뒤 원래 값으로 복원했습니다. 공개 가격에는 VAT 포함 56,100원이 반영됐습니다.",
], y + 187)
para("운영 가격·크레딧은 변경하지 않았습니다. 이 메뉴가 실제 결제 게이트를 열거나 유료 구독 자격을 부여하지 않습니다. 회수는 지정 지급분의 남은 사용 가능 수량만 대상입니다.", M, y + 1, CW, size=9.3, leading=14, color=MUTED)
finish()

# 7. Service workflow, clearly distinct from payment and actual delivery.
start(7, "서비스 신청을 견적과 이력으로 관리", "소유자: 별도 서비스 신청 / 관리자: 서비스 신청과 견적 관리")
y = shot("service-history.png", caption="실제 UI: 자체 시험 신청에서 견적·수락·진행·전달·완료의 이력이 남았습니다.")
y = steps([
    "소유자가 검토·도입 지원·첫 달 Pro 모집 문의를 접수합니다. 관리자는 범위, 제외 사항, VAT 포함 금액과 기한을 제안합니다.",
    "소유자가 견적을 수락하면 관리자가 진행 → 결과 전달 → 업무 완료를 순서대로 기록합니다. 상태 변경은 결제 수납을 뜻하지 않습니다.",
    "로컬 55,000원 시험 견적에서 신청부터 완료까지 6단계를 실행했습니다. 신청 0a47f5ed…에 저장본 6·사건 6개·견적 1개가 남았습니다.",
], y + 22)
callout("실제 수납·구독 개통은 하지 않았습니다", "해당 QA DB의 결제 주문과 구독은 각각 0건입니다. 전문 서비스 납품이나 실제 결제를 검증한 사례가 아니며, 첫 달 Pro 문의의 구독 개통 단계는 아직 연결하지 않았습니다.", y + 4, 98, warning=True)
finish()

# 8. Metrics with unknown and superseded costs explicitly distinct.
start(8, "미확인 비용을 0원으로 바꾸지 않기", "운영 관리 → 내부 성과·원가 → 기간 조회 / 원가 기록·정정")
y = shot("metrics-cost-correction.png", caption="실제 UI: 지원 시간 5분을 7분으로 정정하고 원본 이력과 유효 합계를 함께 확인했습니다.")
y = steps([
    "기간을 정한 뒤 실제 제공자와 fixture, 제조사 회신과 test를 구분합니다. 비용은 실제·추정·미확인으로 나누어 봅니다.",
    "지원 비용을 금액 미확인·5분으로 기록한 다음, 같은 항목을 사유와 함께 7분으로 정정했습니다. 이전 기록을 덮어쓰지 않습니다.",
    "유효 합계는 5+7=12분이 아닌 7분, 금액은 0원이 아닌 미확인(null)입니다. 활동 추정 2.0분과 fixture 요청 4건도 별도로 표시됐습니다.",
], y + 22)
callout("시간 수치의 의미", "활동 시간은 브라우저 신호에 따른 추정으로 정확한 노동·청구 시간이 아닙니다. 입력 문구·키·포인터 좌표를 수집하거나 외부 분석 서비스로 보내지 않습니다.", y + 4, 83)
para("실제 유료 고객 획득, 제조사 10건 검증, 공급자 청구서와의 원가 대사는 이번 로컬 점검에서 수행하지 않았습니다.", M, y + 100, CW, 9.3, 14, color=MUTED)
finish()

# 9. Deletion request/cancel only; no successful physical deletion claim.
start(9, "보관과 삭제 요청은 분리해서 확인", "소유자 → 보관·삭제 관리 → 파일 선택 → 요청 또는 취소")
y = shot("retention-request-canceled.png", caption="실제 UI: 검토 PDF의 보존 hold 안내를 확인하고 삭제 요청을 취소했습니다.")
y = steps([
    "삭제할 파일의 종류와 작업을 확인하고 요청 사유를 적습니다. 요청·승인·유예·보존 hold·실제 삭제는 서로 다른 상태입니다.",
    "분쟁·지원·백업 보존이나 사용 중인 참조가 있으면 서버가 삭제를 막습니다. 실행되지 않은 요청은 취소할 수 있습니다.",
    "검토 PDF 20414a8b…에 요청 → 보존 안내 → 취소를 실제 화면으로 수행했습니다. 이 점검에서 실제로 삭제한 파일은 0개입니다.",
], y + 22)
callout("운영 파일 삭제 없이 확인한 절차", "운영 삭제·GC 스위치를 켜거나 운영 파일을 삭제하지 않았습니다. 조직/프로젝트 전체 삭제와 정체를 알 수 없는 파일의 자동 GC는 지원하지 않습니다.", y + 4, 83, warning=True)
finish()

# 10. Keep the handoff practical, local, and traceable without a hash dump.
start(10, "결과 파일과 다음 확인", "원본 플랫폼 출력은 그대로 보존 / 이 문서는 로컬 수행 기록")
image(SCREENSHOTS / "font-editable-zip.png", M, 139, CW, 238)
para("실제 UI: 편집 ZIP의 저장본·파일 수·0크레딧·완료 다운로드를 확인했습니다.", M, 388, CW, 9, 14, color=MUTED)
text("작업 PC의 전달 파일", M, 422, 12, True)
files = [
    ("SVG 포함 검토 PDF", "output/ops-ui/svg-upload-review-febdd1dd.pdf"),
    ("SVG·PNG·글꼴 편집 ZIP", "output/ops-ui/svg-upload-editable-0e658b5f.zip"),
    ("글꼴 CMYK 시험 ZIP", "output/ops-ui/uploaded-bold-cmyk-test-e2f549f8.zip"),
    ("이 사용 매뉴얼", "output/pdf/spec-completion-ops-manual.pdf"),
]
y = 449
for label, path in files:
    text(label, M, y, 9.8, True)
    y = para(path, M, y + 17, CW, 8.4, 12, color=MUTED) + 12
callout("확인 완료 / 별도 확인이 필요한 것", "완료: 실제 UI 수행, 저장·재열기, 출력 원본과 해시·글꼴·이미지 연결, 총 10쪽 패키지 PDF 시각 검사.\n별도: 운영 배포의 제공 상태, 실제 유료 결제, 제조사 색상 교정·입고 승인, 물리 인쇄 품질. 이 시험 파일을 제조 승인본으로 사용하지 않습니다.", y + 5, 115, warning=True)
para("근거: docs/spec-completion-ops-release.ko.md 및 연결된 세부 검증 문서. 실제 화면은 docs/images/spec-completion-ops, 출력·검증 JSON은 output/ops-ui에 있습니다. 출력 파일은 이 작업 PC에서 별도 전달하며 Git에 대형 파일을 넣지 않습니다.", M, y + 134, CW, 8.8, 13, color=MUTED)
finish()

# 11. Later production verification, separate from the ten preserved local pages.
start(11, "운영에서도 글꼴과 SVG를 저장", "배포 21e5070 · Vercel Linux · 실제 UI · 같은 QA 프로젝트의 저장본 5")
image(SCREENSHOTS / "production-font-mobile.png", M, 145, 164, 355)
image(SCREENSHOTS / "production-svg-editor.png", M + 179, 145, CW - 179, 355)
para("등록 글꼴의 출처·OFL·권한 입력과 등록 완료 안내", M, 511, 164, 8.7, 13, color=MUTED)
para("새로 연 실제 운영 편집기: 등록 문구와 SVG 이미지 저장 완료", M + 179, 511, CW - 179, 8.7, 13, color=MUTED)
y = steps([
    "배포 전 로컬 기록 1~10쪽은 그대로 보존했습니다. 이 부록은 21e5070 배포 후 별도 QA 프로젝트에서 수행한 실제 운영 확인입니다.",
    "OFL Noto Sans KR Bold 약 6.22MB를 등록하고 브랜드에 연결했습니다. ‘오리고기 100% / Duck 37.5g × 4개입’을 실제 700·27pt로 저장했습니다.",
    "SVG 업로드가 Vercel Linux에서 완료됐습니다. 1200×900px 표시 PNG를 X70/Y140mm·90×67.5mm에 배치했고, 실제 배치 해상도는 338.67ppi입니다.",
], 561)
para("프로젝트: cfd10093-af0d-43a6-87f3-5cb14ade2309\n이 프로젝트는 기능 확인용이며 이전 킹콩바이츠 V2 최종 디자인을 변경하지 않았습니다.", M, y + 3, CW, 9.1, 14, color=MUTED)
finish()

# 12. Exact immutable files generated by the user-visible production UI.
start(12, "운영 출력 3종도 원본으로 검증", "편집 ZIP → 검토 PDF → CMYK 시험 ZIP / 모두 저장본 5·0크레딧")
image(SCREENSHOTS / "production-exports.png", M, 143, 231, 353)
text("실제 파일 이력에서 3종 완료", M + 249, 148, 12, True)
para("각 요청은 플랫폼 UI에서 실행했습니다. 검증 과정은 해당 프로젝트의 DB 읽기 전용 트랜잭션과 원본 객체 GET만 사용했습니다.", M + 249, 181, CW - 249, 10, 16)
para("PDF·ZIP·등록 TTF·정화 SVG·PNG의 해시와 저장 장면이 일치했습니다. 출력 원본은 복사만 했으며 다시 생성하거나 편집하지 않았습니다.", M + 249, 261, CW - 249, 10, 16)
para("검토 PDF 2쪽과 CMYK 아트·CUT·FOLD 각 2쪽, 총 8쪽을 렌더해 전수 확인했습니다. 원본 1200×900px 이미지와 한글 굵기·배치에 잘림이 없었습니다.", M + 249, 346, CW - 249, 10, 16)
para("최종 CI: Python 844개·JS 78개 통과. 자동시험 결과와 아래 실제 파일 검수는 별도 근거입니다.", M + 249, 433, CW - 249, 9.2, 14, color=MUTED)
entries = [
    ("편집 ZIP · 6,197,054B", "f7b29a3a-b6bc-4f34-902d-5f3fff82d430"),
    ("검토 PDF · 74,016B", "0b4d3241-d971-48b3-9d48-23b10d509ffd"),
    ("CMYK 시험 ZIP · 341,983B", "1862189b-1576-400c-97d3-f511999ac589"),
]
y = 520
for label, identity in entries:
    text(label, M, y, 10, True)
    text(identity, M + 190, y + 1, 8.5, color=MUTED)
    y += 31
callout("실제 변환을 확인했지만 제조 승인본은 아닙니다", "공통 재단 230×310mm·사방 3mm 도련. 검토 Media 236×316mm, CMYK 시험 Media 236×328mm(별도 시험 표기 12mm). CMYK 4성분 ICC와 실제 윤곽선을 확인했습니다. 합성 ICC 시험이며 PDF/X·제조사 입고 승인·물리 인쇄 품질을 뜻하지 않습니다.", y + 9, 114, warning=True)
para("이번 운영 QA는 추가 유료 AI 생성과 크레딧 소비가 없었습니다. 원본과 검증 JSON: output/ops-production/production-ui-verification.json. 이 작업 PC에서 별도 전달합니다.", M, y + 141, CW, 8.9, 13, color=MUTED)
finish()

c.save()
manifest = {
    "manual": str(TARGET.relative_to(ROOT)).replace("\\", "/"),
    "sha256": hashlib.sha256(TARGET.read_bytes()).hexdigest(),
    "bytes": TARGET.stat().st_size,
    "pages": 12,
    "scope": "10 preserved local UI pages plus 2 later production UI verification pages; no manufacturing approval claim",
    "production_deployment": "21e5070",
    "production_project_id": "cfd10093-af0d-43a6-87f3-5cb14ade2309",
    "production_verification": "output/ops-production/production-ui-verification.json",
    "source_document": "docs/spec-completion-ops-release.ko.md",
    "visual_status": "awaiting_visual_review",
    "images": [
        {"path": str(p.relative_to(ROOT)).replace("\\", "/"),
         "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(ASSETS)
    ],
}
(OUTPUTS / "ops-manual-verification.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps({"path": str(TARGET), "pages": 12, "bytes": manifest["bytes"],
                  "sha256": manifest["sha256"]}, ensure_ascii=False))
