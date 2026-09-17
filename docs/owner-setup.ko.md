# 운영 연결 안내

2026-09-17 사용자 지시에 따라 Resend/SMTP 가입, 인증메일, 비밀번호 재설정, 제조사 자료 요청 절차를 폐기했다. Google 로그인, Toss 문서 MCP, 공개 인쇄 규격을 사용한다.

## Google 로그인

- 외부 인증 설정은 **Package Design 전용으로 발급한 웹 OAuth 클라이언트의 `GOOGLE_CLIENT_ID`** 하나다. 클라이언트 비밀키나 메일 서버는 필요하지 않다.
- 운영 허용목록은 `ADMIN_EMAILS=phoenixai.sw@gmail.com` 한 계정만 사용한다. 고객의 개인/팀 작업 공간은 기존 소유자·편집자·뷰어 권한으로 격리한다.
- Google Cloud 전용 프로젝트 `phoenix-packaging`, 앱 `Phoenix Packaging`, 웹 클라이언트 `phoenix-packaging-web` 생성과 발급을 완료했다. Google 사용자 데이터 정책 동의도 완료했다. AI브릿지 Client ID는 패키지 로컬·Vercel 설정에서 제거했다. 발급한 ID는 `C:\codex\phoenix-service-keys.local.txt`의 `GOOGLE_CLIENT_ID`로 환경에 반영하며 Git에 기록하지 않는다.
- 전용 웹 클라이언트의 승인된 JavaScript 원본에 `https://phoenix-packaging.vercel.app`, `http://localhost:3000`, `http://127.0.0.1:3000` 세 주소의 저장을 확인했다. 다른 서비스의 클라이언트나 허용 원본은 변경하지 않는다.
- Google 버튼의 팝업/콜백으로 받은 ID 토큰을 서버에서 서명·발급자·수신자·만료·인증 이메일·일회성 nonce까지 검증한다. 이메일·비밀번호 가입/로그인/복구 API는 사용하지 않는다.
- 기존 Gmail/Workspace 계정은 Google이 이메일 소유권을 보증할 때만 안전하게 연결한다. 외부 이메일 주소의 기존 계정 충돌은 자동 합치지 않는다.
- 팀 초대는 소유자에게 표시하는 일회성 초대 링크를 직접 공유한다. 초대장에 지정한 계정으로 Google 로그인해야 수락할 수 있다.

공식 근거: [Google 웹 클라이언트 설정](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid), [서버 ID 토큰 검증](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token).

전용 ID와 관리자 허용목록은 로컬 및 Vercel production/preview 암호화 환경에 저장했다. 로컬에서 사용자가 Google 팝업 인증을 완료한 뒤 `/app`으로 이동했고, `Phoenix Ai_SW`·`phoenixai.sw@gmail.com` 계정과 운영 관리 링크를 확인했다. `/admin` 진입 후 Google 로그인 연결 상태와 관리자 데이터도 정상 표시됐다.

운영 DB 마이그레이션과 웹·API 배포를 완료했다. `APP_URL`은 `https://phoenix-packaging.vercel.app`으로 설정하고 허용 원본과의 일치를 검증했다. 운영 health·홈·로그인 준비 응답 200과 새 전용 ID 일치, 이전 비밀번호 로그인·가입 API의 410 응답을 확인했다. 운영 도메인의 실제 Google 로그인은 사용자 확인 대기 중이다.

## Toss 결제

`~/.codex/config.toml`에 다음 서버를 등록하고 실제 MCP 문서 조회를 확인했다.

```toml
[mcp_servers.tosspayments-integration-guide]
command = "npx"
args = ["-y", "@tosspayments/integration-guide-mcp@latest"]
```

명세는 이 MCP의 `get-documents`와 `document-by-id`로 조회한다. 기존 aibridge-site의 승인·취소·웹훅·동기화 코드를 현재 Python 서버와 기존 원장에 연결한다. 브라우저 성공 화면이나 웹훅 본문만으로 결제 완료/크레딧 지급을 확정하지 않는다.

기존 로컬 설정에서 테스트 키는 확인했으나, 연결할 상점의 MID는 확인되지 않았다. 실제 테스트 결제 연결에는 같은 상점의 `TOSS_CLIENT_KEY`, `TOSS_SECRET_KEY`, `TOSS_MERCHANT_ID`가 필요하다. 자동결제 라이브 전환은 해당 계약과 실제 흐름 검증 후 별도로 한다. 참고: [API 키](https://docs.tosspayments.com/reference/using-api/api-keys), [자동결제](https://docs.tosspayments.com/guides/v2/billing/integration).

## 출력 규격

제조사에 보낼 자료 요청문은 제거했다. GS1, ISO 공개 설명, 국내 3곳의 공식 입고 규격을 직접 조사한 [기본 규격표](basic-print-specifications.ko.md)와 버전별 검사 규칙으로 검토 PDF를 검증한다.

공개 입고 조건은 해당 제조사가 이 앱의 파일이나 임의 치수를 승인했다는 증거가 아니다. 도련·안전 여백·해상도·글꼴·바코드 등 계산 가능한 조건을 검사하며, 색상 공정·실제 바코드 판독·재질과 가공 공차는 파일 검사 결과와 구분한다. 지원하지 않는 CMYK/PDF-X/윤곽화 조건은 통과로 표시하지 않는다.

## 로컬 설정 파일

추가하거나 변경할 값은 `C:\codex\phoenix-service-keys.local.txt`에 보관할 수 있다. 서비스 비밀번호는 필요하지 않으며, 시크릿 키는 채팅이나 Git에 붙이지 않는다. Google 원본 등록 및 실제 로그인, Toss 상점 식별과 결제 흐름 검증의 결과를 운영 기록에 남긴다.
