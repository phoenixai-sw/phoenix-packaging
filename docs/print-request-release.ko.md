# 인쇄 의뢰본 출력과 칼선 레이어 합본 PDF

2026-09-18. 검토 PDF(RGB)와 제조 승인 게이트 사이에 있던 빈틈을 메운다. 제조사 승인 없이도 인쇄소에 보낼 수 있는 CMYK 파일을 만들되, 승인 기록이 없다는 사실은 그대로 남긴다.

## 이번에 실제로 된 것

| 항목 | 내용 | 확인 |
| --- | --- | --- |
| 인쇄 의뢰본(`print_request`) 모드 | 편집기 → 검수와 출력 → **CMYK 인쇄 파일** 패널에서 “인쇄 의뢰본 ZIP” 선택. 시험 ZIP과 같은 엔진(ICC CMYK·정적 TTF 윤곽선·도련·CUT/FOLD/PROCESS 분리)이지만 “제작 사용 불가” 표시와 12mm 하단 띠가 없다. 0크레딧 | `tests/geometry_pdf/test_print_request.py` 3건, 로컬 UI 패널 렌더 |
| 실제 ICC 강제 | 합성 시험 ICC(`synthetic-cmyk-engine-test-v1`)로는 의뢰본을 만들 수 없다(`TEST_ICC_PRODUCTION_FORBIDDEN`). 관리자가 등록하고 시험 공개한 실제 ICC 프로필만 `print_request_available=true` | API 시험 |
| 칼선 레이어 합본 `artwork-with-dieline.pdf` | 시험·의뢰본·제작 ZIP 모두에 추가. `Artwork`·`Dieline` 두 OCG 레이어, 칼선은 `CutContour`(M100 표시), 접힘선은 `Crease`(C100 표시) 별색. cut.pdf/fold.pdf와 같은 좌표를 0.01mm 허용오차로 검증하고 `verification.combined_file`에 기록. 별도 CUT/FOLD 파일이 여전히 기준 파일 | `test_print_engine*`, `test_print_finishing`, 로컬 UI 시험 ZIP(segments_matched=8) |
| 검수 경고 정책 | 의뢰본은 시험과 같이 저PPI·샘플 바코드·도련 부족을 **막지 않고** `preflight.json`/manifest `issues`에 경고로 남긴다. 제작(production) 게이트는 그대로 오류로 차단 | 코드 `inspect_print(print_request=True)` |
| 매니페스트 | `kind: print_request`, `review_only: false`, `manufacturer_approval: false`, `pdf_x_conformance: not_claimed`, `combined_file` 항목 추가. Job 결과 `format: print_request_zip`, 파일명 `phoenix-print-request-<job>.zip` | 계약 재생성·drift 검사 통과 |

## 아직 아닌 것 (외부 자료·사용자 결정)

- **실제 ICC 등록**: 운영·로컬 모두 등록된 실제 ICC가 없어 의뢰본 메뉴는 “실제 ICC 프로필이 필요합니다” 안내만 보인다. 사용자 PC `C:\Windows\System32\spool\drivers\color\JapanColor2001Coated.icc`(Adobe, 문서 임베드 허용)가 있으니 관리자 메뉴 “ICC 등록 → 프로필 등록(시험 공개 켬)” 순서로 올리면 바로 쓸 수 있다. 무료 재배포 가능한 대안은 ECI `PSO Coated v3`/`ISO Coated v2 300%`. 어느 것을 쓸지는 인쇄소가 요구하는 조건에 맞춘다.
- **제조사 승인·실물 검수**: 의뢰본은 승인 기록이 없다. 인쇄소가 도면·색상을 확인한 뒤 제작 게이트(승인 도면·프로필·`ENABLE_PRODUCTION_EXPORT`)를 열어야 “제작용 출력”이 된다.
- PDF/X 선언, 화이트 잉크, 오버프린트, 반투명은 여전히 미지원. 합본 PDF의 별색은 칼선 표시용 기술 별색이며 인쇄 잉크 별색 지원이 아니다.
