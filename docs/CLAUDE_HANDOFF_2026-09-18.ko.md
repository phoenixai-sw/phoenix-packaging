# Phoenix Packaging — Claude Code 인수인계

작성: 2026-09-18. 사용자가 Codex에서 Claude Code로 잠시 이동하여 개발을 이어가기 위해 요청한 인수인계다. 이 문서에 비밀키·세션·서명 다운로드 URL은 포함하지 않았다.

## 1. 사용자 의도와 보고 방식

- 최초 작업지시서대로 패키지 디자인 플랫폼을 끝까지 개발한다. 부분 구현을 전체 완료라고 보고하지 말 것.
- 사용자는 과도한 토큰 소비, 반복 점검·문서 작성, 어려운 설명에 불만이 있다. 설명은 짧고 쉬운 한국어로, 실제 사용 결과 중심으로 한다.
- 가장 중요하다고 재확인한 기능은 **참고 이미지 또는 글 설명으로 이미지 생성 → 추가 수정 → 앞·뒷면 디자인에 배치**다.
- 도면·칼선, 글자 수정 기록, 바코드, 걸이 구멍·지퍼 입구·절취 홈·박스 윗면도 요청했다.
- 작업 메뉴를 직접 이용한 결과물 PDF와 실제 화면·이미지를 넣은 매뉴얼을 원한다. 필요 이상으로 기존 매뉴얼을 다시 만들지는 말 것.
- 지금은 인수인계 요청이다. 이번 인수인계 턴에는 앱 코드 변경·배포·유료 호출을 하지 않았다.

## 2. 가장 먼저 확인할 위치

**실제 최신 작업 저장소:** `C:\codex\agent\package design`

- GitHub: https://github.com/phoenixai-sw/phoenix-packaging (private)
- 현재 브랜치: `codex/current-platform`
- 인수인계 때 확인한 HEAD: `ef2104c` — PR12 문서 병합.
- 실제 기능 배포는 PR11, 병합 커밋 `9dbf5c31555323b2e3eeaf394d86389de9ce5a91` 기준. PR12는 문서만 변경.
- 별도 작업 트리 `C:\codex\agent\package design-next`는 이전 QA/개발용이다. 최신 기준을 혼동하지 말 것.
- Codex 화면의 작업 경로 `C:\Users\naraj\OneDrive\바탕 화면\문서\ChatGPT\package design`는 위 실제 저장소와 다르다. 그 위치에도 이 인수인계 사본을 두지만, 코드 수정은 위 실제 저장소에서 시작한다.
- 현재 기존 변경: `apps/web/next-env.d.ts` 수정, `output/` 미추적. Next가 만든 변경과 기존 결과물이므로 덮어쓰거나 삭제하지 말 것.
- `apps/web/AGENTS.md`, `apps/web/CLAUDE.md`를 읽을 것. 프런트 수정 시 설치된 Next 문서도 확인하도록 지시되어 있다.

우선 읽을 파일(실제 저장소 기준):
1. `Phoenix_Packaging_Codex_작업지시서.md` — 최초 원문. 후속 사용자 변경은 아래를 함께 적용.
2. `docs/spec-completion-tracker.ko.md` — 요구사항/AC01~32 대조표.
3. `docs/checkout-finishing-release.ko.md` — 마지막 기능 배포 및 실제 검증 근거.
4. `docs/image-generation-upgrade.ko.md`, `docs/ai-layout-context.ko.md`, `docs/print-preparation-release.ko.md` — 이미지 생성·수정.

주의: 대조표 일부 행의 '앱 배포 대기'는 오래된 문구다. PR11 배포는 완료된 것으로 확인했다. 또한 오래된 `image-quality-tools.ko.md`의 'RGB 출력만 지원' 문구는 이후 ICC CMYK 엔진 추가 전 기록이다. 최신 구현·배포와 문서 시점을 구분한다.

## 3. 운영·연동 상태

- 웹: https://phoenix-packaging.vercel.app
- API: https://phoenix-packaging-api.vercel.app
- 관리자: `phoenixai.sw@gmail.com`
- Google OAuth는 Phoenix Packaging 전용 클라이언트. 기존 AI브릿지 클라이언트를 빌려 쓰지 않는다.
- Supabase project ref: `jflpitqwnhznxmvwecnm`, Pro 조직의 서울 MICRO 프로젝트.
- 마지막 운영 DB 상태: migration `0018_service_checkout`, 앱 테이블 64개 RLS 적용 확인.
- 직전 보고 턴에 운영 `/api/v1/health`, `/api/v1/config`를 재확인: DB 연결 정상, Google 로그인 활성, `ai_provider=openai`, `billing_provider=disabled`, `production_export_enabled=false`, 환경 표시 `staging`.
- 운영 URL에 배포되었다는 사실과 상용 서비스 인수 완료는 다르다. 잠금 플래그를 켜는 것만으로 완료 처리하지 않는다.
- Vercel 마지막 확인 배포 ID: web `dpl_8gZ5fc34wNe58DjSedBC1guZNbEk`, API `dpl_YtDg8A88cymZ5S4VEgA3xUQLXhzQ`.
- 로컬 서버는 마지막 확인 시 3000(웹)/8000(API)에서 실행 중이었다. 실제 프로세스는 다시 확인할 것. 로컬 AI fixture/결제 disabled/제작 출력 false.
- 3001/8001 별도 QA 서버는 종료했다. `.local/ops-qa` 자료는 보존.

설정/비밀정보는 `.env`, `.local/cloud-env.json` 및 기존 설정 스크립트에 있다. 값을 대화·로그·Git에 노출하지 않는다. 과거 사용자가 준 원본 키 파일은 `C:\codex\openai & biteplus API.txt`; 필요할 때만 안전하게 읽고 출력하지 말 것.

## 4. 실제 구현된 기능과 정확한 한계

### 핵심 이미지 생성·편집
- AI 생성/수정, 프롬프트 입력, 이미지 자산 업로드·배치, 구조·브랜드색·문구/바코드 여백 맥락 전달.
- 설정된 모델 선택값: `gpt-image-2.5-sunburst`, `gpt-image-2.5-flare`; 품질 auto/low/medium/high/xhigh/max. 이것은 이 앱의 설정값이며 외부 공식 모델 지원을 별도로 입증하는 문서는 아니다.
- 실제 유료 생성·수정 확인은 Sunburst/high. 모든 모델·품질 조합의 실제 품질/속도를 시험한 것은 아니다.
- 사용자는 참고 이미지+설명, 설명만으로 생성, 결과 추가 수정이 가능한지 물었다. Codex는 가능하다고 답했다. **다음 담당자는 실제 메뉴에서 참고 이미지 업로드→수정의 흐름 및 텍스트만 생성 흐름을 확인해, 생성과 기존 이미지 수정 UI를 정확히 안내할 것.**
- 음성 입력 메뉴는 없다. '말로 생성'은 설명을 글로 입력하는 의미로 답했다.
- 한 번에 생성 1~3장, 수정 1장. 앱 설정상 low/medium/high 10크레딧, auto/xhigh/max 20크레딧. 현재 정책은 서버 견적을 기준으로 재확인한다.
- 인쇄 300PPI나 모든 요소 보존은 보장하지 않는다.
- 이미지 속 글자: 브라우저 OCR→사용자 교정→단일 영역의 기존 글자 AI 제거→독립 텍스트 추가. 이후 그 텍스트는 다시 수정 가능하다. 원본 이미지의 글자가 자동 벡터화되는 기능이 아니다.
- 영역 제거는 참조 이미지 수정 결과를 지정 영역에 합성하는 방식이다. 영역 밖 픽셀 보존, 영역 안 복원 품질은 확인 필요. provider-native mask 지원이라고 설명하지 말 것.
- 실제 운영에서 OCR→글자 제거 1회→22pt 굵게 새 문구→PDF까지 확인한 기록이 있다.
- 홈페이지 AI 이미지 3종 및 Seedance 2.5 영상 적용. 고객용 영상 생성 메뉴는 없다.

### 편집·관리·복구
- 브랜드/상품/변형·문구 연결, 이미지 자산 보관함, 정적 TTF 글꼴 등록·권리 기록.
- 앞/뒤/바닥 및 박스 면 편집, 위치·크기·회전·crop·자간·행간·불투명도·잠금·다중선택·정렬·분배.
- 자동 저장, 저장본 비교/복원, 탭 간 편집권·충돌 방지, 도구 입력 초안 재개.
- PNG/JPEG/WebP, 정제한 SVG를 정적 PNG로 사용하는 방식. SVG 벡터 노드 편집 기능은 아니다.
- Google 인증·관리자/팀 권한, 대시보드/작업/파일 기록, 정책·크레딧 정정·운영 지표·제한된 지원 세션.
- 출력 파일 손실 감지/재시도, 논리 백업·격리 앱 복원. 모든 원본 자동복구나 DB 물리/PITR 복구 완료는 아니다.

### 바코드·가공·출력
- EAN-13 번호 조합/체크디지트·중복·사용 권한 근거·상품 연결·샘플 구분·벡터 배치/PDF 판독.
- GS1 정식 상품번호 발급이나 외부 소유권 인증 기능은 아니다.
- 원형 걸이 구멍, 지퍼/개봉부, U/V 절취 홈; 지원 구조에서 칼선 PDF 출력 가능.
- 박스 6면·윗면·바닥·접착부/덮개와 3D/전개도 구현. 실제 승인 박스 조립 공차 검증은 미완료.
- PPI 진단·Lanczos 픽셀 확대·edge/mirror 도련 확장·원본 품질 계보 보존. 확대는 디테일 복원이 아니며 원본 저PPI 경고를 지우지 않는다.
- 검토 PDF, 편집 ZIP, ICC CMYK 시험 출력, 정적 TTF 윤곽선, CUT/FOLD/PROCESS 자료 엔진 구현.
- 편집 ZIP은 Scene/자산/허용 글꼴 묶음이다. AI/PSD 호환 파일도, ZIP 재가져오기 UI도 아니다.
- 최종 제조 출력은 운영에서 잠겨 있다. 제조 도면/치수/재질/ICC/가공 조건별 실제 승인 근거와 실물 검증이 없다.
- 미지원: PDF/X, 별색/화이트/오버프린트, 임의 곡선 칼선·비원형 걸이 구멍·미세천공/레이저 절취 등. 이것은 **소프트웨어 미구현**이며 외부 자료만 받으면 모두 해결되는 상황이 아니다.
- 실제 제조 입고 기록과 내부 시험 기록은 구분되며, 자체 시험을 제조사 승인으로 만들지 않는다.

### 결제
- Toss 결제/빌링/환불·웹훅 동기화, 크레딧 예약/차감/복원, 구독/충전, 서비스 견적 단건 결제 구현 및 모의 검증.
- 로컬 모의 단건 서비스 55,000원, 파일럿 99,000원/갱신 108,900원 동의·중단·미사용 전액 환불 UI 확인 기록.
- 실제 Toss 테스트 가맹점 승인·빌링키·갱신·취소·웹훅 전체 실검증 미완료. 운영 실제 결제 비활성.

## 5. 사용자 후속 지시·승인 범위 보존

- 이메일/비밀번호/Resend 대신 Google 로그인 사용. 관리자·팀원 허용 목록 방식 요청. Google 전용 클라이언트 사용.
- Toss 명세는 등록된 `tosspayments-integration-guide` MCP 사용 요청. Claude에서 사용 가능 여부를 확인하고 필요 시 설정한다. 원 요청 설정은 npx `-y @tosspayments/integration-guide-mcp@latest`.
- 결제 참고 코드 원본: `C:\codex\agent\aibridge-site\lib\toss.js`, `lib\payment-sync.js`, `api\payments\webhook.js`, `api\payments\confirm.js`. 이미 이식된 부분을 확인하고 무작정 재복사하지 않는다.
- 제조사 요청문 대신 GS1·ISO12647·국내 3곳 공개 입고 조건 조사 요청. `docs/basic-print-specifications.ko.md`에 기록. 일반 규격 조사와 특정 제조사 승인 구분.
- Supabase MICRO 월 약 10달러 비용은 승인받아 생성했다. 새 유료 서비스 구매의 포괄 승인으로 보지 않는다.
- AI 테스트 승인: **관리자 40크레딧 추가 + 최대 4회 생성/총 2달러 한도**. 과거 실행분이 있으므로 새로 40크레딧/2달러를 다시 지급·소비할 수 있는 승인이 아니다. 실제 원장·작업 기록으로 누적 사용을 확인하고 범위 내에서만 사용한다.
- 사용자 샘플 문구: **300g 수치 제외, 소고기 100% → 오리고기 100%**. 그 외 개수/총중량/법정 표시를 임의 확정하지 않는다.

## 6. 샘플·산출물·기존 프로젝트

사용자 원본 이미지 폴더:
`C:\antigravity - openclaw\5th\agent make llm\@@@@@ 상품기획부터 상세p 및 인쇄까지 - 디자이너 없이 구축\1. packaging design agent\@ 친구유종민`

- `happy2.png`: 기존 오리스틱 앞면
- `KakaoTalk_20260910_192708944_04.jpg`: 기존 뒷면
- `도면_칼선따기.png`: 240×330×120mm 지퍼 스탠드 파우치 참고
- `거는 구멍 디자인도 필요해.png`, `박스 윗부분 디자인 필요해.png`, `지퍼백 윗부분 절단부분 디자인도 필요해.png`, `지퍼백 입구 디자인도 필요해.png`
- 구멍/입구 추가 예시에는 90×180mm/상단30mm가 표시되어 있다. 기존 240×330mm 샘플에 무조건 같은 완성 치수로 적용하지 말고 구조와 예시를 구분한다.

운영 프로젝트:
- V2 파우치 `ed4e42ca-7ad6-4a6f-b563-a218f64abcdb` (마지막 V2 문서 기준 저장본22)
- 글자 제거 QA 사본 `02d610a9-1e3c-46b6-a218-06cf37479b19` (저장본6): 전체 V2 최종본이 아님.
- 등록 글꼴/SVG/출력 QA `cfd10093-af0d-43a6-87f3-5cb14ade2309`
- 사용자가 보고 있던 review export `0b4d3241-d971-48b3-9d48-23b10d509ffd`. 짧게 만료되는 서명 URL을 저장/재사용하지 말고 앱에서 다시 다운로드한다.
- 사용자가 동시에 프로젝트를 생성하기도 했다. 기존 프로젝트를 임의 변경/삭제하지 말 것.

기존 산출물(실제 저장소의 `output/pdf/`):
- `kingkong-pouch-review-v2.pdf`, `kingkong-box-review-v2.pdf`
- `kingkong-workflow-manual-v2.pdf` — 실제 샘플 작업 매뉴얼
- `image-text-edit-qa.pdf` — 글자 편집 QA
- `checkout-finishing-manual.pdf` — 8페이지, 실제 화면/모의 결제/가공/입고/복구
- `spec-completion-exports-manual.pdf`, `spec-completion-ops-manual.pdf`
- 가공 시험 파일은 `output/checkout-finishing-ui/finishing-trial.zip` 및 그 출력 자료.
- 문구 변경 기록: `docs/kingkong-text-changes-v2.ko.md`

**이 자료는 검토본이다. 샘플 바코드, 저PPI/도련, 일부 표시사항 미확정이 남아 있으므로 최종 제조본이라고 말하지 않는다.**

## 7. 검증 근거와 실행 명령

마지막 기능 CI: GitHub Actions run `35283331214`, HEAD `0560f2fec3941b9209aad2f7a2c0eb8d9d103fa5`.
Python 935개, JS 92개, 타입·계약·빌드 통과. 이는 지난 개발 검증 기록이며 이 인수인계 턴에 재실행한 것이 아니다.

주요 실제 확인:
- 운영 Google 로그인, 관리자 메뉴, 브랜드·상품·팀·보관·지표, 등록 글꼴/SVG→출력 3종 다운로드·해시.
- 로컬 가공 UI→CMYK 시험 ZIP 8파일/8페이지 렌더 확인.
- 로컬 출력 고의 손상→시간 가속 관측→실제 UI 재시도→동일 저장본/동일 PDF SHA 복원. 운영 파일을 손상시킨 시험이 아니다.
- 운영 DB 0018 전후 암호화 논리 백업·격리 앱 복원. PostgreSQL 물리/PITR 복구 시험은 아님.

PowerShell 시작 위치:
```powershell
Set-Location -LiteralPath 'C:\codex\agent\package design'
git status --short
```
기존 서버가 없을 때만 `./scripts/dev.ps1`을 검토 후 사용한다. 이 스크립트는 `.env`를 읽고 DB migration/API/worker/web를 실행한다. 기존 프로세스나 운영 DB를 무작정 재시작/이행하지 않는다.

검사 명령은 `.github/workflows/checks.yml`에 있으며, 관련 변경부터 검사 후 필요한 통합 검사를 한다. 테스트 환경은 운영 DB/키와 분리한다.
```powershell
$env:APP_ENV = 'test'
& '.\.venv\Scripts\python.exe' -m pytest services/api/tests services/api/billing/tests tests/geometry_pdf -q
npm test
npm run typecheck
& '.\.venv\Scripts\python.exe' packages/contracts/generate.py --check
& '.\.venv\Scripts\python.exe' packages/contracts/export_openapi.py --check
npm run contracts:check
npm run build
```

## 8. 다음 담당자 권장 진행 순서 (새로 합의한 완료 기준은 아님)

1. 원문과 현재 코드에서 사용자가 가장 중요하다고 한 **글 설명만 생성 / 원본 이미지로 수정 / 재수정 / 디자인에 배치 / 저장·재열기 / 검토 PDF**의 실제 동선을 우선 확인한다. 이미 검증한 부분을 무작정 전부 재시험하지 말고 빈틈·오류를 고친다. 유료 생성은 기존 예산 잔액부터 확인.
2. 작업지시서의 필수 출력 범위와 현재 미구현 PDF/X·분판 기능을 대조하여 필요한 소프트웨어 개발을 진행한다. 외부 제조 자료가 필요한 검증과 자체 개발 가능한 기능을 분리해 일을 멈추지 않는다.
3. 실제 Toss 테스트 키/설정 존재 여부를 값 노출 없이 확인하고 가능한 실연동 검증을 마친다. 없으면 꼭 필요한 항목만 쉬운 말로 요청하고 독립 개발을 계속한다.
4. 사용자 샘플에 변경 기능을 적용하고 실제 PDF·문구·가공·바코드·품질을 확인한다. 자체 결과를 제조사 승인으로 바꾸지 않는다.
5. 변경 범위 테스트·실제 메뉴 확인·배포 후 검증을 마친 뒤, '이번에 실제 된 것 / 아직 안 된 것'을 짧게 보고한다. 근거 없이 전체 완료 선언하지 않는다.

모든 미지원 기능을 무조건 새로 추가하거나 문서 양을 늘리는 것이 목표가 아니다. 원래 요구사항과 후속 지시를 충족하는 사용자 동작을 끝까지 완성하는 것이 목표다.
