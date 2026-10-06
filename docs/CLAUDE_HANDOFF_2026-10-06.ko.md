# Phoenix Package Design — 인수인계 (2026-09-24~10-06, 개발방-3 → 개발방-4)

작성: 2026-10-06, Claude Code 세션 "phoenix package design 개발방-3". 이전 인수인계 [2026-09-23](CLAUDE_HANDOFF_2026-09-23.ko.md)의 후속이다.

**다음 방은 Claude 계정이 바뀐다.** 로컬 경로·폴더·파일·저장소·서버·키 파일 위치는 그대로다. 이전 계정의 Claude 메모리(`C:\Users\naraj\.claude\projects\...\memory`)가 새 계정에서 보인다는 보장이 없으므로, 그 메모리에 있던 내용을 이 문서 §6에 모두 옮겨 두었다. **이 문서만 읽어도 일할 수 있게 썼다.** 비밀값은 없다.

## 0. 첫 10분

1. 이 파일 → [2026-09-23](CLAUDE_HANDOFF_2026-09-23.ko.md) → [2026-09-22](CLAUDE_HANDOFF_2026-09-22.ko.md) 순으로 읽는다. 앞 두 개는 필요한 절만 봐도 된다.
2. 작업 폴더는 **`C:\codex\agent\package design` 하나뿐이다.**
3. 상태: **main = `47b29f0`(+이 문서 PR), 열린 PR 없음.** PR #13~#29 병합·운영 배포 완료.
4. 미커밋 변경: `apps/web/next-env.d.ts`(Next 개발 서버가 자동으로 고침 — 커밋하지 말고 `git checkout`으로 되돌림), `output/`(예전 보고서, 손대지 않음).
5. **계정이 바뀌었으니 먼저 확인할 것** (§7):
   ```powershell
   gh auth status            # 기대: github.com account phoenixai-sw
   git config user.name      # 기대: phoenixai-sw / phoenixai.sw@gmail.com
   npx vercel whoami         # 배포 확인용. 로그인 안 돼 있으면 사용자에게 요청
   ```
6. **사용자가 결정해 줘야 시작할 수 있는 일이 있다** — §3 "컨펌 대기". 먼저 그것부터 물어본다.

## 1. 개발방-3에서 한 일 (PR #29, 병합·운영 배포 완료)

### 1-1. 요금제를 셀프서비스 수준으로 내림

경쟁사 Packify보다 크레딧 단가가 30~60배 비쌌다(§6-5). 사용자 승인("그대의 의견대로")을 받아 바꿨다.

| | 이전 | 지금 (VAT 포함/월) |
|---|---|---|
| Starter | ₩330,000 · 500크레딧 | **₩29,000 · 300크레딧** · 1석 |
| Pro | ₩550,000 · 1,500 | **₩79,000 · 1,000** · 3석 |
| Partner | ₩990,000 · 4,500 | **₩199,000 · 3,000** · 5석 |
| 가입 체험 | 30크레딧 | **100크레딧** (14일, 표준 생성·수정 전용) |
| 첫 달 Pro 모집 | ₩99,000 | **₩39,000** (새 Pro 정상가보다 비싸면 안 되므로) |
| 추가 충전 | 500=₩42,900 / 1,000=₩75,900 | 그대로 (활성 구독 필요) |

- 원가: AI 1장 약 $0.14 ≈ ₩196 → 크레딧(10/장)당 약 ₩20. Starter 크레딧당 ₩97, Pro ₩79, Partner ₩66.
- 기존 구독은 **동의한 요금표 스냅숏으로 갱신**된다(원래 있던 구조). 로컬 테스트 구독(₩330,000)이 그대로 유지되고 화면에 "기존 구독에서 동의한 조건" 안내가 나오는 것을 확인했다.
- 운영 DB에 게시된 요금 정책이 없어 `config/pricing.seed.json`이 그대로 적용된다. 운영 `/v1/pricing` 버전 = `2026-09-25-tiers-29-79-199` 확인.
- 관련 파일: `config/pricing.seed.json`, `services/api/operations/schemas.py`(체험 크레딧 `Literal[30]` → 1~200 정수), `services/api/service_orders/catalog.py`·`checkout.py`(`PILOT_AMOUNT_KRW=39000`), `apps/web/src/components/service-orders.tsx`.

### 1-2. **화면에서 잡은 버그: 체험 크레딧을 바꾸면 기존 가입자 전원이 로그인 불가**

- `_grant()`는 같은 `grant_key`에 다른 금액이 오면 409 `GRANT_CONFLICT`("기존 지급 내역과 요청이 다릅니다")로 거부한다.
- `ensure_trial()`은 **로그인·크레딧 조회 때마다** 불린다. 30을 받은 계정에 100을 주려다 매번 거부 → 로그인 화면에서 멈춤.
- 수정: `services/api/billing/service.py` `ensure_trial()`이 `signup-trial` 버킷이 이미 있으면 그대로 둔다. 시험 `test_trial_size_change_keeps_existing_trials_and_lets_them_sign_in`.
- 운영 배포 후 사용자 계정으로 로그인·크레딧 조회 정상 확인.
- **앞으로 크레딧 수를 바꿀 때 같은 함정을 다시 볼 것.** 월 지급(`grant_key`가 주기마다 다름)은 괜찮지만, 고정 키로 한 번 주는 지급은 모두 이 문제를 가진다.

### 1-3. 시험 구조: 크레딧 수를 시험에서 고정

원장 시험 수십 개가 "체험 30, Starter 500/Pro 1,500/Partner 4,500"을 전제로 짜여 있다. 요금을 바꿀 때마다 시험을 고치지 않도록:
- `services/api/conftest.py`가 시험 중에만 이 크레딧 수를 고정한다(금액은 실제 seed 값).
- 실제 출시 값은 `services/api/tests/test_pricing_seed.py`가 검사한다. **요금을 바꾸면 이 파일만 고치면 된다.**
- 요금 정책 시험(`test_operation_policies_billing.py`)은 가격을 2배로 올려 게시하는데, VAT 110% 정확 검사 때문에 공급가를 `round(inc/1.1)`로 다시 계산하게 고쳤다.

### 1-4. 표준 파우치 규격 6종 등록 (코드 수정 없음, 데이터)

| 규격 | 펼친 바닥(`bottom_mm`) | 운영 등록 ID |
|---|---|---|
| 스탠드 100×170 · 바닥 30mm 접힘 | 60 | `5316812a-3653-4ef9-a73c-eed55881378a` |
| 스탠드 130×200 · 바닥 40mm 접힘 | 80 | `e09f3405-e9fc-4ea7-a3c0-195e427edf52` |
| 스탠드 150×220 · 바닥 40mm 접힘 | 80 | `4428bc66-695f-4f0d-a04f-f5934ccafc6f` |
| 스탠드 180×260 · 바닥 50mm 접힘 | 100 | `ae698c81-925a-4614-8643-efaea84f7cc8` |
| 스탠드 200×300 · 바닥 50mm 접힘 | 100 | `59cf75b4-6431-4f4b-b540-762e14701905` |
| 삼방 실링 · 실링 10mm (폭 60~400 × 높이 80~500 범위형) | — | `e34351f4-c047-46f5-bc61-ea63a34cdd37` |

- 근거: 국내 기성품 업체 여러 곳(팩포유·스타패키지 등)이 같은 치수로 판다(10×17+3 … 20×30+5cm).
- **가정 하나:** 업체 표기 "+4cm"는 **접힌 바닥 깊이**로 보고 펼친 거싯을 2배로 넣었다. 실제 인쇄소가 정해지면 그 업체 도면과 맞춰 봐야 한다.
- 안전영역: 앞·뒤 좌우 12 / 위 25 / 아래 15mm, 바닥 10mm.
- **위 여백은 처음에 35mm였다가 25mm로 줄였다.** 새 프로젝트의 기본 문구(뒷면 제목)가 높이의 16% 위치에 놓여, 100×170에서 27mm라 35mm 안전영역 밖으로 나가 "적용"이 막혔다. 화면에서만 보이는 문제였다. 시험 `test_new_project_default_text_fits_the_standard_safe_area`가 막는다.
- 파일: `scripts/build-standard-structures.py`(생성기) → `fixtures/structures/*.json`, 관리자 등록 본문은 `fixtures/structures/standard-registrations.json`. 큰 것부터 등록해야 고객 목록에 작은 것부터 보인다(목록이 최신순).
- `manufacturer` = "표준 규격 · 제조사 확인 전", `is_demo: false`, `review_available: true`, 상태 `draft`(검토용). 인쇄 의뢰본은 `manufacturer_approval: false`로 정직하게 나온다.
- **로컬 화면에서 끝까지 확인**: 관리자 등록 → 새 프로젝트 → "등록 구조 검토"에서 고르기 → 미리보기 → 적용 → 인쇄 의뢰본 ZIP.
  - 스탠드 130×200: 전개도 130×480mm, `cut.pdf` 8선분 닫힌 외곽, `fold.pdf` 3선, 도련 3mm, ICC CMYK, 글꼴 윤곽선, preflight 이슈 0.
  - 삼방 실링 230×310: 면별 페이지, 칼선 면마다 4선분, preflight 이슈 0.
- **운영**: 6종 등록, 고객 목록·미리보기가 로컬과 같은 값(130×200 → 130×480, 안전 106×160). 운영에서는 테스트 프로젝트를 만들지 않아 "적용 → 인쇄 의뢰본"은 운영에서 실행하지 않았다.
- 로컬에는 35mm 판 5개가 남아 있다. 지우지 않고 `review_available=false` + 이름 뒤 "[철회: 윗 여백 35mm 판]"으로 숨겼다(초안은 철회 API가 없음).

### 1-5. 파우치 설비 편차 조사 결론

- **가로×세로는 업체끼리 같다.** 바닥 깊이·실링 폭(5~10mm)·지퍼 위치(위에서 25~38mm)·노치는 업체마다 다르다. 재단 오차 최대 약 3mm.
- 파우치 업체 상당수가 "우리 템플릿에 맞춰 달라"고 한다.
- Packify도 파우치 칼선을 지원한다고 적혀 있으나 템플릿 목록이 공개돼 있지 않아 박스 위주인지 확인 못 함.
- 결론: 표준 규격을 미리 넣는 것은 시장 관행에 맞다. `manufacturer_approval: false`와 "제조사 확인 전" 표시는 유지.

### 1-6. 검증

- pytest 989건(전체: `services/api/tests`, `services/api/billing/tests`, `tests/geometry_pdf`) 통과, 약 28분.
- `npm test` 116건, `npm run typecheck`, `npm run build`, 계약 검사 통과.
- 이번 방 유료 AI 호출: **0회.**

## 2. 화면 작업에서 배운 함정 (다음 방도 그대로 겪는다)

| 함정 | 해결 |
|---|---|
| 브라우저 창이 좁으면 편집기 왼쪽 도구 막대가 숨는다("등록 구조 검토" 버튼이 화면 밖) | 위쪽 탭 "요소·레이어"를 누르면 도구 목록이 나온다 |
| `form_input`으로 textarea에 JSON을 넣으면 `[object Object]`가 된다 | 클릭 → `ctrl+a Delete` → `type`으로 입력 |
| `form_input`으로 `<select>`를 바꾸면 React 상태가 안 바뀐다 | select 클릭 → 화살표 키 → Enter |
| 관리자 API를 페이지에서 직접 부를 때 403 | `fetch('/api/v1/me')`의 `data.csrf_token`을 `X-CSRF-Token` 헤더로 |
| 같은 초에 여러 개 등록하면 목록 순서가 뒤섞일 수 있다 | 등록 사이 1초 쉼 |
| 관리자 등록 화면의 "시험 예시 구조" 체크가 기본 켜짐 | 실제 규격이면 끄고, "사용자가 검토용으로 선택하도록 공개"는 켠다 |
| PowerShell 도구로 `scripts/dev.ps1` 실행 시 alembic stderr 때문에 실패로 표시 | Bash에서 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1` (끌 때 `-Stop`) |
| 전체 pytest는 10분을 넘는다 | 백그라운드 실행. `-x`를 빼고 결과 파일로 받기 |
| `npm run build`가 개발 서버와 `.next`를 같이 쓴다 | 개발 서버를 끄고 빌드한 뒤 다시 켠다 |
| 로컬 DB는 Postgres가 아니라 SQLite `.data/phoenix.db` | 테이블 이름: `registry_versions`, `operation_policy_versions`, `operation_active_policies`, `jobs` |
| 운영 DB 읽기는 이번에 안전장치가 막았다 | 운영 상태는 공개 API(`/v1/pricing` 등)나 사용자 로그인 화면으로 확인 |

## 3. **컨펌 대기 — 다음 방이 가장 먼저 할 일**

### 3-1. 사용자가 준 과제

사용자가 "gpt-6 sol로 조사한 자료"라며 긴 지시문을 줬다. 요지:
> AI가 만든(또는 외부) 패키지 이미지의 글자·로고·그림을 레이어로 나눠 고치고, 저장 후 다시 열어 재수정할 수 있게. Packify의 쉬운 흐름은 벤치마킹하되 경쟁력은 정확한 한글 수정과 편집 가능한 결과물. 현황 조사 → 대표 샘플 품질 시험 → 우선순위 → 구현·검증 → 보고.

그리고 사용자 본인의 핵심 요구(원문 취지):
> **"어도비 포토샵·일러스트레이터·익스프레스 수준으로, AI가 만든 이미지의 텍스트와 이미지를 레이어별로 구분해서 어색하지 않게 수정할 수 있는가? 캔바는 겨우 레이어 구분하는데 어도비보다 못하다. 나는 어도비 수준을 원한다."**

### 3-2. 개발방-3의 분석 (코드로 확인함)

**이미 되는 것** — 다시 만들지 말 것:
- OCR(브라우저 tesseract) → 원문·영역 교정 → 확정해야만 적용(`image-text-tools.tsx`)
- 글자 덮기 두 방식: ① 단색 덮개(무료) ② AI 글자 제거 후 배경 복원(10크레딧, 원가 $0.14). 둘 다 새 문구는 **진짜 텍스트 객체**(`packages/editor/src/image-tools.ts` `applyImageText`)
- 새 문구 크기를 실제 글꼴 폭으로 맞춤(`fitFontSizePt`, #26), 영역 이동 시 색·크기 재계산(#25)
- 텍스트 속성: 크기·굵기(400/700)·색·정렬·자간·행간·회전·위치, 브랜드 글꼴 등록
- 선택 도구 넷(브러시·올가미·같은 색 자동·사각형) → 부분 수정 / 피사체 따기(투명 PNG 레이어) → 레이어 병합
- 저장·리비전·편집 ZIP(`editable_exports.py`, scene.json 전체 보존)·검토 PDF
- 새 디자인 AI 생성은 지시문에서 "글자·로고·바코드를 그리지 말라"고 하고 문구는 텍스트 객체로 따로 둔다(`image_provider.py` `design_prompt`)
- 이미지 파일끼리의 파생 관계는 서버에 기록(`reference_asset_id`, `edit_mode`, `parent_asset_id`)

**어도비 수준에 못 미치는 곳 = 어색함의 원인:**

| 원인 | 지금 | 예시 |
|---|---|---|
| 새 글자 모양이 원래와 다름 | 본고딕 400/700만, **외곽선·그림자·그라데이션·곡선 배치 없음**, 글꼴 일치 없음 | 금색 붓글씨+그림자 "유기농 그래놀라"를 고치면 반듯한 고딕이 들어감 |
| 단색 덮개 | 질감 배경 위에 평평한 네모가 보임 | 크라프트지 위 베이지 사각형 |
| 경계 깃털 없음 | AI로 지운 자리·따낸 로고 가장자리에 이음선 | #21에서 안내 문구로만 보완 |
| 물체를 옮기면 원본이 남음 | "피사체 따기"는 원본 위에 복사본을 둠. 원래 자리를 지우려면 "부분 수정"을 따로 한 번 더 | 로고를 오른쪽으로 끌면 로고가 둘 |
| 레이어 마스크 없음 | 한 번 딴 모양을 나중에 다듬을 수 없음 | — |
| 텍스트↔덮개 짝 정보 없음 | 덮개만 지우면 원래 글자가 드러나 겹치는데 경고 없음. 레이어 목록에서 "OCR로 만든 텍스트"가 구분 안 됨 | — |
| 새 디자인도 층이 2개뿐 | AI가 배경+일러스트+제품 그림을 **한 장**으로 그림 | 그래놀라 그릇만 옮길 수 없음 |
| 곡면·반사·투명·엠보스 | 한 번도 평가 안 함 | — |

**정직한 한계(사용자에게 이미 설명함):** 한 장짜리 이미지를 원본 PSD처럼 완벽히 복원하는 것은 어도비도 못 한다(가려진 뒤쪽 픽셀이 없어서 AI가 새로 그려야 함). 어도비가 앞서는 것은 **편집 도구의 자연스러움**이고, 그건 만들 수 있다. 붓글씨·손글씨·3D 글자를 똑같이 재현하는 것은 비슷한 글꼴 파일이 없으면 어도비도 못 한다. 어도비·캔바 기능 설명은 공개 소개 기준이며 직접 써 보지 않았다.

**어도비 수준에 가까워지는 두 길:**
- **길 A (새 디자인, 가장 확실):** 생성할 때부터 배경 / 주요 일러스트(투명 PNG) / 텍스트를 따로 만든다. 쪼갤 필요가 없어 어색함이 안 생긴다. AI 호출이 장당 1회 → 2~3회, **원가 $0.14 → $0.3~0.4** (요금·크레딧 재검토 필요).
- **길 B (이미 완성된 한 장):** "자동 레이어 분해" — OCR 전체 → 사용자 교정 → AI 1회로 글자·선택 물체를 모두 지운 깨끗한 배경판 → AI 1회로 물체 따기 → 배경/로고/그림/텍스트 층 + 출처 기록. 면당 $0.3~0.4. 화면에 "자동 재구성 — 원본 PSD 아님" 표시.
- **공통(AI 비용 없음):** 글자 효과(외곽선·그림자·그라데이션·굵기 확대), 부드러운 경계, 레이어 마스크, 텍스트↔덮개 짝 보호·출처 표시, "따면서 원래 자리 채우기".

### 3-3. 사용자에게 올린 제안 (답 아직 없음)

1. **1단계 · 실제 어색함 측정** — 샘플 4종(평면 라벨·파우치·박스 앞면·곡면 병)을 AI로 직접 생성(권리 문제 없음), 각 샘플에서 글자 바꾸기(덮개·AI 제거 둘 다)와 로고 옮기기, 두 번째 수정까지. 확대 사진·시간·호출 수·비용 기록. **최대 20회, 약 $2.8.**
2. **2단계 · 무료 편집 도구 보강** — 1단계에서 가장 어색했던 순서대로(예상: 글자 효과 → 부드러운 경계 → 원래 자리 채우기 → 짝 보호·출처).
3. **3단계 · 길 A** (원가 상승, 요금 결정 필요)
4. **4단계 · 길 B** (가장 크고 어려움, 1~3 결과 보고 결정)
- 하지 않을 것: Packify 로그인 비교, "모든 픽셀 완벽 레이어화" 선언.

**사용자가 답해야 할 3가지:** ① 이 순서로 갈지 ② 1단계 유료 최대 20회 / 약 $2.8 승인 ③ 장당 원가 $0.3~0.4로 오르는 방향 수용 여부(3단계 전에 다시 물어도 됨).

**유료 호출 승인은 이전 승인을 재사용하지 말 것.** 새 방에서 다시 받는다.

## 4. 그 밖에 남은 일

1. **운영 결제 활성화 여부** — 지금 `billing_provider=disabled`. 다섯 조건 모두 필요(`billing/payments.py`): 운영 환경, 라이브 시크릿 키, `BILLING_POLICY_APPROVED`, `LIVE_BILLING_ENABLED`, 코드에 `Literal[False]`로 잠긴 `pricing().live_billing_enabled`. 토스 정식 계약이 먼저다. 사업 판단이므로 사용자에게 묻는다.
2. **표준 규격 바닥 깊이 가정** — 실제 인쇄소가 정해지면 그 업체 도면과 맞춘다(§1-4).
3. (선택) 선택 도구 한계: 도넛 모양 안쪽까지 선택됨(서버가 닫힌 고리 하나만 래스터화), 떨어진 같은 색 덩어리는 따로 클릭, `cutout` 피사체 지정은 글로.
4. (선택) 프로젝트 삭제 기능 없음(`EXECUTABLE_SCOPES`는 asset·export뿐).
5. 문서 정리: `docs/service-checkout.ko.md`·`docs/checkout-finishing-workflow.ko.md`에는 옛 요금(99,000원·108,900원 등)이 그때 기록으로 남아 있다. 사용자는 문서 늘리기를 싫어하니 필요할 때만.

## 5. 남겨 둔 데이터 (사용자가 "그냥 둬"라고 함)

- 운영: QA 프로젝트 "QA · ICC 인쇄 의뢰본 확인 0923"
- 로컬: QA 프로젝트 여러 개(이번에 "QA · 표준 규격 130×200 확인 0925" `fd7b8b91-…`, "QA · 삼방 실링 10mm 표준 확인 0925" `09ed605f-…` 추가), 테스트 크레딧(사용 가능 1,350), 테스트 Starter 구독(₩330,000 옛 조건), 숨긴 35mm 규격 5개.

## 6. 이전 계정 메모리에 있던 내용 (새 계정에서 메모리가 안 보일 수 있어 옮김)

### 6-1. 사용자 스타일
- **짧고 쉬운 한국어, 결과 먼저.** "된 것 / 안 된 것"을 몇 줄로. 반복 보고·문서 늘리기·어려운 설명을 싫어한다.
- **덜 끝난 걸 완료라고 하지 않는다.** 소프트웨어 일과 외부 자료가 필요한 일(ICC, 토스 키, 제조사 승인)을 구분해 말한다.
- **기능을 만들거나 고쳤으면 로컬 화면에서 끝까지 눌러본다.** 개발방-2의 버그 13개, 개발방-3의 버그 2개(로그인 불가, 35mm 안전영역) 모두 화면 조작에서 나왔다.
- 이미 구현된 기능은 다시 만들지 않는다. 기존 데이터·결과물 보존.

### 6-2. 유료 AI
- **장당 약 $0.14** (면 전체를 300ppi, 약 2500×3300px로 요청하기 때문). 옛 메모의 $0.08은 틀림.
- **사용자가 건별로 승인한다. 숫자를 먼저 말하고, 끝나면 실제 합계를 보고한다.** 실제 값은 로컬 `.data/phoenix.db`의 `provider_attempts.cost_usd`(API 키에 사용량 조회 권한 없음).
- 유료 호출은 **로컬 앱**에서 `.env`를 `AI_PROVIDER=openai`로 바꾸고 키 파일 `C:\codex\openai & biteplus API.txt`의 키를 넣어 실행. 끝나면 **반드시 `AI_PROVIDER=fixture`로 되돌리고 키를 지운다.** 키는 출력·커밋 금지.
- 누적: 9/18 9회 ≈ $1.07(운영), 9/23 7회 $0.97(로컬), 개발방-3 0회.

### 6-3. 로그인은 사용자만 할 수 있다
- 앱은 구글 로그인 전용. Claude는 세션 행을 DB에 만들 수도, 구글 버튼을 누를 수도 없다(안전장치 두 개가 막음 — 앱 버그 아님).
- 화면 검증이 필요하면 **브라우저 창을 `http://localhost:3000/auth`(또는 운영 `https://phoenix-packaging.vercel.app/auth?next=%2Fadmin`)로 띄우고 사용자에게 로그인을 부탁한다.** 기다리는 동안 서버·시험 작업을 한다.
- PR 병합(`gh pr merge`), 결제 확인 버튼, 파일 선택 창, 구글 클라우드 콘솔도 사용자가 한다.

### 6-4. 그 밖의 함정
- **토스 테스트 mId는 `tvivarepublica`** (`tosspayments` 아님). 로컬 `.env`의 `TOSS_MERCHANT_ID`가 틀리면 승인된 결제를 서버가 `PAYMENT_ACCOUNT_MISMATCH`로 거부한다. `.env`는 커밋하지 않으므로 새로 설정하면 또 틀린다.
- **운영에서만 터지는 canvas 오염**: 운영 자산 URL은 Supabase 서명 URL로 307 리디렉션된다. `<img>`를 canvas에 그리면 `getImageData()`가 `SecurityError`. 반드시 `fetch → blob → createImageBitmap`(`image-region-tools.tsx` `readSource()`, `image-text-tools.tsx` `loadSourceBlob()`).
- **글자 수정은 "이미지 속 글자 편집"**(텍스트 레이어), **"부분 수정"은 픽셀을 AI가 다시 그림**. 문구·법정 표시사항은 반드시 앞쪽 도구.
- ICC: Japan Color 2001 Coated는 총잉크량 **350%**로 등록해야 한다(300%면 어두운 색이 전부 `TOTAL_INK_LIMIT`).
- 스탠드 파우치 칼선은 코드가 아니라 **등록 구조**가 있어야 나온다(`REGISTERED_NET_REQUIRED`).

### 6-5. 경쟁사 Packify (https://www.packify.ai/, 2026-09-24 공개 페이지 확인)
- 칼선: 표준 템플릿("Print-ready dielines — Quick templates"), Business 요금제부터 내보내기. 승인된 제조사 도면을 요구하지 않음.
- 제조: `/print`는 공급업체 디렉터리(지도·업체 자가 등록). 인쇄 대행 안 함.
- 요금(연간, 월): Basic $14.9/1,000 · Pro $19.9/2,500 · Business $29.9/3,500 · Enterprise $69.9/10,000. AI 1건 10크레딧(우리와 같음). 무료 체험 100크레딧.
- 그 밖: AI 포토슈트, 로고 삽입, 라벨·영양성분표 생성기, API, 유료 디자인 서비스. "200,000+ brands"는 마케팅 문구.
- 우리 새 요금(₩29,000/300)도 크레딧 수로는 여전히 적다. **원가($0.14/장) 때문에 크레딧 수로 경쟁하지 않고 인쇄 의뢰본(CMYK·칼선·윤곽선)으로 차별화**하는 것이 개발방-3의 판단이다.

## 7. 환경·계정·경로 (값 없음)

| 항목 | 위치·값 |
|---|---|
| 작업 폴더 | `C:\codex\agent\package design` (유일) |
| 저장소 | https://github.com/phoenixai-sw/phoenix-packaging (이름은 옛 "packaging" 그대로) |
| GitHub 계정 | `phoenixai-sw` (gh keyring 로그인), git user `phoenixai-sw` / `phoenixai.sw@gmail.com` |
| 운영 웹 / API | https://phoenix-packaging.vercel.app · https://phoenix-packaging-api.vercel.app (`environment=staging`) |
| 배포 | main에 푸시(병합)하면 web/api 자동 배포. 병합은 squash + 브랜치 삭제, **사용자가 실행** |
| 로컬 서버 | `scripts/dev.ps1` (3000 web / 8000 api / worker), `-Stop`으로 끔 |
| 로컬 DB | SQLite `.data/phoenix.db`, 파일 저장소 `.data/storage/` |
| 로컬 설정 | `.env` (커밋 금지. 토스 테스트 키·`BILLING_ENCRYPTION_KEY` 포함, `AI_PROVIDER=fixture`) |
| 운영 연결 정보 | `.local/cloud-env.json` (`DATABASE_URL`을 `postgresql+psycopg://`로 바꿔 접속. 쓰기 전 백업. 이번 방에서는 읽기도 안전장치가 막음) |
| OpenAI 키 | `C:\codex\openai & biteplus API.txt` (실행 시에만 읽고 출력·커밋 금지) |
| Vercel | `npx vercel` 로그인 상태였음. 새 계정 환경에서 `npx vercel whoami`로 확인 |
| Claude 메모리(이전 계정) | `C:\Users\naraj\.claude\projects\C--codex-agent-package-design\memory\` — 내용은 §6에 옮김 |
| 이름 | 제품명 Phoenix **Package Design**. 구글 OAuth 프로젝트·클라이언트(`phoenix-packaging`), 저장소, Vercel 주소는 외부 식별자라 옛 이름 유지 |

## 8. 검사 명령

```powershell
Set-Location -LiteralPath 'C:\codex\agent\package design'
$env:APP_ENV = 'test'
& '.\.venv\Scripts\python.exe' -m pytest services/api/tests services/api/billing/tests tests/geometry_pdf -q -p no:cacheprovider   # 989건, 약 28분
npm test; npm run typecheck; npm run contracts:check; npm run build
& '.\.venv\Scripts\python.exe' packages/contracts/export_openapi.py --check
& '.\.venv\Scripts\python.exe' packages/contracts/generate.py --check
```
- 계약(DTO) 변경 시: `export_openapi.py` → `npx openapi-typescript packages/contracts/openapi.json -o packages/contracts/api.generated.ts` → CRLF→LF.
- 웹 시험은 `apps/web/tests/*.test.mjs`에 순수 함수만. 확장자 없는 상대 import 모듈은 Node ESM에서 못 불러온다.
- 표준 규격을 바꾸면: `scripts/build-standard-structures.py` 실행 → `tests/geometry_pdf/test_standard_structures.py`.
- 요금을 바꾸면: `config/pricing.seed.json` + `services/api/tests/test_pricing_seed.py` + §1-2 함정 확인.
