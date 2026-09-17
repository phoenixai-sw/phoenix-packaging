# 유료 운영 준비 상태

플랫폼 기능은 구현했지만 현재 운영은 staging이다. 실제 요금 청구와 제작 출력은 잠겨 있다. Supabase 비용 승인은 MICRO 기본 월 약 US$10 범위다.

## 연결 완료

- GitHub 비공개 저장소, Vercel 웹·API와 Supabase Pro 서울 프로젝트
- PostgreSQL 마이그레이션·RLS, 비공개 확정/검역 버킷, 암호화 환경변수
- 서버 AI 공급자·모델·계정 제한·일일 호출 상한
- 크레딧·결제 원장, 작업 복구, 승인 증빙·제작 검사, 운영 게이트

## 운영 전 확인

1. Toss 테스트 키·상점 정보로 가상 결제·환불·빌링을 검증하고 라이브 자동결제 계약을 완료한다.
2. Google Client ID의 JavaScript 원본에 배포 주소를 등록하고 실제 로그인·ADMIN_EMAILS 권한을 확인한다. 비밀번호와 인증메일 절차는 제거했다.
3. 직접 조사한 공개 기본 규격표로 출력 엔진을 검증한다. 공개 규격의 숫자 검사와 실물 출력·바코드/색상 검수, 실제 제작 승인 기록은 구분한다.
4. 가격·부가세·환불·정기결제 고지, 개인정보·이용조건을 확정한다.
5. 로컬 논리 복원에서 확인한 앱 재열기를 운영 PostgreSQL 복구 환경에서도 실증한다. 모니터링과 복구 담당자를 정한다.

Google·Toss 설정은 [연결 안내](owner-setup.ko.md)에 있다. 제조사 자료 요청문과 Resend 가입 요구는 폐기했다.

## 서버 게이트

실제 결제는 production 환경, 라이브 키·MID, `BILLING_POLICY_APPROVED`, `LIVE_BILLING_ENABLED`, 가격 파일의 `live_billing_enabled`가 모두 유효해야 한다. 테스트·모의 결제로 우회할 수 없다.

제작은 `ENABLE_PRODUCTION_EXPORT`, `OPERATING_POLICY_APPROVED` 외에도 실제 승인 템플릿·프로파일·증빙, 정확한 치수·재질, 최신 리비전·전 면 검토, 상품 식별과 지갑 검사를 통과해야 한다. 현재 엔진은 RGB·임베드 글꼴·면별 PDF를 지원한다. PDF/X·CMYK·별색·백색판·오버프린트·글꼴 아웃라인 등 지원하지 않는 조건은 차단한다. 제조 구멍 재단선도 지원 완료로 보지 않는다.

## 백업과 복원

`scripts/backup-platform.py`는 애플리케이션 테이블·연결된 Storage 객체를 암호화하고 격리 SQLite에서 행·열 값·외래키·파일 해시를 확인한다. 키는 백업 디렉터리 밖에 보관한다. 최신 클라우드40테이블·98행·11객체 복원 검사를 통과했다. 별도 로컬 파일/DB 환경에서 기존 QA 로그인과 장면·리비전·원장·이미지·PDF 재열기도 통과했다.

이 도구는 PostgreSQL 물리 백업/PITR을 대체하지 않는다. DB 백업에 Storage 객체가 포함된다고 가정하지 않는다. 운영 PostgreSQL 복구 프로젝트에서의 재검증은 남아 있다.

## 롤백

직전 정상 Vercel 웹/API 배포로 함께 되돌린다. 데이터가 있는 DB에 자동 downgrade를 실행하지 않는다. 백업·호환성 확인 후 순방향 migration을 우선한다. 키는 Git에 기록하지 않는다. 기존 환경 갱신은 `scripts/update-cloud-env.py`를 사용한다.
