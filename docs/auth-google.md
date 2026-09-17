# Google 계정 인증과 운영 권한

인증은 Google Identity Services만 사용한다. 비밀번호 입력·저장·재설정, 인증 메일, SMTP 및 로컬 이메일 outbox를 사용하지 않는다. 일반 사용자는 Google 로그인으로 개인 작업 공간을 만들 수 있다. 기존 팀의 소유자·편집자·열람자와 작업 공간 제한은 그대로 유지한다.

## 환경 설정

- `GOOGLE_CLIENT_ID`: **Package Design 전용 Google Cloud 프로젝트에서 발급한 웹 애플리케이션 OAuth 클라이언트 ID**. AI브릿지 클라이언트는 재사용하지 않는다. 서버 한 곳에만 설정하며 공개 클라이언트 ID는 로그인 준비 응답으로 브라우저에 전달된다. 클라이언트 비밀키는 필요하지 않다.
- `ADMIN_EMAILS`: 현재 운영 허용목록은 `phoenixai.sw@gmail.com` 한 계정만 사용한다. 서버 전용이며 브라우저에 목록을 반환하지 않는다. 비어 있으면 운영 관리 접근을 허용하지 않는다.
- 기존 `APP_URL`, `ALLOWED_ORIGINS`, `COOKIE_SECURE`와 저장소·DB 설정은 유지한다. 전용 클라이언트의 승인된 JavaScript 원본에 실제 프런트엔드 원본과 필요한 localhost 원본을 각각 등록해야 한다. 다른 서비스의 OAuth 설정은 변경하지 않는다.
- 기존 `WORKER_SECRET`은 웹 프록시와 API에 같은 값으로 유지한다. Vercel 웹 프록시는 플랫폼이 제공하는 `x-vercel-forwarded-for`의 단일 IP를 검증하고 HMAC으로 익명 제한 식별자를 만든다. 식별자·시각·메서드·경로를 다시 서명하며 API는 60초 안의 유효한 서명만 수용한다. 브라우저가 보낸 제한용 헤더는 전달하지 않는다. 이 용도와 워커 인증은 서로 다른 입력 형식이며 비밀값은 응답에 포함하지 않는다.

Google 설정이 없으면 로그인 준비·교환 API가 `503 GOOGLE_LOGIN_NOT_CONFIGURED`로 차단된다. 가짜 버튼, 임시 비밀번호 또는 환경변수 기반 인증 우회는 제공하지 않는다.

전용 Google Cloud 프로젝트 `phoenix-packaging`, OAuth 앱 `Phoenix Packaging`, 웹 클라이언트 `phoenix-packaging-web` 생성과 발급을 완료했다. 사용자 데이터 정책 동의와 운영·로컬 원본 3개 저장도 확인했다. AI브릿지 ID는 제거했고 전용 ID와 관리자 허용목록을 로컬 및 Vercel production/preview 암호화 환경에 반영했다. 로컬에서 사용자가 실제 Google 팝업 로그인을 완료해 `http://localhost:3000/app`으로 이동했으며, `Phoenix Ai_SW`·`phoenixai.sw@gmail.com` 계정과 운영 관리 링크 표시를 확인했다. 이어 `/admin`에 진입해 Google 로그인 연결 상태와 관리자 데이터 로딩도 확인했다. 새로운 클라이언트 비밀키는 사용하지 않는다.

운영 DB의 `0007_google_auth` 전환과 웹·API 운영 배포를 완료했다. 초기 API 설정 오류는 `APP_URL=https://phoenix-packaging.vercel.app`을 명시한 뒤 설정 검증과 재배포로 해결했다. 운영 health·홈·로그인 준비 응답은 200이며 전용 클라이언트 ID 일치를 확인했다. 이전 비밀번호 로그인·가입 API는 410으로 차단된다. 운영 도메인의 실제 Google 팝업 로그인은 사용자 확인 대기 중이다.

## 서버 검증과 세션

1. `GET /v1/auth/google/challenge`는 10분짜리 일회성 요청을 만들고 HttpOnly 쿠키와 nonce를 연결한다. DB에는 쿠키와 nonce의 SHA-256 해시만 저장한다. 배포 환경에서는 웹 프록시가 서명한 실제 클라이언트 식별자별로 15분에 120번으로 준비 요청을 제한한다. 서로 다른 사용자가 같은 프록시 서버를 거쳐도 카운터가 합쳐지지 않는다. API가 직접 받은 `X-Forwarded-For`나 서명 없는 헤더는 신뢰하지 않으며 원래 IP는 인증 DB에 저장하지 않는다. 같은 공인 IP를 사용하는 사내망·NAT 사용자는 제한을 공유한다. 개발·시험 환경은 실제 연결 주소를 사용하고 모든 전달 헤더를 무시한다. Vercel 외 호스팅으로 옮길 때에는 그 플랫폼의 신뢰할 수 있는 주소 검증을 구현하기 전까지 로그인 준비를 허용하지 않는다.
2. Google 공식 버튼은 nonce를 ID 토큰에 포함한다. 브라우저는 `{credential, csrf_token}`을 `POST /v1/auth/google`에 보낸다. 이메일·역할을 입력해 로그인할 수 없다.
3. 공식 `google-auth` 라이브러리가 Google 공개키와 서명, 대상 클라이언트, 발급자, 만료를 검증한다. 서버가 다시 `aud`, `iss`, `azp`(있는 경우), `exp`, `iat`, `email_verified`, `sub`, 이메일 형식, nonce를 검사한다. Origin, 쿠키에 묶인 CSRF, 1회 사용 SQL 조건, 요청별·계정별 횟수 제한을 적용한다.
4. 성공하면 기존 형식의 무작위 HttpOnly 세션 쿠키를 발급한다. Google ID 토큰을 저장하거나 로그로 남기지 않는다. 세션은 DB에 해시로 저장하며 기존 변경 요청의 `X-CSRF-Token` 검증을 유지한다.

## 기존 계정 연결 및 충돌 기준

- Google `sub`가 영구 로그인 식별자다. DB의 유니크 인덱스로 중복 연결을 막는다.
- 처음 Google에 연결하는 기존 이메일 계정은 Google이 현재 소유권을 보증하는 **검증된 Gmail 또는 Google Workspace(`hd`)** 계정과 일치할 때만 자동 연결한다. 기존 사용자 ID·작업 공간·역할·프로젝트·잔액을 보존하고 예전 세션은 폐기한다.
- Google이 이메일 소유권을 보증하지 않는 외부 이메일의 신규 Google 로그인은 개인 작업 공간을 만들 수 있다. 같은 이메일의 기존 계정이 있으면 자동 병합하지 않고 `GOOGLE_ACCOUNT_LINK_REQUIRED`로 중단한다. 운영자의 별도 소유권 확인과 데이터 이전이 필요하며 공개 계정 연결 우회 API는 없다.
- 이메일이 이미 다른 `sub`에 연결되어 있거나, 기존 `sub`의 새 이메일이 다른 계정과 충돌하면 `409`로 중단한다. 이메일 대소문자만 다른 레거시 중복 계정도 자동 병합하지 않는다.
- 비활성 계정은 Google 로그인이 성공해도 애플리케이션에 로그인할 수 없다.

## 운영 허용목록과 팀 권한

`ADMIN_EMAILS`에 있고, 연결된 Google 계정의 이메일이 검증되어 있으며 Gmail/Workspace 소유권이 확인된 활성 사용자에게만 운영 관리자 접근을 부여한다. 매 요청마다 현재 서버 설정을 적용한다. 예전 DB `is_admin` 값은 권한의 근거로 사용하지 않는다.

운영 허용목록은 고객 팀의 역할이나 작업 공간 접근을 자동으로 올리지 않는다. 일반 팀 참가에는 소유자가 발급한 이메일 지정 초대와 기존 좌석·역할·작업 공간 제한이 적용된다. 소유자는 발급 직후 표시되는 7일짜리 일회성 링크를 직접 전달한다. 서버는 메일을 보내지 않으며, 초대 목록이나 감사 로그에 링크 원문을 저장하지 않는다. 초대 수락도 해당 이메일의 검증된 Gmail/Workspace 계정으로 제한한다.

## 마이그레이션과 운영 도구

`0007_google_auth`를 로컬과 운영 DB에 적용했다. 기존 비밀번호 해시·메일 토큰을 제거하고 기존 세션과 DB 관리자 플래그를 초기화한다. 운영 전환 시 기존 사용자 2개와 프로젝트 6개를 보존했고 public 41개 테이블 모두 RLS가 설정된 것을 확인했다. 기존 사용자는 Google로 다시 로그인해야 한다. `password_hash` 열은 레거시 스키마 호환 목적으로 빈 값만 남기며 인증에 사용하지 않는다. 삭제한 자격 증명을 downgrade로 복원하지 않는다.

`python -m services.api.seed --email <기존 Google 계정>`은 로컬 개발·시험 환경의 기존 계정에 예제 프로젝트만 만든다. `scripts/manage-admin.py --email ...`은 운영 허용목록 상태를 읽기만 한다. 권한을 변경하려면 배포 환경의 `ADMIN_EMAILS`를 수정하고 재시작·재배포한다.

## 검증의 범위

회귀 시험은 테스트 앱에서만 Google 서명 검증 dependency를 대체하고 실제 nonce·CSRF·계정 연결·세션·팀 ACL 경로를 실행한다. 별도의 RSA 서명 시험은 실제 공식 검증 라이브러리로 위조 서명·잘못된 발급자·대상·만료를 검증한다. 테스트용 helper는 운영 앱에서 import하지 않는다. 이는 실제 Google 계정 로그인이나 Google Cloud 도메인 등록을 대신 검증하지 않는다.

참고: [Google 서버 ID 토큰 검증](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token), [공식 로그인 버튼](https://developers.google.com/identity/gsi/web/guides/display-button), [nonce API](https://developers.google.com/identity/gsi/web/reference/js-reference#nonce).

프록시 주소 근거: [Vercel 공식 요청 헤더 문서](https://vercel.com/docs/headers/request-headers#x-vercel-forwarded-for). 일반 `X-Forwarded-For`를 임의로 파싱하거나 클라이언트가 제공한 값을 제한 식별자로 사용하지 않는다.
