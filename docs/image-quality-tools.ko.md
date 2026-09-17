# 이미지 품질 보완 도구와 인쇄 출력의 경계

확인일: 2026-09-17. 무료 로컬 이미지 처리이며 외부 AI·유료 API를 호출하지 않는다.

## 실제 제공 기능

- 저장된 프로젝트의 이미지 객체를 지정해 실제 파일 픽셀, 배치 mm, 현재 ppi, 보완 이전 원본의 ppi, 재단선에서 3mm 도련까지의 부족분을 진단한다.
- Lanczos 방식으로 픽셀을 확대한다. 기본 목표는 300ppi, 입력 범위는 72~600ppi이다. 이미 목표보다 큰 이미지를 줄이지 않는다.
- 재단선에 닿는 이미지의 부족한 도련만 가장자리 픽셀 반복(`edge`) 또는 대칭 반사(`mirror`)로 확장한다. 면 내부의 작은 이미지를 임의로 전체 배경으로 채우지 않는다.
- 원본 자산과 프로젝트는 그대로 두고 파생 PNG와 적용할 객체 패치를 반환한다. 사용자가 미리보기를 확인한 후 편집기의 기존 저장 절차로 적용한다.
- 이미지마다 원본 자산·해시·픽셀, 부모 자산·해시·ppi, 원본 영역, 확장 영역, 확대 여부를 서버에 남긴다. 다시 확대하거나 도련을 추가해도 최초 원본의 픽셀 밀도 정보가 이어진다.

픽셀 확대는 사진·일러스트의 실제 디테일 복원이 아니다. 원본이 300ppi 미만이면 확대 파일이 300ppi가 되어도 `BASIC_ORIGINAL_LOW_PPI` 경고가 유지된다. 제조사 최소 ppi에 못 미치는 원본은 `ORIGINAL_LOW_PPI`로 제작 출력을 계속 차단한다. 도련 부족을 보완하면 부족 경고는 없어지지만 `BASIC_SYNTHETIC_BLEED`가 남아 경계·반복 무늬·투명도 확인을 요구한다.

## API 계약

`POST /v1/image-quality/inspect`

입력: `project_id`, `base_revision`, `face_id`, `object_id`, 선택 `target_ppi`.

반환: `base_revision`, `face_id`, `object_id`, `source:{id,sha256,width_px,height_px}`, `placed_mm`, `effective_ppi`, `original_effective_ppi`, `bleed_missing_mm:{left,right,top,bottom}`, `required_pixels:{width,height}`, `warnings`.

`POST /v1/image-quality/preview`

진단 입력에 `operation_key`, `source_sha256`, `resample`, `bleed_mode:none|edge|mirror`를 추가한다. 반환은 새 `asset`, `patch:{asset_id,x_mm,y_mm,width_mm,height_mm}`, `quality`, `provenance`, 기존 객체 식별자와 원본 `source`, `credits_charged:0`이다. 이 API는 프로젝트에 패치를 적용하지 않는다.

서버가 테넌트·작업공간 접근, 편집 권한, CSRF, 저장 버전, 실제 원본 해시를 검사한다. 같은 테넌트의 동일 요청 키는 같은 파생 자산을 반환하며 다른 요청 내용을 재사용하면 409이다. 적용은 기존 저장 CAS를 사용하므로 미리보기 뒤 다른 창에서 저장했다면 최신 상태를 다시 확인해야 한다.

## 처리 한계

- 입력·출력 파일은 각각 20MiB 이하, 디코딩된 이미지와 최종 결과는 4천만 픽셀 이하이다. 파생 이미지는 기존 200MiB 저장 한도에 포함하며 진행 중인 업로드 예약량도 계산한다. 테넌트별 1시간 20개까지 새 보완 자산을 만들 수 있다.
- 회전된 객체의 픽셀 확대는 가능하지만 도련 자동 확장은 지원하지 않는다. 진단의 `bleed_missing_mm`은 이때 `null`이다.
- RGB 계열 이미지 처리를 제공한다. CMYK 원본 변환이나 EXIF 회전 정규화는 이 도구가 임의로 수행하지 않는다.
- 원본의 투명도를 보존한다. 투명한 가장자리를 복사하면 그 투명도도 연장되므로 실제 시각 확인이 필요하다.
- 합성한 도련은 새로운 촬영 영역이나 AI 생성 영역이 아니다. 기존 데모·제조 승인·가공 칼선·바코드·출력 능력 제한을 해제하지 않는다.

## CMYK·PDF/X·별색 지원에 필요한 별도 작업

현재 제작 출력기는 기본 RGB PDF만 지원한다. 이 품질 보완 기능은 색상이나 파일 표준 지원 범위를 바꾸지 않는다.

1. **인쇄 조건과 ICC:** 제조 공정·기재·잉크에 맞는 출력 ICC 프로필과 사용권, 입력 색공간, 렌더링 의도, 총잉크량·검정 처리 조건이 필요하다. ICC는 PDF의 Output Intent가 인쇄 조건을 전달한다고 설명한다. [ICC 공식 인쇄·교정 안내](https://www.color.org/cxf_test/)
2. **실제 PDF/X 변환·검사:** 사용할 PDF/X 판본을 먼저 확정하고, 해당 판본을 생성·검사할 수 있는 출력 엔진, Output Intent 삽입, 글꼴·투명도·색공간·TrimBox/BleedBox 검증을 연결해야 한다. Ghostscript 문서는 지원 판본을 PDF/X-1·PDF/X-3으로 제한하고 다른 판본은 지원하지 않는다고 명시한다. 요구 판본을 확인하지 않은 채 단순 옵션이나 파일 이름으로 PDF/X를 표시해서는 안 된다. [Ghostscript 공식 PDF 작성 안내](https://ghostscript.readthedocs.io/en/latest/VectorDevices.html#creating-a-pdf-x-document)
3. **별색·화이트·가공 분판:** 제조사가 요구하는 판 이름·잉크 정의, Separation/DeviceN 색공간, 오버프린트·녹아웃·화이트판·칼선 구분, RIP 분판 결과 검사가 필요하다. ICC는 별색 교정에 공정색 Output Intent만으로는 충분하지 않을 수 있음을 설명한다. [ICC 공식 별색 설명](https://www.color.org/cxf_test/)

외부 표준이나 상용 엔진을 구매하지 않았고, 이번 구현에서 해당 출력 능력을 활성화하지 않았다.

## 검증

`services/api/tests/test_image_quality.py`는 원본·프로젝트 불변, 실제 RGBA 경계 픽셀, 대칭 반사, 반복 보완의 원본 밀도, 서버 검수·PDF 작업까지의 provenance 전달, 테넌트·작업공간·viewer·CSRF 차단, 원본 해시·저장 버전, 동시 요청 중복 방지, 픽셀·바이트·저장량·빈도 한도를 검증한다. 기존 구조·검토 PDF·제작 출력 회귀도 함께 실행한다.
