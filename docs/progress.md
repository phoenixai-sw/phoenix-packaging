# 진행 현황

## 2026-09-18 현재 갱신

**운영은 PR8까지 배포됐고, PR9는 로컬 실동작·프리뷰를 확인한 배포 후보다. 원문 전체 완료가 아니다.** 전용 Google 운영 로그인·관리자 접근, 실제 AI 생성/글자 제거·검토 PDF와 Sunburst/Flare·6품질 선택은 이미 검증한 운영 범위다. 아래 옛 ‘Google 로그인 대기’나 당시 시험 수를 현재 상태로 해석하지 않는다.

| 구분 | 반영 상태 | 확인한 검증 |
| --- | --- | --- |
| [PR7 편집 보완](https://github.com/phoenixai-sw/phoenix-packaging/pull/7), `49e7c27` | 운영 웹/API 배포 완료 | Python531·프런트57·타입/빌드 통과. crop·잠금·정렬·저장본 복원·두 탭 편집권·OCR 초안 재개·모바일390 검수, 운영 저장본 비교/잔액0 확인 |
| [PR8 ZIP·구조·AI 맥락/예산](https://github.com/phoenixai-sw/phoenix-packaging/pull/8), `7f4e81f` | 운영 웹/API 배포 완료, DB0010 | Python612·프런트66·타입/빌드·CI 통과. 운영 저장본6의 ZIP12파일·9.8MiB 생성/다운로드 및 전체 장면·파일 해시 확인. 추가 유료 AI0·잔액0 유지 |
| [PR9 운영 도구·출력 보완](https://github.com/phoenixai-sw/phoenix-packaging/pull/9), 초기 후보 `9907248` | **후보, 운영 반영 전** | 로컬 프런트78·타입·계약 drift 통과, 웹/API 프리뷰 빌드 성공. 전체 Python/최종 CI는 진행 중이며 통과로 세지 않음. 후속 안내 문구·문서는 별도 반영 예정 |

PR9 후보는 공개 HTTP141개 응답 DTO/OpenAPI/TS 계약, 정적 TTF·브랜드 허용·동일 글꼴 출력, ICC CMYK/윤곽선/CUT·FOLD, 보관·지원·삭제 요청·알려진 orphan GC, 별도 서비스 견적, 요금 정책·크레딧 정정, 내부 성과/비용, 정화 SVG, 검토 상태 전이와 완료 파일 승인 철회 안내를 포함한다. 미지원 PDF/X·별색·화이트·오버프린트·가공 제작은 계속 차단한다.

격리 로컬 실제 UI에서 다음을 확인했다. 글꼴 업로드→브랜드→저장/재열기→검토 PDF·편집 ZIP·CMYK 시험3건/8페이지, SVG 배치→저장본4의 원본 포함 ZIP·검토 PDF2페이지, 요금/단가 게시와 원복·3크레딧 지급/회수, 서비스6단계, 활동2분/fixture4건과 지원5→7분 정정, 삭제 요청의 보존 안내/취소, 초안→검토중과 증빙 없는 승인 비활성이다. 실제 제조 승인·운영 삭제·유료 AI 추가 호출은 하지 않았다.

최신 운영 논리 백업41테이블·293행·33객체를 격리 복원하고, 소유자 프로젝트7·저장본41·자산13·작업16·출력9·원장16·이미지 연결14개를 재열어 확인했다. 과거 manifest에 없던 필드를 보존하는 계약 호환 회귀도 반영했다. 원본 운영 데이터는 변경하지 않았으며 PostgreSQL 물리 복구/PITR 실증과 구분한다.

현재 실제 메뉴와 사진은 [PR9 후보 사용·검증 기록](spec-completion-ops-release.ko.md), 이전 편집/구조 증거는 [PR7 기록](editor-completion-release.ko.md)·[PR8 기록](spec-completion-exports-release.ko.md), 원문 항목별 상태는 [요구사항 추적표](spec-completion-tracker.ko.md)에 있다. 실제 PG 승인/갱신·제조사 입고10건·실물 품질·네이티브 Windows IME와 PR9 전체 CI/운영 이행을 완료로 집계하지 않는다.

## 2026-09-16~17 과거 기록 보존

아래는 당시 실행 결과를 그대로 보존한 이력이다. 현재 상태 판단에는 위 갱신과 연결된 추적표를 우선한다.

2026-09-17 변경 진행: Google 로그인·ADMIN_EMAILS, Toss 문서 MCP 기반 결제 동기화, 공개 인쇄 기본 규격으로 전환한다. Resend 가입과 제조사 자료 요청 절차는 폐기한다. 아래 P0–P5 및 클라우드 증빙은 2026-09-16 검증 기록이며 이번 변경 검증은 별도로 기록한다.

| 영역 | 구현 내용 | 남은 외부 검증 |
|---|---|---|
| P0 환경·계정·저장 | GitHub, Vercel, Supabase Pro DB·Storage, 세션·CSRF·RLS | 실제 Google 로그인, Docker 실기동 |
| P1 편집·검토 PDF | 한글·이미지·면별 편집, 서버 저장·두 탭 충돌·연결 실패 복구, 실제 크기 PDF | 네이티브 Windows IME |
| P2 AI·정산·결제 | GPT Image2.5 생성·편집, 작업 큐, 크레딧, Toss·구독·환불·재조회 | Toss 테스트 키·빌링 계약, 확정 요금 정책 |
| P3 제작·팀·관리자 | 스탠드 파우치, EAN-13, 구멍 검사, 3D, 증빙·승인, 제작 묶음, 팀·권한 | 제조사 도면·인쇄 조건·출력 승인 |
| P4 운영 시험 | 제조사 접수 기록, 운영 게이트, 암호화 백업·격리 로컬 복원 후 앱 재열기 | 제조사 실제10건, PostgreSQL 물리/PITR 복구 실증 |
| P5 박스·상품 확장 | 6면 박스, 상품·변형·복제, 연결·수동 문구 보존 | 승인 박스 규격 및 실물 검수 |

[웹](https://phoenix-packaging.vercel.app), [API](https://phoenix-packaging-api.vercel.app/v1/health), [저장소](https://github.com/phoenixai-sw/phoenix-packaging). Supabase `phoenix-packaging` / `jflpitqwnhznxmvwecnm` / 서울 / MICRO. 앱은 staging, 실제 결제와 제작 출력은 비활성이다. 확정 파일과 이미지 검역용 비공개 버킷을 분리했다.

실제 PostgreSQL에서30크레딧에20크레딧 예약 두 건을 동시에 요청해 한 건만 예약되고 다른 건은 부족 오류로 차단됨을 확인했다. 반환 후 잔액30, 예약0이었다. 모든 애플리케이션 테이블의 RLS도 확인했다.

최종 GitHub CI에서 Python210개·편집기5개·타입 검사·빌드가 통과했다. 실제 GPT Image2.5 생성·편집2회를 앱 작업 큐로 수행하여20크레딧 차감과 원본 유지, PDF 적용을 확인했다.3종 구조의 클라우드 PDF와4.6MB 비공개 직접 업로드도 통과했다.

마이그레이션 전 백업에 이어 최신 클라우드40테이블·98행·11객체를 암호화 백업했다. 별도 로컬 DB/파일로 복원 후 기존 QA 로그인·장면5개·리비전11개·원장5개·자산4개·PDF6개 재열기를 통과했다. PostgreSQL 물리/PITR 복구로 확대 해석하지 않는다.

실행 증빙은 [검수 결과](acceptance-results.md)에, Google·Toss 연결은 [연결 안내](owner-setup.ko.md)에 기록한다.
