# ICC CMYK · 글꼴 윤곽선 출력 엔진

2026-09-18 개발 검증. 이 문서는 실제 기능과 개발 시험을 설명한다. 제조사 입고 승인, 특정 인쇄기의 색상 교정, PDF/X 적합성 인증 결과가 아니다. 운영 배포 여부는 릴리스 기록에서 별도로 확인한다.

## 사용할 수 있는 경로

1. 편집기의 **CMYK 출력 시험**에서 저장한 장면과 시험 조건을 선택한다. 무료 작업 큐가 ZIP을 만들고 완료 후 다운로드한다. 실패 시 동일한 불변 저장본을 다시 요청할 수 있다.
2. 관리자는 **ICC · CMYK 출력 조건 등록**에서 CMYK 출력 장치 ICC(4MiB 이하), 출처와 보관·PDF 임베드 사용권을 기록한다. 원본 바이트·SHA256·LittleCMS 변환 가능 여부를 검증한다. 출력 조건의 제조사·재질·도련·최소 해상도·총잉크량·면별/전개도 배치를 등록한다.
3. 새 조건은 **미승인**이다. 기존 제조 등록의 승인 증빙 확인을 거쳐야 제작 조건이 된다. 고객은 기존 제조 조건 선택 메뉴에서 승인된 도면·프로필·재질을 연결하고, 모든 면과 필수 표시사항을 확인한 뒤 제작 검수·견적·출력을 진행한다.
4. 제작 작업도 같은 ICC 변환·윤곽선·CUT/FOLD 어댑터를 사용한다. 작업 직전과 공개 직전에 도면·프로필·ICC·권한을 다시 검사한다. 실제 파일 보관을 확인한 후 결과 공개와 크레딧 차감을 한 트랜잭션으로 처리한다. 실패·철회 시 예약을 복원한다.

API는 `GET /v1/print-engine/profiles`, `POST /v1/print-engine/tests`, `POST /v1/admin/print-engine/icc`, `POST /v1/admin/print-engine/profiles`이다. 시험 작업은 기존 `review_export` 큐에 서버 소유 `print_output.mode=test`를 동결한다. 결과 `format=print_engine_zip`로 기존 PDF와 구분하며 기존 작업 조회·재시도·다운로드 경로를 사용한다. 원본 ICC를 고객 다운로드 API로 공개하지 않는다. ICC 원본은 증빙 보존 대상이며 자동 GC에서 제외한다.

## 실제 지원과 차단

| 항목 | 구현·검증 | 제한 |
|---|---|---|
| CMYK | Pillow/LittleCMS2로 RGB와 입력 ICC가 있는 원본을 등록 CMYK ICC로 실제 변환. 최종 벡터 DefaultCMYK·이미지 ICCBased·OutputIntent에 동일 ICC를 임베드하고 SHA 검증 | 프로필 등록은 인쇄 공정 교정·제조 적합성 확인을 대신하지 않는다. 무태그 CMYK는 차단한다. 무태그 RGB는 명시적인 sRGB 입력 정책 |
| 검정·총잉크량 | 벡터 순흑과 바코드는 K100, 순백은 0. 나머지 색·모든 이미지 픽셀의 변환 후 총잉크량 검사 | 특수 먹색 조합·오버프린트·별색 미지원 |
| 글꼴 | 번들 NotoSansKR 400/700 또는 브랜드에 허용한 정적 TTF의 실제 곡선을 벡터 경로로 출력. 동일한 원본의 실제 문자 폭·상하 기준·줄바꿈·간격 유지. 최종 아트 PDF에 활성 텍스트 연산이 없는지 확인. 원문은 manifest에 보존 | 아랍어·히브리어·조합형 자모·결합 문자처럼 별도 셰이핑이 필요한 윤곽선은 차단. 브랜드 글꼴은 검토 PDF에도 같은 제한을 적용한다. 윤곽선 아트에서 텍스트 검색·추출은 되지 않는다 |
| 도련 | 프로필 0~10mm, 3.175mm 포함. 실제 MediaBox/TrimBox/BleedBox 0.01mm 오차 검증. 면 바탕색 연장 | 현재 장면 편집 객체의 범위는 바깥 3mm까지다. 3.175mm 이상의 도련을 요구하는 가장자리 이미지·도형은 부족하면 제작 차단. 확대·합성 도련은 원본 디테일 복원으로 인정하지 않는다 |
| CUT/FOLD | `production.pdf`와 별개 `cut.pdf`/`fold.pdf`, 동일 크기·원점·배치. 직사각 패널/부품 외곽을 중복 없이 계산. 공유 접지선은 CUT에서 제외하고, 접지선이 없는 날개 공유 경계는 절개 | 수평·수직 선만 지원. 외곽 CUT와 FOLD 중첩, 빈 공간 통과, 패널 중첩 차단. 곡선 칼선·별색 CUT/CREASE 레이어는 미지원 |
| 구조 | 삼방 파우치 면별 페이지 또는 승인 증빙과 정의 해시가 연결된 등록 고정 전개도. 동결 기하·면 방향 사용 | 기본 데모는 제작 불가. 스탠드·박스에 면별 사각 CUT를 실제 전개도로 대용하지 않는다. 실제 제조 도면을 임의 추론하거나 승인하지 않는다 |
| 바코드 | 최종 CMYK PDF를 300dpi로 렌더하여 ZXing으로 EAN13 재판독 | 샘플은 시험만 허용. 실제 제작은 기존 상품 번호·소유 확인 조건 유지. 실물 인쇄 등급·스캐너 검증 별도 |
| PDF/X·화이트·가공 | 미지원 조건을 명시적으로 차단 | 일반 PDF의 ICC OutputIntent가 PDF/X 인증을 의미하지 않는다. `/GTS_PDFXVersion`을 쓰지 않는다. PDF/X, 화이트 잉크, 별색, 오버프린트, 반투명, 구멍/지퍼/노치 제작은 계속 차단 |

시험 ZIP은 아트·CUT·FOLD PDF, 미리보기 PNG, 검수 JSON, 해시 manifest의 6개 파일이다. 아트 파일명은 `production.pdf`지만 **시험의 모든 PDF에 별도 도련 밖 영역으로 `ENGINE TEST / 검토용 · 제작 사용 불가`를 표시**하며 manifest는 `review_only=true`다. 실제 제작 ZIP은 이 6개에 작업지시서 PDF/JSON을 추가한 8개다. 기존 RGB 출력기의 6개 파일 계약은 그대로 유지한다. manifest 자신을 제외한 모든 파일에 SHA256과 바이트 크기를 기록한다.

프로필·ICC 해시·렌더링 의도·검정 정책·도련·장면·자산 바이트 해시·원본 해상도 계보를 작업에 동결한다. 장면 변경, 다른 tenant/작업공간 자산, 권한·승인 철회, ICC/이미지 바이트 변경은 결과를 공개하지 않는다. 이미지의 원본 계보는 현재 DB 값을 뒤늦게 신뢰하지 않는다. 입력은 이미지당 20MiB, 합계 200MiB/4천만 픽셀/20개로 제한한다. 시험 ZIP은 200MiB 상한이다.

## 브랜드 글꼴의 동일성

업로드한 정적 TTF는 팀 권한, 웹 편집·인쇄 포함 사용권 기록, 파일의 임베드 제한, 실제 두께와 문자·윤곽선 검사를 거쳐 브랜드 허용 목록에 연결한다. 업로더의 권리 확인 기록이며 플랫폼이 라이선스를 대신 발급하거나 소유권을 인증하는 절차는 아니다. 장면은 불변 `font_asset_id`를 보관하고 출력 작업은 원본 SHA·실제 두께·권리 기록을 동결한다. 원본이 없어지거나 바이트가 달라지면 검수의 해당 문구가 차단되며 기본 글꼴로 대체하지 않는다.

검토 PDF는 같은 파일을 부분 포함하고 CMYK 출력은 그 파일의 실제 윤곽선을 사용한다. 두 파일의 내부 PostScript 이름이 같아도 서로 다른 SHA면 PDF 등록 식별자까지 분리한다. 이는 ReportLab의 내부 이름 중복 재사용 때문에 다른 파일의 폭·모양이 적용되는 문제를 방지한다. 원본 파일은 변경하지 않는다. manifest에는 원본 파일 SHA, 자산 ID, 실제 두께, 패밀리와 라이선스명을 남긴다. 편집 ZIP에 글꼴 원본을 동봉하는 권한은 PDF 포함 권한과 별개로 검사한다.

## 검증 증거

- `tests/geometry_pdf/test_print_engine.py`: 실제 CMYK 픽셀 변환, ICC 무결성, 한글 400/700 윤곽선, 원문 보존, 0/2/3/3.175/10mm PDF 박스, 크롭/PPI, 최종 EAN13 판독, 전개도 등록 방향, 공유 경계·날개 절개·부분 겹침·빈 공간/사선 FOLD, 투명/미지원 프로필 차단.
- `test_print_engine_api.py`: 실제 무과금 큐→ZIP→인증 다운로드, idempotency, tenant/CAS/관리자 권한, ICC 철회·역할 변경 차단, 실패 후 동일 저장본 재시도, 공식 번들 합성 ICC 제작 차단.
- `test_print_production.py`: **격리 DB의 자체 승인 fixture**로 동일 어댑터의 실제 제작 작업→8파일 ZIP→원자적 40크레딧 차감을 검증. 저장 이후 ICC 철회 시 결과 미공개·예약 복원. 실제 제조사 승인이나 운영 차감이 아니다.
- `test_production_worker.py`: 기존 RGB 8개 회귀 통과. `apps/web/tests/editor-lease-headers.test.mjs`: 여러 열린 편집기에서도 현재 project lease만 전달, 읽기 전용 차단.
- `test_custom_font_rendering.py` 15개와 `test_custom_font_exports.py` 5개: 실제 폭이 다른 동명 TTF의 분리, 파일 해시·두께·미지원 문자 차단, 업로드→브랜드→저장/재열기→검토 PDF 포함 및 CMYK 윤곽선 작업·인증 다운로드, 원본 손상 시 객체 검수 차단과 결과 미공개, 격리 승인 fixture 제작 작업의 동일 글꼴 동결. PDFium으로 포함/윤곽선 출력의 문자 잉크 위치도 비교한다.
- 브랜드 글꼴 최종 시각 증거는 이 PC의 `.local/font-render-qa/verified/`에 있다. 내부 이름이 같은 두 시험 글꼴에서 ‘가’의 실제 진행 폭을 다르게 만든 PDF를 Poppler로 렌더했다. 검토 2페이지와 윤곽선 2페이지 전체를 확인했고 한글·서로 다른 진행 폭·시험 표기가 잘림 없이 일치한다. 두 번째 면은 의도적으로 빈 면이다. 이 시험용 글꼴은 실제 고객 글꼴이나 제조사 인쇄 승인 자료가 아니다.
- CMYK·브랜드 글꼴 통합을 포함한 최종 `tests/geometry_pdf` 전체 216개가 통과했다(213.90초). 인쇄 엔진의 29개 집중 시험, 기존 RGB worker 8개, 웹 lease 2개도 앞선 단계에서 별도 통과했다. 앱 전체 및 운영 배포 검증 결과는 릴리스 로그에서 구분한다.
- 이 작업 PC의 `.local/print-engine-qa/verified/`에 삼방 2면과 6면 박스 전개도 실제 PDF/manifest/미리보기가 있다. Poppler로 한글·바코드·크롭·6면 배치·분리 CUT/FOLD를 직접 확인했다. 잘림 없이 표시되고, 박스 날개 사이 절개가 최종 CUT에 남는다. Git에 대형 임시 증거 파일을 포함하지 않는다.

CI에는 직접 생성한 CC0 합성 ICC만 포함한다(`fixtures/icc/README.md`). 이는 실제 인쇄 조건이 아니며 기본 시험 ICC의 제작 사용은 서버에서 차단한다. 양성 제작 파이프라인 테스트의 별도 이름 ICC와 승인도 격리된 자체 fixture다. 실제 인쇄 색감이나 제조사 승인을 재현한 시험으로 해석하지 않는다.

## 격리 로컬 UI의 실제 출력 확인

2026-09-18 로컬 UI에서 정적 Noto Sans KR Bold 파일을 등록하고 브랜드에 연결한 뒤, 프로젝트 `c19bb51b-e06e-47cc-9623-2f8ba32c1428`의 앞면에 **오리고기 100% / Duck 37.5g × 4개입**을 27pt·두께 700으로 저장했다. 아래 세 작업은 같은 저장본 2번(`4d921358-ea97-4b91-9672-638cdb37e97d`)으로 UI가 요청하고 로컬 worker가 완료했다. 검사자는 `.local/ops-qa/phoenix.db`를 읽기 전용으로 열고 해당 작업의 저장 파일을 그대로 복사했다. 검사 중 새 작업 생성·DB 쓰기·파일 재작성은 하지 않았다.

| UI 작업 | 완료 작업 ID | 원본 파일·크기 | 확인 결과 |
|---|---|---|---|
| 무료 검토 PDF | `20414a8b-1cf5-4c79-a6ec-4d62146d01b1` | `uploaded-bold-review-20414a8b.pdf` · 30,846B | 2페이지, 실제 한글·영문·중량 추출, 등록 글꼴 부분 포함 및 SHA 기반 식별자 확인 |
| 편집 ZIP | `c2c93400-0aa1-4d62-8bb1-41f56c2f7907` | `uploaded-bold-editable-c2c93400.zip` · 6,130,948B | 11개 파일, 저장 장면과 일치, 재배포 허용 TTF 원본·라이선스 및 전체 manifest 해시 확인 |
| 무료 CMYK 출력 시험 | `e2f549f8-315f-4de7-854b-4eda3775ecec` | `uploaded-bold-cmyk-test-e2f549f8.zip` · 292,652B | 6개 파일, 같은 글꼴 원본으로 윤곽선 출력, 실제 CMYK ICC 포함, 아트·CUT·FOLD 분리 및 전체 manifest 해시 확인 |

등록 글꼴 원본 SHA256은 `f83cb7d28cc6c5ab36629da7bbed2d925f4c740665d0ae0de7455dadd9630efc`로 세 작업의 동결 기록·최종 manifest·편집 ZIP의 TTF 바이트가 일치했다. 모든 면의 TrimBox는 230×310mm다. 검토 PDF의 MediaBox는 236×316mm이고, CMYK 시험 PDF는 같은 3mm 도련에 시험 표기용 별도 하단 12mm를 더해 236×328mm다. 후자는 TrimBox 원점이 (3,15)mm이며 크기는 동일하다. CMYK 아트에서 활성 글꼴과 추출 텍스트가 없고, 원문은 manifest에 보존됐다.

Poppler로 검토 2페이지와 CMYK 아트·CUT·FOLD 각 2페이지, 총 8페이지를 렌더하여 모두 직접 확인했다. 한글과 굵은 글꼴의 배치·윤곽선이 유지되며 잘림이 없다. 삼방 면별 시험의 CUT는 면 외곽 사각형이고, 접지선이 없는 FOLD는 시험 표기만 남는 것이 의도된 결과다. 세 작업은 모두 0크레딧이며 `review_only=true`다. CMYK 시험은 합성 ICC·데모 구조이고 제조사 입고본이나 PDF/X 인증본이 아니다. 상품 뒷면의 초기 안내 문구도 그대로 둔 기능 검증용 프로젝트다.

원본 파일, 8개 미리보기 PNG, 파일별 전체 SHA와 실제 PDF 박스·글꼴을 기록한 `local-ui-export-verification.json`은 이 PC의 `output/ops-ui/`에 보관한다. 해당 대형 산출물은 Git에 포함하지 않으며 이 검증은 운영 배포 QA와 구분한다.

## 공식 자료·다음 조건

2026-09-18 확인한 [Pillow ImageCms](https://pillow.readthedocs.io/en/stable/reference/ImageCms.html), [fontTools ReportLabPen](https://fonttools.readthedocs.io/en/latest/pens/reportLabPen.html), [ReportLab 그래픽](https://docs.reportlab.com/reportlab/userguide/ch2_graphics/) API 범위로 구현했다. [ECI 공식 배포](https://eci.org/doku.php_id=en_downloads.html)의 PSO Coated v3는 내장 저작권의 재배포 제한 때문에 프로젝트에 번들하지 않았다. 사용·PDF 임베드 권한과 원본 파일 재배포 권한은 별개다.

[Ghostscript 문서](https://ghostscript.readthedocs.io/en/latest/VectorDevices.html)만으로 특정 PDF/X 버전의 적합성을 선언하지 않는다. 서버에 검증된 버전·ICC 정책·독립 PDF/X 검사기를 설치하고 통과 증거를 확보해야 지원을 열 수 있다. [veraPDF](https://verapdf.org/)의 주 검증 대상은 PDF/A·PDF/UA이며 PDF/X 검사기를 대신하지 않는다. 실제 공급자 조건은 [Adobe Preflight 설명](https://helpx.adobe.com/acrobat/using/pdf-x-pdf-a-pdf.html) 등 해당 검사도구와 제조사 기준으로 검증해야 한다.

[ePac 공개 안내](https://epacflexibles.com/en-gb/packaging-design-how-to-submit-artwork-for-epac/)의 3.175mm 도련·CMYK·윤곽선·화이트판/레이어 요건 중 일부 수치·기능을 구현한 것이며, 이 어댑터가 ePac 전체 입고 조건을 만족한다는 주장은 하지 않는다.
