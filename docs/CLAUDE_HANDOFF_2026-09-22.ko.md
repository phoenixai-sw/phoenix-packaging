# Phoenix Packaging — 인수인계 (2026-09-22, 개발방-1 → 개발방-2)

작성: 2026-09-22, Claude Code(Opus 5) 세션 "phoenix package design 개발방-1". 이전 인수인계 [CLAUDE_HANDOFF_2026-09-18b.ko.md](CLAUDE_HANDOFF_2026-09-18b.ko.md)의 후속이다. 경로·저장소·서버·키 파일 위치는 그대로, 사용자도 같다. 비밀값은 없다.

## 0. 첫 10분

1. 이 파일 → [2026-09-18b](CLAUDE_HANDOFF_2026-09-18b.ko.md) → 필요하면 [2026-09-18](CLAUDE_HANDOFF_2026-09-18.ko.md) 순으로 읽는다.
2. 사용자 스타일: **짧고 쉬운 한국어, 결과 중심, 덜 끝난 걸 완료라 말하지 않기, 반복 보고·문서 늘리기 금지.**
3. 작업 폴더는 **`C:\codex\agent\package design` 하나뿐이다.** (`package design-next`는 옛 QA용, OneDrive의 `문서\ChatGPT\package design`은 인수인계 문서 사본 보관용 — 둘 다 쓰지 않는다.)
4. **main = `400f1c1`, 열린 PR 없음, 미커밋 변경 없음.** PR #13~#16 모두 병합·운영 배포 완료.
5. 로컬 서버(3000 web / 8000 api / worker)는 이 방에서 `scripts/dev.ps1`로 띄워 두었다. 꺼져 있으면 다시 띄운다.
6. 바로 할 일은 §3.

## 1. 사용자가 가장 중요하게 보는 것

> "AI로 생성된 이미지들을 **부분 편집**할 수 있어야 한다. 포토샵·일러스트레이터·캔바처럼. 레이어를 나눈다/딴다는 개념. readdy.ai의 부분 수정 방식도 참고."

조사 결론(**다시 조사하지 말 것**):
- Adobe Photoshop/Firefly API = 기업 계약·별도 과금 → 채택 안 함. Photoshop/Illustrator MCP = 로컬 앱 자동화라 웹 서비스 부적합. Polotno/Pintura SDK = 유료·중복 구조.
- **채택: 직접 구현.** 이미 쓰는 `gpt-image-2.5`가 공식으로 **마스크 인페인팅**(`mask`, 알파 채널, 원본과 같은 크기)과 **투명 배경 출력**(`background:"transparent"` + png/webp)을 지원한다. 추가 비용 없음(수정 1회 10크레딧).
- 캔바식 **텍스트 박스 편집은 이미 구현되어 있다**(`packages/editor`, mm 기반 Scene). Fabric.js/Konva로 갈아끼우지 말 것.

## 2. 2026-09-22에 병합·배포한 것 (PR #16)

| 기능 | 내용 |
| --- | --- |
| **부분 수정 `region`** | 편집기 → 이미지 → **"부분 수정·레이어 따기"** 탭. 브러시/사각형으로 바꿀 곳 표시 → 지시문 → AI가 마스크 안만 수정. 마스크 **밖 픽셀은 바이트 그대로 보존**(서버 합성), 결과로 원본 레이어 교체(실행 취소 가능) |
| **레이어 따기 `cutout`** | 피사체를 투명 배경 PNG로 분리 → 원본 **위에 새 레이어**로 추가 (포토샵 배경 제거) |
| **무료 사각형 복사** | 표시한 사각형을 crop으로 잘라 새 레이어 복사(크레딧 0, AI 호출 없음) |
| 서버 | `edit_shapes`(rect/polygon/brush, 0~1 정규화 좌표, 총 ≤3,000점)를 견적에 동결 → 서버가 픽셀 마스크로 래스터화 → `edit_mask_sha256`로 스냅샷 검증. provider `mask` 전송, cutout은 `background:transparent`. 로컬 fixture도 투명 결과 생성 |
| 로그인 | **"페이지 이동 방식으로 로그인"**(GIS `ux_mode:"redirect"`) + `/auth/google/return` 라우트. 팝업 차단 브라우저용 |
| 수정 | Vercel API 함수 `includeFiles`에 `fixtures/demo-backgrounds/**` 추가(예시 배경이 운영에 없던 문제) |

주요 파일: `services/api/image_provider.py`(shapes/mask/합성/프롬프트), `services/api/ai_routes.py`(견적 동결), `services/api/ai_jobs.py`, `services/api/contracts/images.py`, `apps/web/src/components/image-region-tools.tsx`(신규 UI), `image-preparation-tools.tsx`(탭), `apps/web/src/app/auth/page.tsx`, `apps/web/src/app/auth/google/return/route.ts`, 시험 `services/api/tests/test_image_region_edit.py`(15건).

**검증**: 전체 CI `verify` 통과 후 병합. 운영 `/v1/config`에서 `edit_modes: ['full','remove_text','region','cutout']` 확인, `/auth/google/return` 200 확인. 유료 AI 호출 없음.
**미확인**: 실제 화면에서 브러시→견적→결과 적용을 아직 못 해봤다(로컬 Google 세션 만료). §3-1이 그것이다.

## 3. 다음 담당자 할 일 (우선순위)

1. **부분 수정·레이어 따기 실사용 확인.** 로컬 로그인 → 프로젝트 편집기 → 이미지 선택 → "부분 수정·레이어 따기" 탭 → 브러시로 표시 → 견적 → (로컬은 fixture라 실제 과금 없음) 결과 적용까지. 마스크 좌표·레이어 추가·실행 취소가 의도대로인지 보고 어긋나면 고친다.
2. **레이어 병합 기능 추가.** cutout으로 만든 투명 레이어는 검토 PDF엔 나오지만 **CMYK 제작 출력은 반투명을 거부**한다(`PRINT_TRANSPARENCY_UNSUPPORTED`, `services/api/exporters/print_color.py`). "선택 레이어를 아래와 병합해 하나의 불투명 이미지로" 버튼이 필요하다. 서버에서 합성해 새 asset을 만드는 방식 권장.
3. **Google 리디렉션 URI 등록**(사용자 작업). Google Cloud Console → OAuth 클라이언트 `814727734872-a11gd2faaofr5u16du4ieod8cgnbhk5o` → 승인된 리디렉션 URI에 아래 3개. 등록 전에는 "페이지 이동 방식" 버튼이 `redirect_uri_mismatch`로 실패한다.
   - `https://phoenix-packaging.vercel.app/auth/google/return`
   - `http://localhost:3000/auth/google/return`
   - `http://127.0.0.1:3000/auth/google/return`
4. **운영 ICC 등록 → 인쇄 의뢰본 ZIP**(사용자가 이미 "진행해" 승인). `C:\Windows\System32\spool\drivers\color\JapanColor2001Coated.icc`(Adobe, 문서 임베드 허용, 앱 `inspect_icc` 통과 확인됨) → 운영 `/admin` 인쇄 엔진 관리에서 ICC 업로드 → 프로필 등록(`review_available` 켬, bleed 3mm) → 편집기에서 "인쇄 의뢰본 ZIP 만들기" → `production.pdf`(표시 없음)·`artwork-with-dieline.pdf` 확인. 막혀 있던 이유는 Claude 내장 브라우저의 Google 팝업 차단 → 3번이 풀리면 가능. 또는 사용자가 본인 Chrome에서 직접 업로드.
5. **Toss 테스트 결제 완주**(승인됨). 로컬 `.env`가 `PAYMENT_PROVIDER=toss_test`(토스 공개 문서용 테스트 키, mId `tosspayments`). `/app/billing` → 충전 주문 → 결제창 열림까지 확인됨. **카드 입력은 사용자가** 한다. 승인 후 크레딧 지급 확인. 운영에 테스트 결제를 열지는 사용자에게 먼저 물어볼 것.

## 4. 예산·키·계정 (값 없음)

- OpenAI 키 파일 `C:\codex\openai & biteplus API.txt` — 실행 시에만 읽고 절대 출력·커밋 금지. 9/18에 사용자 승인으로 이미지 11장(≈$0.90) 생성함.
- 앱 내 유료 AI 호출: 이전 승인(4회/$2) 대비 9회·$1.07 사용 → **새 유료 호출은 사용자 재승인 필요**. 로컬은 fixture(무료).
- 운영 DB 읽기: `.local/cloud-env.json`의 `DATABASE_URL`을 `postgresql+psycopg://`로 바꿔 접속. 쓰기 전 반드시 백업(`.local/platform-cloud-ops.py backup` 방식).
- `gh`·`npx vercel` 로그인 상태. main 푸시 시 web/api 자동 배포. 병합은 squash + 브랜치 삭제.
- 로컬 `.env`는 커밋하지 않는다(현재 Toss 테스트 키·`BILLING_ENCRYPTION_KEY` 포함).

## 5. 현재 운영 상태 (2026-09-22)

- 웹 https://phoenix-packaging.vercel.app · API https://phoenix-packaging-api.vercel.app (health ok, environment=staging)
- DB 마이그레이션 `0020_inquiries`까지 적용. 요금제 330,000 / 550,000 / 990,000원(VAT 포함) + Custom "별도 협의".
- 운영 결제 비활성(`billing_provider=disabled`), 제작 출력 잠금(`production_export_enabled=false`), 실제 ICC 미등록 → "인쇄 의뢰본"은 아직 안내만 뜸.
- 미해결 외부 항목: 인쇄소 도면·승인, Toss 정식 키·계약, 유료 고객.

## 6. 검사 명령

```powershell
Set-Location -LiteralPath 'C:\codex\agent\package design'
$env:APP_ENV = 'test'
& '.\.venv\Scripts\python.exe' -m pytest services/api/tests services/api/billing/tests tests/geometry_pdf -q -p no:cacheprovider   # 약 30분
npm test; npm run typecheck; npm run contracts:check; npm run build
& '.\.venv\Scripts\python.exe' packages/contracts/export_openapi.py --check
```
계약(DTO) 변경 시: `export_openapi.py` → `npx openapi-typescript packages/contracts/openapi.json -o packages/contracts/api.generated.ts` → CRLF→LF 정리. **새 모델은 참조하는 모델보다 위에 정의**할 것(PR #16 CI가 이것 때문에 한 번 실패했다).
