# 등록 구조 V2: 실제 구현 범위

이 기능은 제조사별 치수·면 매핑을 서버가 해석하고 검토하는 첫 단계다. 등록·검증·검토 공개는 제조 승인과 별개다. 실제 제조사 승인 데이터는 추가하지 않았고, V2 제작 출력은 `STRUCTURE_V2_PRODUCTION_UNSUPPORTED`로 차단한다.

## 지원하는 정의

`StructureDefinitionV2`의 JSON Schema와 TypeScript는 `packages/contracts/structure-definition.*`에 있다. API는 알 수 없는 필드, 레시피 이름, 문자열 수식, 비유한 숫자, 범위를 벗어난 치수를 거부한다. 좌표는 왼쪽 위 원점 mm이며 네 자리까지 정규화한다. 추가 종속성이나 임의 코드 실행은 사용하지 않는다.

| 레시피 | 구현 | 제한 |
| --- | --- | --- |
| `three-side-seal-separated-v1` | 분리된 앞·뒤 패널, 폭·높이 범위, 좌우상하 개별 실링, 안전 여백, 패널 간격, 검토용 두께 | 완성 외곽 기준. 지퍼·노치·구멍 추가, 스탠드 거싯, 두께 보정 없음 |
| `fixed-panel-net-v1` | 삼방 2면·스탠드 3면·상자 6면, 고정 패널 치수·위치, 직사각 접착부·덮개, 접힘선, 등록 조립 위치 | 등록 치수 그대로 사용. 전개도 회전은 0/180도. 곡선 칼선·가변 두께 공식·실제 접힘 및 맞물림 검증 없음 |

박스 두께와 접착 여유는 명시적 메타데이터다. `inner`/`outer`/`panel` 기준과 등록된 패널 치수를 보존하며 자동으로 `2×두께`를 빼거나 더하지 않는다. 스탠드 `bottom_mm`는 펼친 거싯 폭이다. `fixed-panel-net-v1`은 숫자로 직접 매핑한 구조이며 업로드 PDF·SVG에서 가변 칼선을 자동 추출하는 기능이 아니다.

고정 구조는 면/부품의 겹침, 비어 있는 안전영역, 면 밖 접힘선, 분리된 구조 부품을 거부한다. 현재 직사각 구조 범위의 소프트웨어 검증이며 실제 접힘·생산 적합성 인증이 아니다. 등록된 3D 위치와 UV 방향도 실제 원단 팽창·두께·주름을 시뮬레이션하지 않는다.

첫 고정 상자 레시피는 서로 맞는 직육면체 6면 크기와 기존 바깥면 조립 축/위치를 요구한다. 임의로 어긋난 면이나 반대 방향의 3D 면을 정상 구조로 등록하지 않는다. 두께 때문에 면마다 다른 보정이 필요한 실제 상자는 이 단순 레시피에 억지로 넣지 않고, 후속 제조사 전용 레시피로 검증해야 한다.

## 관리자 등록과 사용자 적용

1. 관리자는 `POST /v1/admin/structures/validate`에 `structure_definition`을 보낸다. 선택적 `inputs`를 생략하면 고정 치수 또는 가변 범위의 최솟값으로 검사한다. `normalized_definition`, `geometry`, `definition_hash`를 반환하며 저장하지 않는다.
2. 기존 `POST /v1/admin/template-versions`에 출처·사용권·제조사명·구조 종류와 정규화된 `structure_definition`을 등록한다. `review_available:true`를 명시한 정의만 사용자 검토 목록에 공개된다. 기본값은 false다. 자체 시험 자료는 `is_demo:true`로 둔다.
3. 로그인 사용자는 `GET /v1/structures`에서 공개된 구조를 조회한다. 폐기한 버전은 신규 선택 목록에서 제외된다.
4. `POST /v1/structures/preview`에 `template_version_id`, `inputs`와 선택적 `project_id`/`base_revision`을 보낸다. 저장된 기존 디자인까지 검사하면 `layout_checked:true`, `layout_issues`, `can_apply`를 반환한다. 문제가 있는 첫 위치를 보고하며 전체 제조 적합성 보고서가 아니다.
5. `PATCH /v1/projects/{id}/structure`에 같은 버전·입력과 `base_revision`을 보내 적용한다. 편집 권한·테넌트·작업공간·편집 lease·CAS를 검사한다. 현재 구조 종류와 다른 종류로 바꾸지 않는다. 객체 문구·이미지·mm 위치는 그대로이며 새 안전영역과 충돌하면 422로 거부한다. 자동 축소·삭제는 없다.
6. 적용 성공 시 기존 인쇄 프로필과 모든 검토/표시사항 확인 체크를 해제한다. 제조 승인을 만들지 않는다. 실제 제작은 별도 지원 단계가 필요하다.

기존 지퍼·노치·구멍이 있는 데모를 V2로 적용하면 차단된다. 이 기능을 이용하기 위해 원래 디자인의 가공을 자동 삭제하지 않는다. 기존 데모 편집·가공·검토 출력은 계속 유지한다.

등록 예시(자체 시험 자료):

```json
{
  "schema_version": "2.0",
  "recipe_id": "three-side-seal-separated-v1",
  "family": "three-side-seal",
  "dimension_semantics": {"basis": "finished_outer"},
  "width_range_mm": {"minimum": 60, "maximum": 600},
  "height_range_mm": {"minimum": 80, "maximum": 800},
  "seals_mm": {"left": 8, "right": 12, "top": 6, "bottom": 14}
}
```

고정 상자의 실행 가능한 자체 시험 예시는 `tests/geometry_pdf/test_structure_v2.py`의 `fixed_box()`다. 기존 데모 숫자를 고정 등록한 테스트이며 특정 제조사의 실제 도면이 아니다.

## 저장·스냅샷·기존 데이터 호환

- `RegistryVersion.details`에 정규화된 정의와 해시를 저장한다. 승인에는 기존 증빙·자료 해시·승인자 조건을 유지하고 V2 정의 해시도 연결한다.
- migration `0010_structure_snapshots`는 Project/Revision에 nullable JSON 열만 추가한다. 기존 행은 NULL이며 자동 변환·승격·backfill하지 않는다. 적용 전 백업과 새 API 배포 순서를 지킨다. V2 프로젝트 또는 대기 작업이 있으면 이전 API로 무작정 되돌리거나 열을 삭제하지 않는다.
- `StructureSnapshotV2`는 정의·엔진 버전·정규화 입력·각 해시·해석 geometry를 함께 고정한다. 현재 엔진은 `structure-v2.1`이다. 이후 계산을 바꿀 때 새 엔진 버전을 추가하고 기존 버전의 검증 경로를 보존해야 한다.
- Scene에는 작은 `structure_ref`만 들어간다. 서버만 full snapshot을 쓴다. Scene-only 경로로 V2를 처리하려 하면 `STRUCTURE_SNAPSHOT_REQUIRED`가 나며 데모 도형으로 조용히 대체하지 않는다.
- 검토 PDF, 편집 ZIP, 이미지 품질 진단·보완, 바코드 배치, 공통 geometry payload가 같은 스냅샷을 사용한다. V2 바코드 배치는 `project_id`와 `base_revision`을 함께 보내 서버 구조를 조회한다.
- 복제는 snapshot까지 보존한다. 저장 이력 복원은 과거 문구·객체를 현 프로젝트의 구조에 검증해 복원한다. 현 구조와 맞지 않으면 거부하며, 과거 승인이나 확인 체크로 새 출력을 승인하지 않는다.
- 이미 생성한 작업은 고정 snapshot을 사용한다. 나중 레지스트리 상태·내용이 바뀌어도 과거 검토 PDF/ZIP을 다른 도형으로 다시 만들지 않는다. 신규 적용은 폐기 상태를 재확인한다. 제작 승인 시작/게시 전 재확인 흐름은 유지한다.

검토 출력은 기존 ReportLab 경로와 면/전개도 renderer를 사용한다. 면별 3mm 기본 검토 도련, 실제 글꼴 임베드, 한글 원문, 바코드 최종 디지털 판독을 유지한다. 구조 선은 검토 안내이며 제조용 별색 분판이나 커팅 머신 파일이 아니다. CMYK/PDF-X/outline/별색 capability는 이번 변경으로 활성화하지 않는다.

## 검증

집중 회귀 코드는 `tests/geometry_pdf/test_structure_v2.py`와 `test_structure_v2_api.py`다. 정의 경계·위변조·기존 해시 보존, 실제 PDF 치수/글꼴/한글/바코드, 저장·재열기·복제·이력 복원, 테넌트/lease/CAS, 레지스트리 변경 후 작업 동결, 편집 ZIP 원문 일치, migration 보존을 확인한다.

2026-09-18 로컬 검증 결과:

- 전체 geometry + 기존 image_quality 회귀 **180개 통과**(153.24초).
- 이후 상자 면/조립 방향 및 숫자·boolean 해시 변조 경계를 보강하고 V2 집중 **40개 통과**(43.35초). 두 실행은 중복되므로 합산하지 않는다.
- Scene/Structure JSON Schema·TypeScript 생성 결과 `--check` 통과.
- 실제 플랫폼 exporter로 자체 고정 상자 7쪽 검토 PDF를 생성했다. 6면과 전개도를 Poppler로 렌더링해 **7쪽 전부 시각 검사 통과**했다. 한글·방향·접착부·덮개·접힘·검토 표시에서 잘림이나 잘못된 제조 승인 표시는 없었다.
- 로컬 결과는 `output/pdf/registered-structure-review.pdf`, 검증 기록은 `.local/structure-v2-qa/verification.json`이다. PDF SHA-256은 `0bc83a0fb33dde2b733a8e6fcedc7804cd3214dae86ce6c4817134430c56e0c4`, 34,385바이트다. 로컬 파일은 별도 전달 산출물이며 저장소 문서 링크가 파일을 배포한다는 뜻이 아니다.
- 외부 유료 호출·실제 제조사 데이터 등록·운영 DB 변경·배포는 이 구현 검증에서 수행하지 않았다. 실제 UI 통합과 릴리스 전체 회귀는 별도 릴리스 기록으로 확인한다.
