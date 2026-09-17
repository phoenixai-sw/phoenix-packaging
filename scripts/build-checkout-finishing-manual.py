"""Author six workflow pages plus preserved intake and later recovery appendices.

Run with the Codex bundled ReportLab Python. This only authors a manual; it
never changes platform projects, screenshots, output PDFs, or earlier manuals.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parents[1]
SCREEN = ROOT / "docs/images/checkout-finishing"
FILES = ROOT / "output/checkout-finishing-ui"
TARGET = ROOT / "output/pdf/checkout-finishing-manual.pdf"
QA = ROOT / "output/pdf/checkout-finishing-manual-verification.json"
W, H, M = 595.276, 841.89, 42
CW = W - M * 2
INK, BODY, MUTED = map(colors.HexColor, ("#253C30", "#354239", "#697268"))
BG, LINE, ACCENT = map(colors.HexColor, ("#F8F6F0", "#D8DED3", "#C9633D"))
for name, filename in (("KR", "NotoSansKR-Regular.ttf"), ("KRB", "NotoSansKR-Bold.ttf")):
    pdfmetrics.registerFont(TTFont(name, str(ROOT / "fixtures/fonts" / filename)))
pdfmetrics.registerFontFamily("KR", normal="KR", bold="KRB")
evidence = json.loads((FILES / "verification.json").read_text(encoding="utf-8"))
assert evidence["status"] == "succeeded" and evidence["visual_review"] == "passed_all_8_pdf_pages"
assert evidence["credits_charged"] == 0 and evidence["manufacturing_approved"] is False
assert hashlib.sha256((FILES / "finishing-trial.zip").read_bytes()).hexdigest() == evidence["sha256"]
TARGET.parent.mkdir(parents=True, exist_ok=True)
c = canvas.Canvas(str(TARGET), pagesize=(W, H), pageCompression=1)
c.setTitle("Phoenix Packaging - 서비스 결제와 파우치 가공 출력 사용 기록")
c.setAuthor("Phoenix Packaging")
c.setSubject("2026-09-18 격리 로컬 UI와 모의 결제, 무료 가공 시험. 운영 배포·제조 승인 아님.")
ASSETS: set[Path] = set()


def text(value, x, top, size=10.5, bold=False, color=BODY):
    c.setFont("KRB" if bold else "KR", size)
    c.setFillColor(color)
    assert pdfmetrics.stringWidth(value, "KRB" if bold else "KR", size) <= W - M - x + 1, value
    c.drawString(x, H - top - size, value)


def para(value, x, top, width, size=10.5, leading=16, color=BODY, bold=False):
    p = Paragraph(escape(value).replace("\n", "<br/>"), ParagraphStyle(
        "body", fontName="KRB" if bold else "KR", fontSize=size, leading=leading,
        textColor=color, wordWrap="CJK", splitLongWords=True))
    _, height = p.wrap(width, 1000)
    assert top + height < H - 48, (value[:30], top, height)
    p.drawOn(c, x, H - top - height)
    return top + height


def image(path, x, top, width, height):
    """Place the entire unchanged image, maintaining its original aspect ratio."""
    ASSETS.add(path)
    with Image.open(path) as source:
        iw, ih = source.size
    scale = min(width / iw, height / ih)
    dw, dh = iw * scale, ih * scale
    c.setFillColor(colors.white)
    c.setStrokeColor(LINE)
    c.roundRect(x, H - top - height, width, height, 5, fill=1, stroke=1)
    c.drawImage(ImageReader(str(path)), x + (width - dw) / 2,
                H - top - (height + dh) / 2, width=dw, height=dh, mask="auto")


def steps(items, top, x=M, width=CW):
    for i, item in enumerate(items, 1):
        c.setFillColor(INK)
        c.circle(x + 8, H - top - 8, 8, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("KRB", 8)
        c.drawCentredString(x + 8, H - top - 11, str(i))
        top = para(item, x + 27, top - 1, width - 27) + 13
    return top


def callout(title, value, top, height=90, x=M, width=CW, warning=False):
    c.setFillColor(colors.HexColor("#F3E9DA" if warning else "#EAF0E6"))
    c.roundRect(x, H - top - height, width, height, 7, fill=1, stroke=0)
    text(title, x + 14, top + 10, 10, True, ACCENT if warning else INK)
    end = para(value, x + 14, top + 32, width - 28, size=9.8, leading=15)
    assert end < top + height - 7, (title, end, top + height)


def start(number, title, subtitle):
    c.setFillColor(BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setFillColor(INK)
    c.rect(0, H - 9, W, 9, fill=1, stroke=0)
    text("PHOENIX PACKAGING", M, 29, 10, True)
    text("서비스 결제와 가공 출력 / 로컬 수행 기록", M + 176, 30, 9, color=MUTED)
    text(title, M, 65, 23, True)
    para(subtitle, M, 102, CW, 10, 15, MUTED)
    c.setStrokeColor(LINE)
    c.line(M, 42, W - M, 42)
    text("2026-09-18 · 모의 결제 / 무료 시험 출력 · 제조 승인 아님", M, H - 31, 8, color=MUTED)
    c.setFillColor(MUTED)
    c.setFont("KR", 8.5)
    c.drawRightString(W - M, 23, f"{number:02d} / 06" if number <= 6 else "부록 01 / 01" if number == 7 else "후속 부록 02")


start(1, "수락한 견적에서 시험 출력까지", "실제 메뉴 조작과 내려받은 파일을 구분해서 확인하는 사용 매뉴얼")
image(FILES / "production-1.png", M, 146, 231, 347)
text("이번에 직접 확인한 것", 293, 154, 13, True)
y = para("일회 서비스 55,000원\n견적 수락 → 모의 결제 → 업무 진행", 293, 189, 260, 11, 18)
y = para("첫 달 Pro 99,000원\n별도 갱신 동의 → 모의 승인\n갱신 중단 → 미사용분 모의 환불", 293, y + 23, 260, 11, 18)
para("160×230mm 파우치\n원형 타공·U 노치·지퍼 안내\n무료 시험 ZIP의 8페이지 확인", 293, y + 23, 260, 11, 18)
para("왼쪽은 플랫폼이 만든 실제 시험 아트 첫 페이지입니다. 원본 PDF를 다시 디자인하지 않았습니다.", M, 506, CW, 9.3, 14, MUTED)
callout("검증 환경과 범위", "localhost:3001의 격리 QA 공간과 모의 결제입니다. 실제 금액 결제 0건, 유료 AI 호출 0건, 제조사 승인 0건입니다. 새 기능의 운영 배포 완료를 증명하는 문서는 아닙니다.", 557, 103, warning=True)
text("02 일회 서비스     03 Pro 동의·환불     04 가공값 입력", M, 692, 10, True)
text("05 실제 CUT·PROCESS     06 전달 파일·확인 범위", M, 721, 10, True)
c.showPage()

start(2, "일회 서비스는 결제와 업무를 따로", "별도 서비스 신청 → 신청 상세 → 견적 수락 → 결제와 이용 상태")
service_shot = SCREEN / "service-paid-viewport.png"
if service_shot.exists():
    image(service_shot, M, 146, CW, 289)
    para("실제 로컬 UI: VAT 포함 55,000원 모의 수납 완료, 지급 크레딧 0, 업무 진행 중을 확인했습니다.", M, 447, CW, 9.3, 14, MUTED)
else:
    text("실제 로컬 수행 결과", M, 155, 13, True)
    para("사람의 파일 검토\n수락 금액 55,000원 · VAT 포함\n모의 수납 완료 · 크레딧 지급 0\n담당자 변경 후 업무 진행 중", M, 190, CW, 11, 21)
y = steps([
    "대상 프로젝트와 요청 내용을 적어 신청합니다. 담당자가 발행한 견적의 금액·VAT·범위·제외 항목·기한을 확인해 수락합니다.",
    "결제와 이용 상태에서 결제를 요청합니다. 이번 환경에서는 ‘모의 결제 승인 확인’을 사용했으며 실제 Toss 청구를 하지 않았습니다.",
    "결제 상태 재조회로 서버의 수납 상태를 확인합니다. 업무 진행은 담당자가 별도로 기록합니다. 불명확한 결과를 결제 성공으로 표시하지 않습니다.",
], 494)
callout("업무 시작 후 자동 환불 제한", "일회 서비스는 업무 시작 전의 자동 전액 환불 조건을 확인합니다. 업무 중·일부 취소·결과 불명 상태는 담당자 확인이 필요하며, 새 결제 요청을 반복하지 않습니다.", y + 8, 99, warning=True)
c.showPage()

start(3, "Pro는 자동 갱신 동의를 다시 확인", "첫 달 조건 확인 → 별도 동의 → 모의 승인 → 갱신 관리·환불")
image(SCREEN / "pilot-refunded-viewport.png", M, 146, 252, 292)
text("수락 당시 조건", 313, 155, 13, True)
y = para("첫 달 99,000원\n다음 달부터 108,900원 / 월\n모두 VAT 포함\n월 1,500크레딧 · 3좌석", 313, 190, 240, 11, 21)
para("왼쪽은 실제 모의 전액 환불 완료 화면입니다. 수락 당시 계약과 현재 결제·구독 상태를 나누어 표시합니다.", 313, y + 24, 240, 10, 16)
y = steps([
    "첫 결제액·다음 결제액·자동 갱신을 확인하는 별도 동의 전에는 결제 버튼이 비활성인 것을 실제로 확인했습니다.",
    "모의 승인 뒤 1,500크레딧·Pro 이용 상태를 확인했습니다. 다음 갱신 중단은 현재 이용 기간을 바로 없애지 않습니다.",
    "사용하지 않은 지급분을 모의 전액 환불하자 구독이 취소됐습니다. 체험 30크레딧과 과거 견적·결제·환불 기록은 남았습니다.",
], 465)
callout("과거 조건을 현재 갱신 예정으로 읽지 마세요", "현재 상태는 구독·잔액·갱신 관리에서 확인합니다. 실제 Toss 승인·빌링키·갱신·환불·웹훅의 실검증은 이번 모의 수행과 별개입니다.", y + 5, 91, warning=True)
c.showPage()

start(4, "가공값을 입력하고 문구 공간 확보", "편집기 구조 도구 → 개봉부·지퍼·노치 / 걸이 구멍")
image(SCREEN / "hanger-settings-viewport.png", M, 146, 242, 294)
text("이번 시험 파우치의 실제 값", 302, 155, 12.5, True)
para("재단 160×230mm\n원형 구멍 Ø6mm / X80·Y15\n헤더 30mm\n지퍼 중심 Y35 / 대역 6mm\n뜯는 위치 Y24mm\n양측 U 노치 3×4mm\n외곽 도련 사방 3mm", 302, 192, 251, 10.5, 24)
para("왼쪽은 실제 구멍 설정 화면 전체입니다. 좌표 원점은 면의 왼쪽 위, 단위는 mm입니다.", M, 451, CW, 9.3, 14, MUTED)
y = steps([
    "가공 도구의 허용 범위 안에서 구멍·노치·지퍼를 설정합니다. 이 문서의 값은 시험값이며 제조사 공통 규격이 아닙니다.",
    "중요 문구가 가공부와 겹치면 위치를 보완합니다. 이번 작업은 앞·뒷면 상단 문구를 Y55mm로 이동했습니다.",
    "저장 후 현재 디자인을 검수하고 무료 CMYK 출력 시험을 요청합니다. 실제 제작에는 제조사의 정확한 가공 명세와 승인이 별도로 필요합니다.",
], 503)
callout("실제 컷과 안내선은 다릅니다", "구멍·노치는 비인쇄 영역과 CUT에 반영합니다. 개봉 참고선·지퍼·실링은 PROCESS 안내입니다. 참고선을 미싱·레이저 절취 가공 명령으로 사용하지 않습니다.", y + 4, 90, warning=True)
c.showPage()

start(5, "CUT와 PROCESS를 별도 파일로", "원본 출력의 렌더 / 같은 작업의 앞면 / 무료 시험·제작 사용 불가")
image(FILES / "cut-1.png", M, 149, 245, 365)
image(FILES / "process-1.png", M + 266, 149, 245, 365)
text("CUT · 외곽, 원형 타공, 양측 U 노치", M, 528, 9.6, True)
text("PROCESS · 실링, 개봉부, 지퍼 안내", M + 266, 528, 9.6, True)
y = steps([
    "cut.pdf에서 외곽선이 노치 구간으로 실제 이어지고 원형 타공이 별도 경로인지 확인합니다. 아트의 해당 영역도 인쇄하지 않도록 반영합니다.",
    "process.pdf는 배치 참고 자료입니다. finishing.json의 좌표·동결 명세와 제조사 조건을 함께 확인해야 합니다.",
    "fold.pdf는 접힘선만 구분합니다. 이번 분리 삼방 면에는 접힘선이 없어 시험 안내만 보이는 것이 정상입니다.",
], 567)
para("총 8페이지의 앞·뒷면을 Poppler로 렌더해 전수 확인했습니다. 합성 ICC 시험은 실제 인쇄 색상 교정이나 실물 가공 정밀도 검증을 대신하지 않습니다.", M, y + 4, CW, 9.3, 14, MUTED)
c.showPage()

start(6, "전달 파일과 아직 별도인 확인", "완료 상태·파일 해시·페이지 검수와 실제 제조 승인을 구분합니다")
text("실제 UI 시험 번들", M, 149, 13, True)
para(f"작업 {evidence['job_id']}\n8개 파일 · {evidence['bytes']:,}바이트 · 0크레딧\n프로젝트 {evidence['project_id']}", M, 180, CW, 10, 18)
entries = [
    ("production.pdf", "CMYK·문구 윤곽선·가공 비인쇄 영역의 시험 아트"),
    ("cut.pdf / fold.pdf", "재단 경로 / 접힘 경로"),
    ("process.pdf / finishing.json", "가공 배치 안내 / 실제 좌표와 물리 명세"),
    ("manifest.json / preflight.json", "파일 해시 / 검수 기록"),
    ("preview.png", "아트 미리보기"),
]
for i, (name, label) in enumerate(entries):
    top = 260 + i * 43
    c.setFillColor(colors.HexColor("#EAF0E6") if i % 2 == 0 else BG)
    c.rect(M, H - top - 43, CW, 43, fill=1, stroke=0)
    text(name, M + 10, top + 7, 9.1, True)
    text(label, M + 10, top + 23, 9.1)
para("SHA-256: " + evidence['sha256'], M, 485, CW, 8.7, 13, MUTED)
callout("실제 제작의 별도 입력", "제조사·재질·치수·가공값·ICC 프로필·증빙의 일치와 승인이 필요합니다. 관리자에서 가공값을 가져오는 것은 값 복사이며, 제조사 승인과 최종 입고 승인을 만들어 주지 않습니다.", 535, 105, warning=True)
para("지원 밖: 임의 SVG 칼선, 비원형 걸이 구멍, 외곽 둥근 모서리, 미싱·레이저 절취, 곡선 접힘, PDF/X 인증, 별색·화이트판·오버프린트·반투명.", M, 657, CW, 9.5, 15)
para("이 기록에는 운영 배포 확인, 실제 결제, 실제 사람의 서비스 제공, 제조사 입고 UI의 후속 실증을 포함하지 않습니다. 전체 요구사항 완료 선언이 아닙니다.", M, 713, CW, 9.5, 15, MUTED)
c.showPage()

# Keep pages 1-6 as the original local record; later evidence is an appendix.
start(7, "입고 기록의 출처를 확인", "부록 · 파일 이력 → 제조사 입고 기록 → 입고 기록 보기 / 로컬 후속 확인")
image(SCREEN / "intake-summary-viewport.png", M, 146, 245, 280)
image(SCREEN / "intake-history-viewport.png", M + 266, 146, 245, 280)
para("출력 이력: 제조사 기록 없음 / 내부 시험 1건", M, 438, 245, 9.2, 14, bold=True)
para("상세 이력: 내부 시험 기록 / 입고 수락", M + 266, 438, 245, 9.2, 14, bold=True)
para("기록 ID 087a33b5-624c-472d-96dd-22febd31b616", M, 480, CW, 9.2, 14, MUTED)
y = steps([
    "앞의 시험 ZIP과 같은 출력본에 기본 출처 ‘내부 시험’으로 수락 기록을 저장했습니다. 제조사 표시에는 ‘Phoenix 내부 QA · 제조사 회신 아님’을 명시했습니다.",
    "파일 이력에서 제조사 회신은 ‘기록 없음’, 내부 시험은 ‘입고 수락 기록 · 1건’으로 분리되는 것을 실제 화면에서 확인했습니다.",
    "입고 기록 보기를 열어 기록 ID·출처·수락 상태·8개 파일과 8페이지 확인 메모를 읽었습니다. 연결된 증빙은 없으며 실제 제조사 회신이나 승인을 기록하지 않았습니다.",
], 521)
callout("이 부록은 내부 시험 기록의 실제 UI 확인", "앞 6쪽은 최초 수행 기록을 보존했습니다. 이 부록으로 입고 기록 메뉴의 로컬 실증만 추가합니다. 운영 배포·실제 제조사 입고 승인·외부 증빙 검증은 포함하지 않습니다.", y + 4, 99, warning=True)
c.showPage()

# The recovery clock was deliberately advanced in an isolated QA environment.
recovery = json.loads((FILES / "recovery/verification.json").read_text(encoding="utf-8"))
assert recovery["status"] == "succeeded" and recovery["retry_ui_status"] == "passed"
assert recovery["original_pdf_sha256"] == recovery["recovered_pdf_sha256"]
assert recovery["same_frozen_snapshot"] is True and recovery["paid_reservation"] is False
assert recovery["production_files_touched"] == 0 and recovery["accelerated_clock_seconds"] == 301
start(8, "손상된 무료 출력은 같은 작업으로 재시도", "후속 부록 · 파일 이력 → 파일 준비 다시 시도 → 다운로드 / 격리 로컬 시험")
image(SCREEN / "export-loss-viewport.png", M, 146, 245, 280)
image(SCREEN / "export-recovered-viewport.png", M + 266, 146, 245, 280)
para("손상 확인 후: 사용 불가 / 다운로드 중지", M, 438, 245, 9.2, 14, bold=True)
para("UI 재시도 후: 준비 완료 / 다운로드 복귀", M + 266, 438, 245, 9.2, 14, bold=True)
para("같은 작업 " + recovery["job_id"], M, 477, CW, 9.2, 14, MUTED)
y = steps([
    "검증된 무료 검토 PDF를 격리 로컬 저장소에서만 손상시켰습니다. 시험 시각 0초와 301초에 무결성을 확인해 실패·사용 불가로 전환했습니다. 실제로 5분을 기다린 시험은 아닙니다.",
    "파일 준비 다시 시도를 실제 UI에서 실행했습니다. 동일 작업이 완료로 돌아오고 다운로드가 다시 표시됐습니다. 동결된 디자인 저장본은 바뀌지 않았습니다.",
    f"재생성된 PDF {recovery['recovered_pdf_bytes']:,}바이트의 SHA-256은 손상 전 원본과 정확히 같습니다. 유료 예약·유료 API 호출은 없었고 운영 파일은 건드리지 않았습니다.",
], 516)
para("복구 PDF SHA-256: " + recovery["recovered_pdf_sha256"], M, y + 4, CW, 8.3, 13, MUTED)
callout("로컬 무료 출력 복구의 실제 UI 근거", "기존 1~7쪽은 그대로 보존했습니다. 이 시험은 유료 출력 보상이나 운영 장애 복구 실증을 뜻하지 않습니다. 파일 준비 성공과 제조사 입고 승인은 계속 별개입니다.", y + 45, 96, warning=True)
c.showPage()
c.save()

result = {
    "manual": str(TARGET.relative_to(ROOT)).replace("\\", "/"),
    "pages": 8, "preserved_original_pages": [1, 2, 3, 4, 5, 6, 7], "bytes": TARGET.stat().st_size,
    "sha256": hashlib.sha256(TARGET.read_bytes()).hexdigest(),
    "source_document": "docs/checkout-finishing-workflow.ko.md",
    "source_verification": "output/checkout-finishing-ui/verification.json",
    "recovery_verification": "output/checkout-finishing-ui/recovery/verification.json",
    "scope": "isolated local UI, mock payments, free synthetic-ICC test output; no deployment/manufacturer approval claim",
    "visual_status": "awaiting_visual_review",
    "images": [{"path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(ASSETS)],
}
QA.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"path": str(TARGET), "pages": result["pages"], "bytes": result["bytes"], "sha256": result["sha256"]}, ensure_ascii=False))
