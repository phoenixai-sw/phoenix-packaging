# P2–P5 검증 범위와 외부 조건

2026-09-16. 이 문서는 구현된 동작과 자동 검증을 기록한다. 실제 PG 상점 승인, 제조사 입고, 실물 인쇄, 운영 데이터 복구 완료를 자동 테스트로 대체하지 않는다. 배포 후 실제 실행 결과는 `docs/acceptance-results.md`에 추가한다.

검증 파일 약어:

- **C**: `services/api/billing/tests/test_credits.py`
- **P**: `services/api/billing/tests/test_payments.py`
- **A**: `services/api/tests/test_ai.py`
- **W**: `tests/geometry_pdf/test_production_worker.py`
- **S**: `tests/geometry_pdf/test_structures_production.py`
- **API / 팀 / 업로드**: `services/api/tests/test_api.py`, `test_business.py`, `test_uploads.py`

| 항목 | 자동 증빙과 실제 검증 범위 |
|---|---|
| AC14 · 합계 80 | C `test_standard_three_edit_and_first_production_cost_80`: 표준 3장+수정 1장+최초 제작 1건의 서버 비용과 원장 합계. |
| AC15 · 부분 성공 | C 부분 정산 + A `test_three_images_capture_twenty_release_ten_and_replay`: 실제 DB 작업 3장 중 2장만 저장·공개, 20확정·10반환. |
| AC16 · 중복·재시작 | A 동시 동일 요청·늦은 lease 결과·불확실 응답 재생성 금지; W 최초 묶음·무료 재출력; P `test_renewal_order_survives_worker_death_after_provider_approval`와 최초 빌링 종료 복구. |
| AC17 · 30에서 20 동시 요청 | C `test_concurrent_reservations_cannot_overdraw_30`. 실제 Supabase PostgreSQL에서도 한 요청20예약·다른 요청거절·반환 후30 복구 확인(`.local/cloud-ledger-regression.json`). |
| AC18 · 동일 항목 재출력 | C 제작 지문 테스트 + W `test_real_worker_bundle_capture_and_free_repeat`: 최초40·같은 지문0, 새 검수·작업·리비전 처리. |
| AC19 · 새 제작 항목 | C 지문 테스트: 내용량 정규화, 바코드·치수·구조·가공 변경 구분. 실제 변경별 제조사 출력 시험은 별도다. |
| AC20 · 미완료 묶음 무차감 | W `test_failure_returns_reservation_without_entitlement`와 업로드 후 승인 철회 시험; S 6개 파일·manifest 해시; A 저장 실패·SQL 공개 실패 시 정산 롤백. 완료된 AI 이미지의 사후 소실·손상은 아래 주기 검사·보상 테스트로 검증한다. |
| AC21 · 월분 만료·구매분 유지 | P 월분 갱신·구매500보존·결제당 지급1회·실패한 갱신 무지급·동시 스케줄러 시험. |
| AC22 · 만료 직전 예약·소실 | C FEFO·예약분 중복 만료 방지·원 이용 범위의7일 보상; A 만료 lease·늦은 결과 무차감. |
| AC23 · 달력·상향·하향 | P 31일·윤년·원화 반올림/크레딧 내림·원 요금제 변경 시 오래된 견적 차단·다음 주기 하향·고유 invoice. |
| AC24 · PG 응답 유실·웹훅 | P 주문/금액/통화/MID/paymentKey 검증, 위조·중복 알림 재조회, 응답 유실, 지연 재조회, 승인 직후 종료 복구. HTTP MockTransport와 모의 PG 증빙이며 실제 Toss 상점 검증은 미실시다. |
| AC25 · 조직 격리 | API 타 조직 프로젝트·자산·job·파일 차단; 팀의 작업 공간 접근 제한; 업로드 완료의 조직/요청자 검증; W 타 조직 제작 견적 차단. |
| AC26 · viewer·플랫폼 관리자 | API `test_viewer_cannot_edit_upload_or_export`, 팀 일반 소유자의 관리자 승인 차단. 서버의 변경 역할·관리자 검사는 구현됐지만 모든 viewer API 조합을 각각 열거한 실기 시험은 아니다. |
| AC27 · 체험 | C `test_trial_once_14_days_scope_and_no_auto_conversion`: 가입30/14일·중복 지급 방지·체험 제작 차단·자동 결제 없음. |
| AC28 · 해지·잔여 구매분·팀 | P `test_cancel_no_new_invoice_owner_retains_purchase_and_team_loses_access`; 팀 구독 종료/멤버 제거 시 차단과 자기 조직 복귀. 보관 기한 계산과 소유자 접근은 유지하며 자동 파일 삭제는 실행하지 않는다. |
| AC29 · 악성 입력 | API SVG·위장 MIME·외부 URL·장면 추가 스크립트 속성 차단. 업로드20MiB·실제 바이트·픽셀 폭탄·격리·동시 완료·할당량 재검증. SVG 업로드는 허용 목록 밖으로 거절한다. |
| AC30 · 제조사10건 | `test_printer_intakes.py`15개: 기술/미적 분류, 동일 작업 중복 응답, 기술 반려 이력 보존, 시험/제조사/이전 기록 구분, 리비전·승인 조건 연결, 고유 작업 기준 기술 통과율. 실제 제조사10건·실물 바코드 판독·색상 검증은 미실시다. |
| AC31 · 백업 복구 | 최신 클라우드40테이블·98행·11객체를 암호화 백업하고 별도 로컬SQLite/객체로 복원했다. 기존 QA 로그인, 장면5개·리비전11개·원장5개·자산4개·PDF6개 재열기와 원본 값/FK/해시를 확인했다. PostgreSQL 물리/PITR 복구는 미실시다. |
| AC32 · 유료 운영 게이트 | P 호스팅 mock 차단·정책 false 라이브 차단·라이브/테스트 환경 분리; API production demo/fixture 차단; S 승인·출처·재질·제조사 실규격·지원 출력 능력 검사. `pricing.seed.json`의 `live_billing_enabled:false`를 두 환경 플래그만으로 우회할 수 없다. |

## 추가 신뢰성 검증

`services/api/billing/tests/test_outbox.py`는 내부 결제 알림 소비의 동시성·중복 방지와 영구 작업이 없는 예약의 미확인을 검증한다. SQL job 테이블은 native/Celery/cron이 함께 사용하는 영구 큐다. 예약 outbox 확인은 기존 작업을 확인할 뿐 새 작업이나 외부 제공자 요청을 만들지 않는다.

`services/api/tests/test_worker_dispatch.py`는 분배기 한 곳이 실패해도 다른 큐 처리를 계속하고, 보호된 API가 내부 예외·비밀값 없이 재시도 가능503을 반환하는지 검증한다. 결제 실패·사용자 작업 실패는 각 영구 상태에 기록하고, 분배기 자체 오류와 구분한다.

`services/api/tests/test_asset_reconciliation.py`는 완료된 AI 이미지의 소실·같은 해시 손상을5분 이상 간격으로 두 번 확인한 뒤 같은 이용 범위로 한 번만 보상하고, 결과 공개·파일 다운로드를 차단하는지 검증한다. 타임아웃·5xx·권한·버킷 오류는 보상하지 않으며 정상 응답이나 불확실한 응답은 이전 의심 증거를 초기화한다. 두 worker의 동일 관측 중복, 체험 만료 후7일 복원, 일부 결과만 손실, 정상 파일24시간 검사 간격, Supabase 스트림 오류 본문과 별도 객체 부재 확인을 포함한다. 기본 검사량은 실행당2개이고 자동 재생성·재청구는 없다. 실제 클라우드 고객 파일을 삭제하는 파괴 시험은 수행하지 않았다.

실제 PostgreSQL 원장 동시성 증빙은 `.local/cloud-ledger-regression.json`, 작업 전 백업 검증은 `.local/backups/before-platform-20260916/verification.json`이다. 이 경로들은 Git 제외이며 백업 키·계정·서명 URL을 문서에 포함하지 않는다.

## 외부 검증과 남은 운영 조건

- Toss 테스트/라이브 상점 키와 계약, 실제 카드 인증·최초 승인·갱신·취소·웹훅 시험, 요금·환불 운영 정책 확정. 현재 라이브 결제는 닫혀 있다.
- 클라우드 SMTP 실발송·수신. 로컬 outbox 기반 인증/비밀번호 복구 자동 검증은 실제 메일 전달률 검증이 아니다.
- 제조사 출처가 있는 실규격과 승인, 실제 제출10건, 실물 치수·공차·색상·바코드 판독. 제작 능력은 검증된 RGB·내장 글꼴 범위이며 PDF/X·CMYK·별색·윤곽화·제작 구멍 칼선은 지원되지 않는 상태에서 차단한다.
- 실제 Windows 한국어 IME 조합 중 Enter·초점 이동. DOM 문자열 주입이나 장면 단위시험만으로 통과를 주장하지 않는다.
- 전체 최신 데이터의 별도 PostgreSQL/객체 저장소 복구 후 애플리케이션 로그인·재열기, 정기 백업 운영 점검과 복구 시간 측정.
- 이미지 제공자 장기 원가·품질/지연 분포·제작 시간 절감·반려율의 실제 측정. 배포 후 앱에서 GPT Image2.5 Sunburst 생성·편집 각1회와20크레딧 차감·원본 해시 유지·PDF 적용을 실제 검증했다. 제공자 요청ID·usage·추정US$0.115447을 기록했다. 두 표본을 전체 상업적 품질 보장으로 해석하지 않는다.

완료된 AI 이미지의 주기 검사·자동 보상은 구현·자동 검증됐다. 제작 ZIP을 포함한 모든 보관 자산의 사후 감시 및 원본 파일 복구까지 같은 범위로 검증한 것은 아니다. 자동 삭제·부분 환불·미확정 추천 지급은 켜지 않았다. P4 유료 시험과 P5 제조사 승인 박스는 외부 승인·실물 검수를 마치기 전 완료로 표시하지 않는다.

객체 부재 판정은 [Supabase Storage 공식 오류 정의](https://supabase.com/docs/guides/storage/debugging/error-codes)의 권한 관련 모호성을 고려한다. HTTP404 하나만으로 손실을 확정하지 않는다.
