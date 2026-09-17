# 장면 이력과 편집 권한

서버는 프로젝트마다 로그인 세션과 탭별 `editor_id`에 묶인 편집 권한을 최대 120초 유지한다. 같은 계정의 다른 탭도 자동으로 소유권을 공유하지 않는다. 클라이언트는 편집 화면 진입 시 선점하고 30초마다 갱신한다. 종료 시 해제를 시도하되 네트워크가 끊기면 만료로 회수한다. 강제 소유권 빼앗기는 제공하지 않는다.

| 경로 | 계약 |
|---|---|
| `GET /v1/projects/{id}/edit-session?editor_id=UUID` | 상태·편집 가능 여부·보유자 이름·만료시각. 토큰은 노출하지 않음 |
| `POST /v1/projects/{id}/edit-session` | `{editor_id}` 선점, 같은 세션/탭 재요청은 같은 토큰 유지 |
| `PATCH /v1/projects/{id}/edit-session` | `{editor_id,lease_token}` heartbeat, 만료 후에는 새 선점 필요 |
| `DELETE /v1/projects/{id}/edit-session` | 같은 본문의 해제, 이미 해제된 요청은 멱등 처리 |
| `GET /v1/projects/{id}/revisions?limit=30&before_number=N` | 내림차순 keyset pagination. `items`, `next_before_number`, `has_more`, `total`, `current_revision` |
| `GET /v1/projects/{id}/revisions/{revision_id}` | 하나의 전체 Scene 스냅샷 |
| `POST /v1/projects/{id}/revisions/{revision_id}/restore` | `{base_revision}`와 편집 헤더, 복원 후 새 프로젝트 payload |

기존 이력 목록은 기본 100개와 기존 `items`의 Scene을 유지한다. 새 UI는 `include_scene=false`로 메타데이터만 페이지 조회하고 선택한 항목의 detail에서 Scene을 읽어야 큰 장면 여러 개가 호스팅 응답 크기 제한을 넘는 일을 피할 수 있다. 마지막 번호보다 작은 번호를 다음 페이지에서 읽으므로 중간에 새 저장이 추가되어도 이전 페이지 항목이 밀려 중복되지 않는다. 기록이 존재하지 않는 오래된 시점을 만들어내지는 않는다.

편집 요청의 헤더는 `X-Editor-Lease`다. 초안·수동 스냅샷·복원·상품 연결 반영·출력 설정 변경과 새 AI 견적/작업·출력·품질 파생자산 생성이 보호된다. 이미 접수된 worker는 브라우저 탭 종료에 종속되지 않는다. 프로젝트·이력·기존 작업·파일 읽기는 기존 조직/작업 공간 권한에 따라 허용한다. viewer는 편집 권한을 선점할 수 없다.

다른 편집자의 활성 권한과 충돌하면 `423 EDIT_LEASE_HELD`, 만료·해제된 토큰을 그대로 사용하면 `423 EDIT_LEASE_EXPIRED`다. 리비전이 달라졌으면 기존 `409 REVISION_CONFLICT`다. 구버전 클라이언트는 활성 권한이 없고 헤더도 없는 경우에만 기존 CAS 저장을 계속할 수 있다. 활성 권한이 있는 동안 헤더를 빼서 우회할 수 없다.

복원은 과거의 얼굴·객체·색상·가공 설정·상품/브랜드 연결을 새 리비전에 복사한다. 현재 프로젝트의 권한 범위·물리 규격·현재 인쇄 승인 설정을 임의로 되돌리지 않는다. 과거 상품/브랜드·이미지에 대한 현재 접근 권한과 치수·Scene 스키마를 다시 검사한다. `reviewed_face_ids`와 `confirmed_fields`는 비우므로 검수와 상품 확인을 다시 해야 한다. 과거 리비전과 과거 출력물·과금 기록은 변경하지 않으며 `revision_restored` 감사 이벤트에 이전/원본/새 리비전을 연결한다.

## 마이그레이션과 배포

`0008_editor_sessions`는 `0007_google_auth` 이후 빈 `project_edit_leases` 테이블과 인덱스만 추가한다. 기존 장면·이력·계정·세션을 수정하지 않는다. PostgreSQL에서는 RLS를 켜고 PUBLIC·anon·authenticated 직접 접근을 철회한다. 임대는 인증 정보가 아니며 세션 쿠키·CSRF·조직/작업 공간 검증을 대체하지 않는다.

운영 적용은 기존 환경 검증을 통과한 설정으로 암호화 백업을 확보한 뒤, 웹/API 배포를 준비하고 `python -m alembic -c services/api/alembic.ini upgrade head`를 실행한 후 API와 웹을 순서대로 전환한다. 코드만 먼저 전환하면 아직 없는 테이블을 조회하므로 금지한다. 기존 API는 추가 테이블을 사용하지 않아 이전 코드로 되돌릴 수 있지만, 새 API가 요청을 받고 있는 동안 테이블을 downgrade하면 안 된다. 이 작업에서는 운영 DB 마이그레이션이나 배포를 실행하지 않았다.

회귀 시험은 `services/api/tests/test_editor_sessions.py`에 있다. 같은 로그인 두 탭/다른 로그인·선점 경합·만료·갱신·해제·CSRF·타 조직/작업 공간·viewer·100개 초과 이력·페이지 중간 신규 저장·전체 장면 복원·CAS·과거 자산 권한·기존 mutation 경로와 새 AI 요청 방어·추가 마이그레이션을 검증한다. 실제 운영 두 탭/네트워크 단절 UI 검증은 배포 후 별도 수행한다.

2026-09-18 실행: 새 13개 시험과 기존 AI·제작 worker·백업 회귀 합계 57개 통과(136.74초), 기존 API·팀·마이그레이션 26개 통과(50.34초). 메타데이터 전용 페이지와 범위 초과 cursor 검증도 추가 후 해당 시험을 다시 통과했다. 이 수치는 이번 변경의 집중 회귀이며 플랫폼 전체 시험 결과로 표기하지 않는다.
