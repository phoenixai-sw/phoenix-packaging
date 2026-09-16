# 진행 현황

2026-09-16 기준. P0–P5 플랫폼 기능을 구현하고 검증·배포를 진행했다. **개발 기능 구현과 상용 운영 승인은 별개**다. 제조사 자료나 실제 결제 계약 없이 유료 운영 통과로 표시하지 않는다.

| 영역 | 구현 내용 | 남은 외부 검증 |
|---|---|---|
| P0 환경·계정·저장 | GitHub, Vercel, Supabase Pro DB·Storage, 세션·CSRF·RLS | SMTP 계정·도메인, Docker 실기동 |
| P1 편집·검토 PDF | 한글·이미지·면별 편집, 서버 저장·두 탭 충돌·연결 실패 복구, 실제 크기 PDF | 네이티브 Windows IME |
| P2 AI·정산·결제 | GPT Image2.5 생성·편집, 작업 큐, 크레딧, Toss·구독·환불·재조회 | Toss 테스트 키·빌링 계약, 확정 요금 정책 |
| P3 제작·팀·관리자 | 스탠드 파우치, EAN-13, 구멍 검사, 3D, 증빙·승인, 제작 묶음, 팀·권한 | 제조사 도면·인쇄 조건·출력 승인 |
| P4 운영 시험 | 제조사 접수 기록, 운영 게이트, 암호화 백업·격리 로컬 복원 후 앱 재열기 | 제조사 실제10건, PostgreSQL 물리/PITR 복구 실증 |
| P5 박스·상품 확장 | 6면 박스, 상품·변형·복제, 연결·수동 문구 보존 | 승인 박스 규격 및 실물 검수 |

[웹](https://phoenix-packaging.vercel.app), [API](https://phoenix-packaging-api.vercel.app/v1/health), [저장소](https://github.com/phoenixai-sw/phoenix-packaging). Supabase `phoenix-packaging` / `jflpitqwnhznxmvwecnm` / 서울 / MICRO. 앱은 staging, 실제 결제와 제작 출력은 비활성이다. 확정 파일과 이미지 검역용 비공개 버킷을 분리했다.

실제 PostgreSQL에서30크레딧에20크레딧 예약 두 건을 동시에 요청해 한 건만 예약되고 다른 건은 부족 오류로 차단됨을 확인했다. 반환 후 잔액30, 예약0이었다. 모든 애플리케이션 테이블의 RLS도 확인했다.

최종 GitHub CI에서 Python210개·편집기5개·타입 검사·빌드가 통과했다. 실제 GPT Image2.5 생성·편집2회를 앱 작업 큐로 수행하여20크레딧 차감과 원본 유지, PDF 적용을 확인했다.3종 구조의 클라우드 PDF와4.6MB 비공개 직접 업로드도 통과했다.

마이그레이션 전 백업에 이어 최신 클라우드40테이블·98행·11객체를 암호화 백업했다. 별도 로컬 DB/파일로 복원 후 기존 QA 로그인·장면5개·리비전11개·원장5개·자산4개·PDF6개 재열기를 통과했다. PostgreSQL 물리/PITR 복구로 확대 해석하지 않는다.

최종 실행 증빙은 [검수 결과](acceptance-results.md)에, 외부 계정과 제조사 요청문은 [준비 안내](owner-setup.ko.md)에 기록한다.
