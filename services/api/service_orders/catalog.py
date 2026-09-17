POLICY_VERSION='service-checkout-2026-09-18-v1'
CATALOG={
    'file_review':{'code':'file_review','name':'사람의 파일 검토','suggested_amount_krw':55000,
        'price_note':'VAT 포함 55,000원 제안. 담당자가 범위와 최종 견적을 제시합니다.',
        'scope':'상품당 파일 검토 1회와 보완 확인 1회. 제조 승인·실물 인쇄 비용은 포함하지 않습니다.'},
    'onboarding':{'code':'onboarding','name':'일회 도입 지원','suggested_amount_krw':100000,
        'price_note':'VAT 포함 100,000원 제안. 범위와 최종 견적을 확인합니다.',
        'scope':'플랫폼 도입·사용 안내 일회 지원. 월 자동 재결제나 크레딧 지급이 없습니다.'},
    'pilot_pro_first_month':{'code':'pilot_pro_first_month','name':'첫 달 Pro 모집','suggested_amount_krw':99000,
        'price_note':'첫 달 VAT 포함 99,000원 제안. 다음 달은 견적에 고정된 정상 Pro 월 요금이며 별도 자동 갱신 동의가 필요합니다.',
        'scope':'신규 구독 조직에 첫 달 Pro 1,500크레딧·3좌석을 제안합니다. 수락만으로 활성화되지 않으며 검증된 수납 후 지급합니다.'},
}
for item in CATALOG.values():item.update(automatic_renewal=False,credit_grant=0,checkout_enabled=False)
CATALOG['pilot_pro_first_month'].update(automatic_renewal=True,credit_grant=1500)
NOTICE='신청·견적 수락은 결제가 아닙니다. 별도 결제 단계에서 수락 금액을 확인하며 첫 달 Pro는 이후 월 요금·자동 갱신에 추가 동의합니다. 제안 요금의 실운영 활성화는 기존 결제·정책 검증 조건을 따릅니다. 전문가 자동 배정은 제공하지 않습니다.'
