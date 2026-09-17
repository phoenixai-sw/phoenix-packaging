# 정적 SVG 가져오기

2026-09-18. 원문 작업지시서 §17의 ‘정화한 SVG’ 업로드를 정적 도형 범위로 구현했다. 화면·3D·검토 PDF·제작 출력은 동일한 PNG 자산을 사용한다. SVG를 편집 가능한 도형으로 분해하거나 SVG 안의 글자를 다시 조판하는 기능은 아니다.

## 사용 방법

1. 편집기의 **이미지** 또는 브랜드의 **로고 이미지**에서 `.svg`를 선택한다.
2. SVG의 글자는 제작에 사용한 원래 글꼴로 윤곽선을 만든다. 편집 가능한 문구는 플랫폼 텍스트 도구에서 별도로 추가한다.
3. 폭·높이는 px/mm/cm/in/pt/pc 또는 viewBox로 지정한다. 원본 SVG는 1MiB 이하이며, 변환은 한 변 1~8192px·전체 1,600만 픽셀 이하다. 실제 크기로 렌더하며 해상도를 임의로 높게 기록하지 않는다. 물리 단위는 SVG의 96dpi 기준으로 픽셀에 대응한다.
4. 성공하면 PNG로 변환된 자산이 표시된다. 정화한 SVG는 비공개 원본 자료로 함께 보관한다. 편집 ZIP에는 PNG와 정화 SVG·변환 계보가 함께 들어간다.

초기 지원은 path, rect, circle, ellipse, line, polyline, polygon, 그룹·변환, 내부 그라디언트·클립과 제한된 인라인 스타일이다. title/desc/metadata, 에디터 전용 정보와 비표시 속성은 제거한다. 스타일은 표현 속성보다 우선하도록 정규화한다.

스크립트·이벤트·외부 URL·파일 참조·data URL·내장 이미지·use 참조·필터·애니메이션·외부 스타일·중첩 SVG는 반려한다. SVG text/tspan/글꼴은 `SVG_TEXT_OUTLINE_REQUIRED`로 반려한다. 위험 요소를 빼고 전혀 다른 모양을 성공으로 저장하거나 운영체제 글꼴로 대체하지 않는다. 지원하지 않는 기능은 고객이 원본에서 정리한 뒤 다시 올려야 한다.

## 저장과 권한

- 기존 `POST /v1/assets`와 검역 업로드 `/v1/assets/uploads`의 `image/svg+xml` 입력을 사용한다. 반환 자산은 `image/png`, `source=svg_import`이며 기존 이미지 배치·crop·PPI 계산을 그대로 사용한다.
- 원시 업로드의 SHA256, 정화 결과 SHA256, 실제 변환 픽셀, 렌더러 버전과 제거한 비표시 요소를 `metadata.svg_import`에 기록한다. AI 모델·원가·인쇄 승인 기록과 구분한다.
- 정화 SVG는 별도 `Asset(source=sanitized_svg)`에 저장하고 표시 PNG의 `source_asset_id`로 연결한다. 두 파일 모두 동일 tenant/작업공간이며 실제 바이트 합계를 200MiB 한도에 반영한다. 원시 위험 SVG는 서비스하지 않으며 기존 임시 검역 삭제 수명을 유지한다.
- 자산 목록은 정화 원본 행을 숨긴다. 해당 UUID를 알아도 일반 장면의 이미지로 직접 배치할 수 없다. 원본 다운로드는 같은 자산 ACL을 거쳐 attachment/octet-stream으로 반환한다.
- 편집 ZIP은 원본을 provenance 역할로만 허용하며 SHA·크기·치수·정화 결과의 동일 바이트를 재검증한다. PNG를 바꾸거나 SVG를 다시 해석해 PDF를 만드는 경로는 없다.
- 백업은 두 Asset의 파일을 모두 포함한다. 기존 Asset 메타데이터 참조 검사가 정화 SVG의 삭제를 막으며, 복원 앱도 SVG의 정화·해시·치수를 확인한다. SVG를 보관한다고 추가 이용권이나 제조사 승인이 생기지 않는다.

## 실행 제한과 검증

XML은 DTD·엔티티·외부 참조를 금지한 defusedxml로 읽은 뒤 허용 요소와 속성만 있는 새 트리로 만든다. 정화 결과는 반복 검사해도 바이트가 같은 canonical 형식이다. 원본 1MiB, 2,000개 요소, 깊이 24, 수치 50,000개, 명시 경로 명령 10,000개, 누적 변환 계수 범위를 검사한다. 캔버스 픽셀×중첩 예산은 6,400만이다.

PNG 렌더는 고정 코드의 별도 Python 프로세스에서 수행한다. SVG 문자열만 stdin으로 전달하며 파일·URL·글꼴·스타일 경로를 받지 않는다. 시스템 글꼴을 읽지 않고 프로세스 환경에 DB/API 비밀값을 전달하지 않는다. 8초를 초과하면 프로세스를 종료한다. Linux에서는 주소공간 512MiB 상한도 적용한다. 결과 PNG를 완전 디코드하고 크기·형식·20MiB 상한을 다시 검사한다.

의존성은 `resvg-py==0.5.0`과 `defusedxml==0.7.1`이다. 2026-09-18 PyPI 배포의 Windows x64 wheel은 약 1.24MB, Linux x64 wheel은 약 1.41MB다. 외부 Cairo 설치가 필요 없는 Rust 렌더러이며, 서버리스의 vendored 의존성 경로를 부모 프로세스에서 확인해 격리 자식에 전달한다. 실제 운영 Linux 업로드 동작은 배포 QA에서 별도로 확인한다.

검증 파일은 `services/api/tests/test_svg_import.py`다. 실제 색·곡선·그라디언트·클립 렌더, 스타일 우선순위, viewBox-only canonical 재검사, XML/스크립트/외부 참조·data URI·재귀 클립·글꼴 거부, 좌표/크기/복잡도 경계, 실제 프로세스 종료, 동시 완료의 불변성, 원본 포함 할당량, tenant ACL, 원본 직접 배치 차단, PNG 검토 PDF, 원본 포함 편집 ZIP과 암호화 복원 앱을 검사한다. 최종 SVG 48개가 57.88초에 통과했다. 실제 PNG·정화 SVG·검토 PDF·편집 ZIP 4개 파일을 암호화 백업하고 격리 앱에서 자산 2개·출력 2개를 다시 확인했다. 앞선 기존 업로드·편집 ZIP 포함 80개 집중 회귀도 통과했다(중복을 더해 총합으로 세지 않는다). 웹 TypeScript 검사와 diff 공백 검사도 통과했다. 운영 배포와 실제 UI의 결과는 릴리스 기록에서 별도로 구분한다.

실제 UI 업로드용 자체 제작 자료는 `fixtures/svg/phoenix-static-mark.svg`다. 기존 고객 로고나 상품 디자인이 아닌 정적 도형 시험 자료다. 이 PC의 `.local/svg-qa/static-mark.png`로 변환한 1200×900px 결과를 직접 확인했다.

## 공식 근거

- [resvg-py API](https://resvg-py.readthedocs.io/en/latest/api.html): SVG 문자열→PNG, 시스템 글꼴 제외 등 호출 계약.
- [resvg 프로젝트](https://github.com/linebender/resvg): 정적 SVG 렌더링·Rust 기반 구현 범위.
- [resvg-py 라이선스](https://github.com/baseplate-admin/resvg-py/blob/master/LICENSE): MIT 배포 조건.
- [defusedxml](https://github.com/tiran/defusedxml): XML 엔티티·외부 자원 공격 방어. 이 라이브러리만으로 SVG 실행 안전성을 주장하지 않고 별도 allowlist와 실행 예산을 적용한다.

자료 확인일: 2026-09-18. 기능 검증과 실제 제조사 입고 승인은 별개다.
