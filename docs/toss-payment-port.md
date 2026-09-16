# Toss 승인·취소·웹훅 이식 기록

2026-09-17 기준. `C:\codex\agent\aibridge-site`의 결제 코드를 현재 FastAPI·SQLAlchemy 서버에 연결해 이식했다. 서버에서 실행하지 않는 JavaScript 사본을 추가하지 않았다. 실제 PG 승인·취소 요청은 수행하지 않았으며, 아래 자동 검증은 주입형 MockProvider와 HTTPX MockTransport를 사용한다.

## 참조 파일과 실행 코드

| 사용자 지정 참조 | 현재 연결 위치 | 반영한 동작 |
| --- | --- | --- |
| `lib/toss.js` | `services/api/billing/payments.py:TossProvider` | 서버 Basic 인증, orderId·paymentKey 조회, 고정 멱등키, 승인·취소, 잔액 조건부 취소 |
| `lib/payment-sync.js` | `services/api/billing/sync.py:sync_verified_payment` | 검증된 승인·취소 상태를 주문·청구서·불변 결제/환불 전표·크레딧·감사 기록과 원자적으로 동기화 |
| `api/payments/confirm.js` | `POST /v1/billing/confirm`, `/billing/billing-key/confirm` | 로그인한 소유자·CSRF 검증, 서버 가격 비교, 기존 결제 조회, 응답 유실과 이미 처리된 요청의 재조회 |
| `api/payments/webhook.js` | `POST /v1/billing/webhooks/toss` | 지원 이벤트 필터, 기존 주문만 조회, PG 서버 재검증, 성공한 전송 ID와 상태의 중복 방지 |

모든 승인 경로, 빌링 승인, 확인 지연 주문 worker, 취소 응답, 웹훅, 소유자 수동 재조회가 `sync_verified_payment`를 사용한다. 함수는 자체 커밋하지 않는다. 호출자가 승인·원장·이벤트를 함께 커밋해야 한다. 기존 테이블을 사용하며 이번 이식을 위한 스키마 마이그레이션은 없다.

## 유지한 검증

- 가격·통화·크레딧 지급량은 서버 주문에서 고정한다. PG 응답의 orderId, 정수 totalAmount, KRW, MID, paymentKey와 저장된 결제 환경이 모두 맞아야 적용한다. 브라우저 콜백과 웹훅의 성공 표시는 증빙이 아니다.
- 모르는 주문을 PG 알림에서 자동 생성하거나 상품을 추측하지 않는다. 상점이 다른 제품에도 쓰일 수 있으므로 알 수 없는 주문과 미지원 이벤트는 지급 없이 `ignored: true`로 응답한다.
- `ALREADY_PROCESSED_PAYMENT` 같은 응답만 보고 성공 처리하지 않는다. 같은 주문을 다시 조회해 실제 승인·취소를 확인한다. 재조회도 실패하면 `reconciliation_required`로 보존한다.
- 결제 내역의 카드 번호, 계좌, secret, billingKey를 감사 JSON이나 브라우저 요약에 저장하지 않는다. 원본 Payment와 PaymentEvent는 불변이며 취소도 별도 전표로 남긴다. 영수증은 HTTPS Toss 도메인 URL만 전달한다.
- PG 취소는 transactionKey별로 한 번 기록한다. 남은 결제액·누적 취소액이 원금과 맞아야 한다. 과거 상태로 취소를 되돌리거나 취소 후 다시 크레딧을 지급하지 않는다.
- 자동 환불은 완전히 미사용인 전액 취소만 허용한다. 부분 취소와 사용·예약·만료 내역이 있는 취소는 실제 환불 전표·청구서 상태를 기록한 뒤 운영 검토로 남긴다. 전액 취소한 현재 구독은 검토 중에도 다음 자동 갱신을 하지 않는다. 가상계좌 환불 계좌 수집과 임의 부분 취소 요청은 미지원이다.
- 상향 주문의 환불은 현재 주기의 최초 구독/갱신과 유효한 상향 이력을 `source_plan_id`로 연결하여 마지막 상향임을 확인한다. 안전한 전액 취소는 이전 요금제·좌석으로 복구하고 원래 유료 기간을 유지한다. 이후 상향, 주기 불일치, 손상된 원 요금제 이력은 PG 취소 요청 전에 자동 환불을 차단한다. 외부에서 이미 발생한 모호한 취소는 환불 완료로 표시하지 않고 운영 검토로 남긴다. 이전 환불 웹훅 재전송은 새 상향을 되돌리지 않는다.
- 웹훅 재전송 ID는 처리 성공과 같은 SQL 트랜잭션으로 확정한다. 조회 실패나 DB 롤백 시 같은 ID를 다시 처리할 수 있다. 외부 이메일·메시지 발송 기능은 복사하지 않았다.

## API 추가 계약

`POST /v1/billing/orders/{order_id}/sync`는 본문 없이 로그인·소유자·CSRF 검사를 거쳐 PG를 조회한다. 승인·청구·취소를 새로 실행하지 않는다. 다른 조직의 주문은 404다.

`GET /v1/billing`의 각 주문과 승인·환불·재조회 응답에는 다음 `payment` 요약이 추가된다. 확인된 PG 상태가 없으면 null이다.

```json
{
  "provider_status": "PARTIAL_CANCELED",
  "method": "카드",
  "total_amount": 31900,
  "balance_amount": 30900,
  "cancelled_amount": 1000,
  "approved_at": "2026-09-17T12:00:00+09:00",
  "receipt_url": null,
  "cancels": [{"amount": 1000, "reason": "고객 요청", "at": "2026-09-17T12:10:00+09:00", "status": "DONE"}],
  "synced_at": "2026-09-17T03:10:01+00:00"
}
```

승인 `amount`는 JSON 정수만 허용하고 문자열·실수·불리언을 거부한다. 환불 `reason`은 3~200자다. 일반 Toss 결제 웹훅에는 보편적인 HMAC 서명이 있다고 가정하지 않는다. `PAYMENT_STATUS_CHANGED`와 `CANCEL_STATUS_CHANGED`만 처리한다. payout/seller 서명을 일반 결제에 적용하지 않으며 `DEPOSIT_CALLBACK`은 현재 미지원이다.

## 지정 MCP에서 확인한 공식 근거

설치·실행한 `@tosspayments/integration-guide-mcp`의 `get-documents` 도구를 사용했다. v2, balanced/precise 검색으로 결제 취소, paymentKey 조회, cancelStatus, 멱등키와 오류 처리를 확인했다. 조회 원문과 실행 결과는 git에서 제외된 `.local/toss-*-result.json`에만 보관한다.

- [코어 API, 문서 ID 127](https://docs.tosspayments.com/reference): 승인, orderId/paymentKey 조회, 취소, cancelReason 200자 제한, refundableAmount와 취소 내역 구조.
- [결제 취소하기, 문서 ID 8](https://docs.tosspayments.com/guides/v2/cancel-payment): 전액·부분 취소와 각 취소 transactionKey의 의미.
- [인증 및 기타 헤더 설정, 문서 ID 122](https://docs.tosspayments.com/reference/using-api/authorization): 서버 시크릿 키 Basic 인증, Idempotency-Key와 같은 요청의 안전한 재시도. 멱등키 만료를 이유로 불확실 주문의 키를 임의 변경하지 않는다.
- [웹훅 이벤트, 문서 ID 125](https://docs.tosspayments.com/reference/using-api/webhook-events): 전송 ID, 이벤트 종류, 일반 결제와 payout/seller 서명의 차이. 빌링 DONE 웹훅을 기다리지 않고 승인 응답과 재조회로 처리한다.

## 검증과 남은 외부 확인

`services/api/billing/tests/test_toss_sync.py`는 승인·취소 응답 유실, 취소 후 프로세스 종료, 실패한 전송 ID 재시도, 동시 웹훅·수동 동기화, PG 키/주문/상점/통화/금액 불일치, 부분→전액 취소, 실제 사용 후 취소, 늦게 도착한 이전 구독 승인, 안전한 요약, 청구 환경 혼용을 검사한다. `test_toss_routes.py`는 실제 Google 세션/CSRF 경로(서명 verifier만 테스트 주입), 소유자 권한, 타 조직 차단, 승인 입력 형식과 웹훅 HTTP 처리를 검사한다. 기존 크레딧·갱신·마이그레이션·작업자 회귀 검사도 함께 실행한다.

참조 프로젝트에서 Toss 테스트 키의 존재만 확인했고 값은 출력하거나 복사하지 않았다. 현재 작업에서는 실제 상점 MID 확인, 상점의 자동결제 계약, 카드 등록·승인·취소, Toss 대시보드 웹훅 전달을 검증하지 않았다. 라이브 결제는 production, 라이브 키, 서버 활성화 플래그, 승인된 버전 정책이 모두 충족될 때만 열리며 `config/pricing.seed.json`의 `live_billing_enabled`는 false다.
