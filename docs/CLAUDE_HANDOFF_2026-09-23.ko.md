# Phoenix Package Design — 인수인계 (2026-09-23, 개발방-2 → 다음 방)

작성: 2026-09-23, Claude Code(Opus 5) 세션 "phoenix package design 개발방-2". 이전 인수인계 [CLAUDE_HANDOFF_2026-09-22.ko.md](CLAUDE_HANDOFF_2026-09-22.ko.md)의 후속이다. 경로·저장소·서버·키 파일 위치는 그대로, 사용자도 같다. 비밀값은 없다.

## 0. 첫 10분

1. 이 파일 → [2026-09-22](CLAUDE_HANDOFF_2026-09-22.ko.md) 순으로 읽는다. §1의 조사 결론은 **다시 조사하지 말 것**.
2. 사용자 스타일: **짧고 쉬운 한국어, 결과 중심, 덜 끝난 걸 완료라 말하지 않기, 반복 보고·문서 늘리기 금지.**
3. 작업 폴더는 **`C:\codex\agent\package design` 하나뿐이다.**
4. **main = `e2a16a5`, 열린 PR 없음, 미커밋 변경 없음.** PR #13~#19 모두 병합·운영 배포 완료.
5. 로컬 서버는 `scripts/dev.ps1`로 띄운다(`-Stop`으로 끈다). 3000 web / 8000 api / worker.
6. 바로 할 일은 §4.

## 1. 이름이 바뀌었다

**Phoenix Packaging → Phoenix Package Design** (2026-09-23, 사용자 요청). 화면·PDF 제목·결제창 주문명·README에 반영했다.

**바꾸지 않은 것** — 외부에 등록된 식별자라 바꾸면 깨진다:
- 구글 OAuth 프로젝트·클라이언트 이름 `phoenix-packaging`, `phoenix-packaging-web`
- 저장소 이름, Vercel 프로젝트와 주소 `phoenix-packaging.vercel.app`
- 지난 인수인계 문서(그때의 기록)

## 2. 2026-09-23에 병합·배포한 것

### PR #18 — 레이어 병합, 부분 수정 흠 수정, 이름 통일

| 기능 | 내용 |
| --- | --- |
| **레이어 병합** (신규) | 겹친 이미지 레이어를 검토 PDF와 같은 방식(자르기·회전·투명도)으로 합성해 **불투명한 한 장**으로 만든다. 크레딧 0, AI 호출 없음, 같은 저장본이면 항상 같은 픽셀. 따낸 레이어의 알파를 없애 CMYK 제작 출력을 통과시키는 것이 목적이다 |
| 안전장치 | 선택 레이어 **사이**에 다른 레이어가 겹치면 `MERGE_LAYERS_BETWEEN`, 합친 뒤에도 투명이 남는데 **아래**에 그림이 있으면 `MERGE_LAYERS_BELOW`로 거부. 잠김·숨김·인쇄 제외·중복 선택도 거부 |
| 부분 수정 흠 4개 | ① 따낸 레이어·무료 사각형 복사본이 면 맨 위로 올라가 글자를 덮던 것 → 포토샵 복제처럼 **원본 바로 위**에 놓는다(이게 고쳐지기 전엔 병합 자체가 불가능했다) ② 예시 배경은 잠겨 있어 도구가 전부 비활성인데 이유를 안 알려주던 것 ③ 패널 크기 변경 시 브러시 표시가 어긋나던 것 ④ 영역 한도(64개·3,000점) 초과가 견적에서야 실패하던 것 |

주요 파일: `services/api/image_merge.py`(신규), `services/api/contracts/images.py`, `apps/web/src/components/image-merge-tools.tsx`(신규), `image-preparation-tools.tsx`, `image-region-tools.tsx`, 시험 `services/api/tests/test_image_merge.py`(10건).

### PR #19 — 결제창이 확인 모달에 가려 결제 불가였던 문제

"결제창 열기"를 누르면 토스 결제창은 열리지만 **보이지 않았다.** 우리 확인 창이 `showModal()`로 연 `<dialog>`라서 브라우저 **top layer**에 그려지고, 이 층은 토스의 `z-index: 9999999`로도 이길 수 없다. 고객에겐 끝나지 않는 스피너만 보였다. **모든 PC 브라우저에서 같다.**

고친 방법: 결제창을 여는 시점에 확인 모달 상태를 비우고, `openTossPayment`가 열린 모달 `<dialog>`를 모두 닫는다(`apps/web/src/lib/payment-window.ts`). 결제창을 닫으면(`USER_CANCEL`) 빨간 오류 대신 다시 여는 방법을 안내한다. 시험 3건 추가.

## 3. 이전 인수인계에서 **틀렸던 값 2개** (바로잡음)

1. **`TOSS_MERCHANT_ID`는 `tosspayments`가 아니라 `tvivarepublica`다.** 테스트 클라이언트 키 `test_ck_D5GePWvyJnrK0W0k6q8g`의 실제 mId다. 틀린 값이면 토스가 승인한 결제를 서버가 `PAYMENT_ACCOUNT_MISMATCH`("다른 상점의 결제는 처리할 수 없습니다")로 거부한다. 로컬 `.env`는 고쳤지만 **`.env`는 커밋하지 않으므로 새로 설정하면 또 틀린다.** 참고: 그 거부는 **안전장치가 제대로 동작한 것**이다 — 확인 안 된 결제로 크레딧을 주지 않았다.
2. **ICC 총잉크량 상한 기본값 300%로는 Japan Color 2001 Coated를 쓸 수 없다.** 이 프로필의 실제 총잉크량은 **349%**(검정 기준)라 어두운 색이 전부 `TOTAL_INK_LIMIT`에 걸린다. **350%** 로 등록해야 한다.

## 4. 다음 담당자 할 일

1. **실제 유료 AI로 부분 수정 품질 1회 확인** — 로컬은 전부 fixture(가짜)라 GPT Image 2.5의 실제 마스크 인페인팅 **품질**은 아직 본 적이 없다. **사용자 재승인 필요**(§5). 10크레딧 1회면 충분하다.
2. **운영 결제 활성화 여부 결정** — 지금 `billing_provider=disabled`. 사용자에게 먼저 물어볼 것.
3. (선택) 부분 수정 도구 보강 — 서버는 다각형(올가미)을 받지만 화면에 버튼이 없다. 자동 피사체 선택(매직완드)도 없어 따내기는 글로 지시한다.
4. (선택) 프로젝트 삭제 기능 — 현재 앱에 없다. 삭제 요청은 `asset`·`export`만 실행된다(`EXECUTABLE_SCOPES`). 운영에 QA 프로젝트 "QA · ICC 인쇄 의뢰본 확인 0923"이 남아 있는데 사용자가 **그냥 두라고 했다.**

## 5. 예산·키·계정 (값 없음)

- OpenAI 키 파일 `C:\codex\openai & biteplus API.txt` — 실행 시에만 읽고 절대 출력·커밋 금지.
- 앱 내 유료 AI 호출: 이전 승인(4회/$2) 대비 9회·$1.07 사용 → **새 유료 호출은 사용자 재승인 필요.** 로컬은 fixture(무료).
- 운영 DB 읽기: `.local/cloud-env.json`의 `DATABASE_URL`을 `postgresql+psycopg://`로 바꿔 접속. 쓰기 전 반드시 백업.
- `gh`·`npx vercel` 로그인 상태. main 푸시 시 web/api 자동 배포. 병합은 squash + 브랜치 삭제.
- 로컬 `.env`는 커밋하지 않는다(Toss 테스트 키·`BILLING_ENCRYPTION_KEY` 포함).

## 6. Claude가 **직접 못 하는 일** (사용자에게 부탁할 것)

안전장치가 막는다. 우회하지 말고 사용자에게 요청한다.

| 못 하는 일 | 대신 할 것 |
| --- | --- |
| 앱 로그인(구글 버튼 클릭, 세션 토큰 생성) | 브라우저 창을 띄워 두고 사용자가 로그인 |
| PR 병합(`gh pr merge`, 자동 병합) | 명령을 알려주고 사용자가 실행 |
| 결제 확인 버튼 클릭 | 화면을 띄워 두고 사용자가 클릭 |
| 파일 선택 창에 파일 넣기(ICC 업로드 등) | 경로를 알려주고 사용자가 선택 |
| 구글 클라우드 콘솔 설정 변경 | 단계를 적어주고 사용자가 수행 |

또한 **Claude 브라우저 창은 팝업을 막는다**(`window.open`이 null). 팝업으로 뜨는 흐름은 검증할 수 없다. 토스 결제창은 PC에서 iframe이라 괜찮았다.

## 7. 현재 운영 상태 (2026-09-23)

- 웹 https://phoenix-packaging.vercel.app · API https://phoenix-packaging-api.vercel.app (health ok, environment=staging)
- 부분 수정 `edit_modes: ['full','remove_text','region','cutout']` + 레이어 병합 `/v1/image-quality/merge` 모두 운영에서 동작.
- **운영 ICC 등록 완료** — `Japan Color 2001 Coated (TAC 350%) — 제조사 승인 전`. 인쇄 의뢰본 ZIP 생성·검증까지 끝냈다(`production.pdf`에 "제작 사용 불가" 표시 없음, CMYK·ICC 임베드·도련 3mm·글꼴 윤곽선 확인, preflight 경고 0건).
- 운영 결제 비활성(`billing_provider=disabled`). 구글 리디렉션 URI 3개 등록 완료.
- **제조사 승인은 여전히 없다.** 파일이 나오는 것과 인쇄소가 그대로 찍어주는 것은 다른 문제다. `manufacturer_approval: false`, `pdf_x_conformance: not_claimed`로 정직하게 기록된다.
- 미해결 외부 항목: 인쇄소 도면·승인, Toss 정식 키·계약, 유료 고객.

## 8. 검사 명령

```powershell
Set-Location -LiteralPath 'C:\codex\agent\package design'
$env:APP_ENV = 'test'
& '.\.venv\Scripts\python.exe' -m pytest services/api/tests services/api/billing/tests tests/geometry_pdf -q -p no:cacheprovider   # 약 30분
npm test; npm run typecheck; npm run contracts:check; npm run build
& '.\.venv\Scripts\python.exe' packages/contracts/export_openapi.py --check
& '.\.venv\Scripts\python.exe' packages/contracts/generate.py --check
```
계약(DTO) 변경 시: `export_openapi.py` → `npx openapi-typescript packages/contracts/openapi.json -o packages/contracts/api.generated.ts` → CRLF→LF 정리. **새 모델은 참조하는 모델보다 위에 정의**할 것.

웹 시험은 `apps/web/tests/*.test.mjs`에 순수 함수만 둔다. Node ESM이라 **확장자 없는 상대 import를 가진 모듈은 불러올 수 없다** — 시험할 로직은 잎 모듈로 빼야 한다(`payment-window.ts`가 그 예다).
