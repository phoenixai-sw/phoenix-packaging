# 2026-09-17 인증·결제·출력 변경 검증

사용자 요청의 세 항목을 같은 변경으로 구현했다. Package Design 전용 Google OAuth 발급·허용 원본 등록·실제 로컬 로그인 검증 후 운영 DB와 웹·API를 전환했다. 운영 HTTP 검증은 통과했으며 운영 도메인의 실제 Google 로그인은 사용자 확인 대기 중이다.

## 반영 범위

- Toss 공식 문서 MCP 등록 및 stdio 조회. 지정 AI브릿지 네 파일의 승인·취소·웹훅·동기화 동작을 기존 Python 서버와 불변 원장에 이식했다. [이식 상세](toss-payment-port.md).
- Google 단독 로그인, 공식 서명 검증, nonce·CSRF·세션 회전, `ADMIN_EMAILS` 운영 권한. 비밀번호·메일 인증·복구 및 SMTP 모듈을 제거했다. 고객 팀 초대는 링크를 직접 공유한다. [인증 상세](auth-google.md).
- GS1·ISO 공개 자료, 한성피엔지·패커티브·레드프린팅 공개 입고 조건 조사와 [기본 규격표](basic-print-specifications.ko.md). 실제 PDF의 3mm 도련·재단 박스·글꼴 포함·바코드 판독을 저장 전에 검사한다.
- 독립 검토에서 발견한 상향 환불 후 유료 팀 권한 잔존, Google 스크립트 로딩 재시도, 프록시 공통 로그인 횟수 제한을 수정했다.

## 운영 전환 및 검증

- Google Client ID 재사용 계획은 사용자 정정에 따라 폐기했다. 전용 프로젝트 `phoenix-packaging`, 앱 `Phoenix Packaging`, 웹 OAuth 클라이언트 `phoenix-packaging-web` 발급과 승인 원본 3개 저장을 완료했다. 새 ID는 `C:\codex\phoenix-service-keys.local.txt`에서 검증해 읽으며, `ADMIN_EMAILS`는 `phoenixai.sw@gmail.com`만 사용한다. 로컬 및 Vercel production/preview 암호화 환경 반영을 완료했다. 새로운 Google 비밀키나 메일 서비스는 사용하지 않는다.
- 로컬 DB는 `0007_google_auth`이며 사용자가 실제 Google 팝업 인증을 완료했다. `http://localhost:3000/app`으로 이동한 화면에서 `Phoenix Ai_SW`·`phoenixai.sw@gmail.com` 계정과 운영 관리 링크를 확인했다. `/admin`에서는 Google 로그인 연결 상태와 관리자 데이터가 정상 로딩됐다. 서버 로그인 준비 응답은 새 전용 ID와 일치한다. 이는 실제 로컬 로그인·관리자 접근 검증 결과이며 운영 도메인 검증과 구분한다.
- 운영 DB를 `0007_google_auth`로 전환했다. 변경 전 배포 코드 `6da6c0e`로 40개 테이블·98개 행·11개 객체를 암호화 백업하고 격리 복원·해시 검사를 통과했다. 전환 시 기존 프로젝트 6개·사용자 2개를 보존했고 public 41개 테이블 모두 RLS가 적용된 것을 확인했다.
- `0007_google_auth`가 기존 비밀번호·메일 토큰·세션을 제거했으며 계정 소유자는 Google로 다시 로그인해야 한다. 과거 자격 증명을 되살리는 자동 downgrade는 제공하지 않는다.
- 전환 후 40개 테이블·92개 행·11개 객체의 암호화 백업과 격리 복원·해시 검사를 통과했다. 이는 애플리케이션 논리 복원 검증이며 PostgreSQL 물리 복원 시험은 아니다.
- 웹·API를 운영 도메인으로 전환했다. 초기 API 500은 누락된 `APP_URL`을 `https://phoenix-packaging.vercel.app`으로 설정한 뒤 `Settings.validate()` 통과와 API 재배포로 해결했다. 운영 health·홈·로그인 준비 응답은 200이며 새 전용 ID 일치를 확인했다. 이전 로그인·가입 API는 410을 반환한다.
- 같은 설정 누락을 막기 위해 클라우드 초기 설정·환경 갱신·운영 DB 마이그레이션 전에 환경 검증을 실행한다. 신규 회귀 6개와 기존 마이그레이션 관련 3개 테스트를 통과했다.

## 남은 실제 서비스 확인

전용 OAuth 발급, 승인 원본 저장, 환경 반영, 실제 로컬 Google 로그인과 운영 DB·배포 전환을 확인했다. 지원·연락처 이메일은 `phoenixai.sw@gmail.com`이다. 기존 `aibridge-web`에 입력했던 미저장 원본 추가는 취소했고 공유 ID도 제거했다. 운영 도메인의 실제 Google 팝업 로그인은 사용자 확인을 요청한 상태이며 성공으로 기록하지 않는다.

Toss 테스트 키는 참조 설정에서 확인했지만 같은 상점 MID와 자동결제 계약은 미확인이다. 실제 카드 등록·승인·취소 또는 라이브 청구를 수행했다고 기록하지 않는다. 결제 비활성 설정을 유지한다.

공개 규격에 따른 검토 PDF 검증과 제조 승인·CMYK·PDF/X·윤곽선·실물 바코드 등급은 구분한다. 제조사 자료 요청문은 폐기했으며 공개 규격만으로 실제 제조 승인을 생성하지 않는다.
