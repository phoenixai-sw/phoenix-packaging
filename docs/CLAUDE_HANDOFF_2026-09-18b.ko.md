# Phoenix Packaging — 인수인계 (2026-09-18 2차, Claude Code → 다음 세션)

작성: 2026-09-18 Claude Code(Opus 5). 이전 인수인계 [CLAUDE_HANDOFF_2026-09-18.ko.md](CLAUDE_HANDOFF_2026-09-18.ko.md)의 후속이다. **다음 세션은 다른 Claude 계정에서 열린다.** 경로·저장소·서버·키 파일 위치는 그대로이고, 사람(사용자)도 같다. 이 문서에 비밀값은 없다.

## 0. 다음 담당자가 첫 10분에 할 일

1. 이 파일과 이전 인수인계를 읽는다. 사용자 스타일: **짧고 쉬운 한국어, 결과 중심, 덜 끝난 걸 완료라고 말하지 않기, 반복 보고·문서 늘리기 금지.**
2. 저장소: `C:\codex\agent\package design`, 브랜치 `codex/current-platform` = main(`ce5bbd9`) 기준. `git status`로 미커밋 확인.
3. **열린 PR [#15](https://github.com/phoenixai-sw/phoenix-packaging/pull/15)** (Toss 고객키 50자 수정, 브랜치 `claude/toss-customer-key-fix`): CI `verify` 통과 확인 후 squash 병합 → Vercel 자동 배포 → `https://phoenix-packaging-api.vercel.app/v1/health` 확인. DB 마이그레이션 없음.
4. 로컬 서버(3000 web / 8000 api / worker)가 떠 있는지 확인. 없으면 `scripts/dev.ps1`(기존 프로세스 없을 때만).
5. 바로 이어서 할 일은 **§4** 두 가지(ICC 등록·인쇄 의뢰본, Toss 테스트 결제 완주).

## 1. 오늘(2026-09-18) 실제로 끝낸 것 — 모두 main 병합·운영 배포됨

| PR | 내용 | 확인 |
| --- | --- | --- |
| [#13](https://github.com/phoenixai-sw/phoenix-packaging/pull/13) | 인쇄 의뢰본(`print_request`) CMYK ZIP + 칼선 레이어 합본 `artwork-with-dieline.pdf`(CutContour·Crease 별색) · AI 참고 이미지 업로드/결과 재수정 · 이미지 속 글자 "모두 찾기→클릭→자동 채움" · 요금제 330/550/990천원 + Custom 카드 · 잔액 20% 알림 · 추천 보너스(100크레딧, 마이그레이션 0019) · 문의 폼 `/contact` + `/admin/inquiries`(0020) | Python 943·JS 96·빌드 통과. 운영 DB 0020까지 이행(이행 전 백업 `.local/backups/before-pr13-20260918`, 64테이블·496행 검증) |
| [#14](https://github.com/phoenixai-sw/phoenix-packaging/pull/14) | GPT Image 2.5 Sunburst **xhigh**로 메인 화보 1 + 콘셉트 4(오리·감귤·베리·말차) + 편집기 예시 배경 6(`fixtures/demo-backgrounds`) 재제작(11장 ≈ $0.90, 사용자 키 사용) · UI 둥글고 따뜻하게 · **피닉스AI 로고 상황별 적용**(헤더=마크, 푸터/로그인=풀 로고, 대시보드/빈 화면=마스코트, 파비콘) | 운영에서 이미지·로고·파비콘 200 확인 |

세부 기록: [print-request-release.ko.md](print-request-release.ko.md), [media-production.md](media-production.md), [spec-completion-tracker.ko.md](spec-completion-tracker.ko.md) "2026-09-18 Claude 작업분".

## 2. 예산·키·계정 (값 없음)

- OpenAI 키 파일: `C:\codex\openai & biteplus API.txt` (실행 시에만 읽음, 절대 출력·커밋 금지). 오늘 사용자가 **이 키로 xhigh 생성을 명시 승인**했고 11장 생성에 약 $0.90 사용. 앱 크레딧 원장과 무관.
- 앱 내 유료 AI(운영 `provider_attempts`): 이전 승인(4회/$2) 대비 9회·$1.07 사용 → 앱 내 새 유료 호출은 사용자 재승인 없이는 하지 않는다. 로컬은 fixture(무료).
- 로그인은 Google 전용. 관리자 이메일 `phoenixai.sw@gmail.com`. 운영 관리자 세션 파일은 없다(있어도 만료됨).
- 운영 DB(Supabase) 읽기: `.local/cloud-env.json`의 `DATABASE_URL`을 `postgresql+psycopg://`로 바꿔 접속. 쓰기 전에는 반드시 `scripts/backup-platform.py`로 백업(예: `.local/platform-cloud-ops.py backup`과 같은 방식).
- Vercel CLI 로그인 상태(`npx vercel whoami` = phoenixai-sw), 팀 `phoenixs-projects-ea6a0b8e`. main 푸시 시 web/api 자동 배포.
- GitHub `gh` 로그인 상태. 병합은 squash + 브랜치 삭제.
- **로컬 `.env`(커밋 금지)**: 오늘 `PAYMENT_PROVIDER=toss_test`, `BILLING_ENCRYPTION_KEY`(로컬 전용 생성), `TOSS_SECRET_KEY/TOSS_CLIENT_KEY/TOSS_MERCHANT_ID=tosspayments`를 추가했다. 키는 **토스 공식 문서의 공개 테스트 키**(docs.tosspayments.com "API 키"·"회원가입 없이 테스트" 문서에 공개된 `test_ck_…`/`test_sk_…`, mId `tosspayments`)라 실제 출금 없음. 운영 Vercel에는 넣지 않았다.
- 로컬 SQLite `.data/phoenix.db`의 `billing_accounts`는 오늘 초기화했다(고객키 길이 버그 재현 후). 로컬 데이터는 QA용이다.

## 3. 오늘 발견한 버그·판단

- **Toss 고객키 51자 버그**: `phoenix_`+`token_urlsafe(32)`가 Toss 규칙(2~50자)을 넘어 실제 결제창이 거부됨 → PR #15로 40자로 수정. 운영 `billing_accounts` 0건이라 영향 없음.
- Toss MCP(`tosspayments-integration-guide`)는 **문서 조회 전용**이다. 결제 실행·키 발급은 하지 않는다.
- Claude 내장 브라우저에서는 **Google 로그인 팝업이 차단**된다(사용자 클릭도 실패, `GSI_LOGGER: Failed to open popup`). 로컬(localhost)은 어제 로그인이 됐던 세션이 남아 있어 사용 가능. 운영 사이트에는 내장 브라우저로 로그인 못 함.
- `.env`의 관리자 ICC 등록을 코드로 대신하려 하면 자동 권한 분류기가 "공유 자원 수정"으로 막을 수 있다. 관리자 UI에서 사용자가 직접 올리거나, 사용자 명시 승인 후 재시도.

## 4. 바로 이어서 할 일 (사용자가 "진행해"라고 승인한 상태)

### 4-A. 운영에 ICC 등록 → 인쇄 의뢰본 ZIP 만들기
- 목적: 편집기 → 검수와 출력 → **CMYK 인쇄 파일 → 인쇄 의뢰본 ZIP**이 "실제 ICC 필요" 안내 대신 실제로 생성되게.
- 파일: 사용자 PC `C:\Windows\System32\spool\drivers\color\JapanColor2001Coated.icc`(Adobe, 문서 임베드 허용; 앱 `inspect_icc` 통과 확인: CMYK prtr, 557,168B). 무료 재배포 대안은 ECI `PSO Coated v3`.
- 절차(관리자 로그인 필요): 운영 `/admin` → 인쇄 엔진 관리(`print-engine-admin.tsx`) → ICC 업로드(출처·사용권 기록) → 프로필 등록(`review_available` 켬, bleed 3mm) → 아무 프로젝트 편집기에서 "인쇄 의뢰본 ZIP 만들기" → `/app/projects/{id}/exports`에서 ZIP 다운로드 → `production.pdf`(표시 없음)·`artwork-with-dieline.pdf` 확인.
- 막힌 이유: 내장 브라우저 Google 팝업 차단. 해결 후보 ① 사용자 본인 Chrome에서 `/admin` 열어 직접 업로드(가장 빠름) ② Claude in Chrome 확장 연결 후 `file_upload` ③ 로그인 페이지에 **GIS redirect 모드 대안 버튼** 추가(`apps/web/src/app/auth/page.tsx`: `ux_mode:"redirect"`, `login_uri`=같은 출처 `/auth/google/return`, nonce는 sessionStorage에 두고 return 페이지가 같은 출처 fetch로 `/api/v1/auth/google` 호출 → 챌린지 쿠키(SameSite=Lax, 600초)가 같은 출처라 그대로 유효). ③은 개발 1~2시간, 임베디드 브라우저·팝업 차단 환경 전반에 도움.
- 로컬에서 같은 절차를 먼저 시험해도 된다(로컬 관리자 = 같은 이메일). API 시험 `tests/geometry_pdf/test_print_request.py` 참고.

### 4-B. Toss 테스트 결제 완주
- 로컬 상태: `http://localhost:3000/app/billing` → "500 크레딧 · ₩42,900" → 주문 생성 → **Toss 샌드박스 결제창(iframe `payment-gateway-sandbox.tosspayments.com`)이 열리는 것까지 확인.** 카드 입력은 Claude가 할 수 없으므로(정책) 사용자가 입력. 승인되면 앱이 `/v1/payments/confirm` 호출 → `payment_orders.status=paid`, 500크레딧 지급. `.local` 로그·`구독과 크레딧` 페이지 결제 내역에서 확인.
- 구독(빌링키 `requestBillingAuth`)은 문서용 키로 안 될 수 있다(자동결제는 계약 MID 필요). 안 되면 사용자에게 토스 개발자센터 가입(이메일만) → "개발 연동 체험 상점" 테스트 키 요청.
- 운영에 테스트 결제를 열려면 Vercel env에 `PAYMENT_PROVIDER=toss_test` + 키 3개 + `BILLING_ENCRYPTION_KEY`(운영은 이미 있음) 추가 필요. 운영 `APP_ENV`는 `staging`이라 코드상 허용된다. **사용자 확인 후** 진행.

### 4-C. 남은 작은 개발(선택)
- 요금제 크레딧 수량 조정(현재 500/1,500/4,500 유지) — 사용자 결정 대기.
- 첫 달 Pro 모집 99,000원 서비스 가격 유지 여부 — 사용자 결정 대기.
- 관리자 ICC 등록 화면에 "인쇄 의뢰본에 사용 가능(시험 공개)" 설명 보강 정도.

## 5. 외부 자료·사용자 결정이 필요한 것 (개발로 못 푸는 부분)

- 실제 인쇄소 도면·재질·색 조건, 제조사 승인(제작 게이트 `ENABLE_PRODUCTION_EXPORT`는 여전히 OFF).
- Toss 정식 테스트/라이브 키·계약.
- 유료 고객 5곳·실제 입고 10건(사업기획서 5~8주차).

## 6. 확인·실행 명령

```powershell
Set-Location -LiteralPath 'C:\codex\agent\package design'
git status --short
$env:APP_ENV = 'test'
& '.\.venv\Scripts\python.exe' -m pytest services/api/tests services/api/billing/tests tests/geometry_pdf -q -p no:cacheprovider   # 약 30분
npm test; npm run typecheck; npm run contracts:check; npm run build
& '.\.venv\Scripts\python.exe' packages/contracts/generate.py --check
& '.\.venv\Scripts\python.exe' packages/contracts/export_openapi.py --check
```
계약(DTO) 바꾸면 `export_openapi.py` → `npx openapi-typescript packages/contracts/openapi.json -o packages/contracts/api.generated.ts` 후 CRLF→LF 정리(생성기가 CRLF를 쓰는 경우 있음).

로컬 API/worker 재시작은 `scripts/dev.ps1 -Stop` 후 `scripts/dev.ps1`, 또는 uvicorn/worker 프로세스만 종료 후 `.env` 읽어 `-m uvicorn services.api.main:app --host 127.0.0.1 --port 8000`, `-m services.worker.runner`.

## 7. 오늘 만든/바꾼 주요 파일

- 엔진: `services/api/exporters/print_pdf.py`, `print_verification.py`, `services/api/print_engine.py`, `services/api/jobs.py`
- AI/편집: `apps/web/src/components/ai-studio.tsx`, `image-text-tools.tsx`, `packages/editor/src/image-tools.ts`
- 요금·추천·문의: `config/pricing.seed.json`, `services/api/billing/referrals.py`, `services/api/inquiries.py`, 마이그레이션 `0019_referrals.py`, `0020_inquiries.py`, `apps/web/src/app/contact/page.tsx`, `apps/web/src/app/admin/inquiries/page.tsx`, `apps/web/src/components/referral-tools.tsx`
- 미디어/로고: `scripts/generate-brand-media.py`, `apps/web/public/media/*`, `apps/web/public/brand/*`, `fixtures/demo-backgrounds/*`, `apps/web/src/components/brand.tsx`, `apps/web/src/lib/media.ts`
- 로고 원본: `C:\codex\agent\phoenix-portal\Logos\` (Simple Ver = 사용자가 지정한 로고)
