POLICY_VERSION='service-inquiry-2026-09-18-v1'
CATALOG={
    'file_review':{'code':'file_review','name':'사람의 파일 검토','suggested_amount_krw':55000,
        'price_note':'VAT 포함 55,000원 제안. 담당자가 범위와 최종 견적을 제시합니다.',
        'scope':'상품당 파일 검토 1회와 보완 확인 1회. 제조 승인·실물 인쇄 비용은 포함하지 않습니다.'},
    'onboarding':{'code':'onboarding','name':'일회 도입 지원','suggested_amount_krw':100000,
        'price_note':'100,000원 제안. 세금과 작업 범위는 VAT 포함 최종 견적에서 확인합니다.',
        'scope':'플랫폼 도입·사용 안내 일회 지원. 월 자동 재결제나 크레딧 지급이 없습니다.'},
    'pilot_pro_first_month':{'code':'pilot_pro_first_month','name':'첫 달 Pro 모집 문의','suggested_amount_krw':99000,
        'price_note':'첫 달 99,000원 모집 제안. 세금·다음 달 요금·구독 조건은 별도 확정합니다.',
        'scope':'첫 달 Pro 모집 조건 확인을 위한 별도 신청입니다. 신청/견적 수락만으로 구독이나 크레딧을 활성화하지 않습니다.'},
}
for item in CATALOG.values():item.update(automatic_renewal=False,credit_grant=0,checkout_enabled=False)
NOTICE='현재 신청·견적·진행 기록을 제공합니다. 견적 수락은 결제·구독 등록이 아니며, 자동 과금·크레딧 지급·전문가 자동 배정을 하지 않습니다.'
