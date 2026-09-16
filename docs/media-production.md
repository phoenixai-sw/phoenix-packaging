# 디자인 이미지·영상 제작

2026-09-16 사용자 요청에 따라 Phoenix Packaging 메인과 디자인 콘셉트에 사용하는 소재를 실제 생성했다. 기존 고객 사례나 실제 제조 완료 제품을 촬영한 사진이 아니라 AI로 만든 디자인 콘셉트다.

## 이미지

- 모델: `gpt-image-2.5-sunburst` (제공된 계정의 접근 가능 여부 확인 후 사용)
- 실행: `imagegen` 스킬의 공식 `image_gen.py generate-batch` CLI
- 품질: `high`, WebP 품질 92, 각 프롬프트 1장씩
- 원본 프롬프트와 설정: [media-image-prompts.jsonl](media-image-prompts.jsonl)
- 결과: `apps/web/public/media/packaging-hero.webp` (1536×1024), `matcha-design.webp` (1024×1024), `granola-design.webp` (1024×1024)
- 이미지 3종을 직접 열어 패키지 형태·인쇄 문구·재질·구도·색감을 확인했다.

메인 화보는 포레스트 그린 말차, 아이보리 그래놀라, 테라코타 과일 패키지를 밝은 석재 위에 배치했다. 말차는 짙은 녹색의 차분한 자연광, 그래놀라는 오렌지 태양 그래픽과 따뜻한 테라코타를 사용했다.

## 보관 및 서비스 범위

사용자가 지정한 로컬 API 파일은 실행 시에만 읽으며 Git·Vercel·브라우저 번들에 포함하지 않는다. 사이트는 생성된 정적 미디어만 제공한다. API 키나 서명 URL을 이 문서에 기록하지 않는다.

이번 제작은 홍보·디자인 예시용 일회성 생성이다. 플랫폼 사용자가 실시간으로 요청하는 유료 AI 생성, 크레딧 정산, 이미지 편집 기능의 완료를 뜻하지 않는다. 각 콘셉트에서 새 프로젝트로 이동할 수 있으며, 화보 자체가 편집 가능한 레이어 템플릿으로 변환됐다고 표시하지 않는다.

## 공식 참고

- [GPT Image 2.5 Sunburst](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst)
- [OpenAI 이미지 생성](https://developers.openai.com/api/docs/guides/image-generation)
- 영상 제작의 모델·프롬프트·실행 결과는 `media-video.json`에 별도 기록한다.

## 영상 및 화면 검증 결과

`dreamina-seedance-2-5-260628`로 참조 이미지 기반 영상 1회를 생성했다. 1280×720, 24fps, 8.04초, H.264, 무음이며 웹 재생을 위해 무손실 fast-start 처리했다. 실제 영상에서 포스터를 추출했다. 세부 기록은 [media-video.json](media-video.json)에 있다.

메인·콘셉트 갤러리·가입·대시보드에 소재를 적용했다. 데스크톱 1440px 및 모바일 390px에서 이미지 로딩과 가로 넘침 없는 배치를 확인했다. 브라우저의 실제 영상은 무음·반복 재생됐고 재생/일시정지 버튼 모두 작동했다. 동작 줄이기 설정 대응은 구현했으며 OS 설정 변경을 통한 별도 시험은 하지 않았다.
