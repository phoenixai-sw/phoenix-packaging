# HTTP API 계약과 프런트 타입

실제 FastAPI 라우트의 `response_model`에서 OpenAPI를 만들고, 같은 파일에서 프런트 TypeScript 타입을 생성한다. 별도로 손으로 작성한 API 응답 명세를 유지하지 않는다. 공개 JSON 응답은 `Envelope<T>`의 `data`, `request_id`를 사용한다. 오류는 `code`, `message`, 중첩 가능한 `field_errors`, `retryable`, `request_id`를 보존한다.

## 생성·검사

저장소 루트에서 Python 개발 의존성과 `npm ci`를 설치한 뒤 실행한다.

```sh
python packages/contracts/generate.py
python packages/contracts/export_openapi.py
npm run contracts:generate

python packages/contracts/generate.py --check
python packages/contracts/export_openapi.py --check
npm run contracts:check
```

`export_openapi.py`는 환경을 임시 로컬 저장소와 메모리 SQLite로 격리한다. 앱 lifespan을 시작하지 않고, 운영 DB·스토리지·Google·결제·이미지 제공자를 호출하지 않는다. 생성 파일은 `packages/contracts/openapi.json`, `api.generated.ts`이다. CI에서 두 단계의 변경 누락을 별도로 검사한다.

## 새 API를 추가할 때

- `services/api/contracts/base.py`의 `ContractModel`, `Envelope`, `ERROR_RESPONSES`를 사용한다. 응답 DTO는 실제 공개 필드와 중첩 구조를 선언하고 `extra="forbid"`로 잘못된 필드 유출을 차단한다.
- JSON 라우트는 `response_model=Envelope[DTO]`, `response_model_exclude_unset=True`, `responses=ERROR_RESPONSES`를 지정한다. 쿠키·CSRF·편집 권한·멱등성 검증은 기존 서버 정책을 그대로 적용한다.
- 파일 응답에는 `Response`와 `binary_responses(...)`를 쓴다. PDF·ZIP·이미지 bytes와 비공개 스토리지 307 전환은 JSON envelope가 아니다.
- 저장된 디자인·이력 응답에는 `contracts.scene.StoredScene`을 사용한다. 입력용 `Scene`을 다시 통과시켜 과거 숫자를 반올림하거나 누락된 기본값을 채우지 않는다. 새 저장 요청은 기존 입력용 `Scene` 검증을 계속 사용한다.
- 작업 결과는 AI·검토·제작·편집 ZIP 종류별 DTO를 사용한다. 내부 저장소 키·공급자 비밀정보·원본 OCR 문구를 일반 작업 목록에 추가하지 않는다.
- 공급자가 선언한 미지원 인쇄 요구조건과 감사 이벤트의 확장 데이터는 의도적으로 JSON 값 사전이다. 이 예외를 전체 응답의 포괄 타입으로 확대하지 않는다. 해당 인쇄 조건의 지원 여부는 별도 검수에서 판정한다.

## 프런트 사용

`apps/web/src/lib/api-contract.ts`의 `apiRequest`는 HTTP 메서드·경로·경로 변수·질의·본문·응답을 생성 계약에서 추론한다. 기존 전송 계층이 same-origin 쿠키, CSRF, `X-Editor-Lease`를 계속 담당한다.

```ts
const project = await apiRequest("get", "/v1/projects/{project_id}", {
  path: { project_id: id },
});
const file = apiFileUrl("/v1/exports/{job_id}/download", { job_id: id });
```

로그인·팀 전환에 typed 호출을 적용했다. Session, 브랜드·상품, 서비스 주문, AI 작업·견적, 검수·이미지 보완 UI는 생성 DTO를 참조한다. 나머지 기존 `api<T>` 호출은 전송 호환 계층으로 남아 있으므로 새 호출은 `apiRequest`를 우선 사용한다. 로컬 초안에서 작업 ID만 먼저 복원하는 상태는 서버의 완전한 작업 응답과 구분한다.

`test_api_contracts.py`는 공개 작업의 성공·오류·바이너리 계약 누락과 중복 operation ID, 과거 장면의 숫자·필드 보존을 확인한다. 프런트 컴파일 검사는 존재하지 않는 경로/메서드·잘못된 본문·누락된 경로 변수·JSON으로 다운로드하려는 호출을 거부하는지 검사한다. 전송 회귀는 중첩 오류와 CSRF/편집 권한 헤더, 잘못된 성공 envelope를 확인한다. 이 검증은 실제 Google 로그인이나 유료 외부 호출을 새로 수행한 증거는 아니다.
