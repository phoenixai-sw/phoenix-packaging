# 진행 현황

2026-09-16 기준. 빈 작업 폴더에서 P0–P1의 핵심 편집·영구 저장·검토 출력 흐름을 구현하고 실제 클라우드에서 검증했다. 현재 결과물은 **P0–P1 개발 시험판**이며, 전체 작업지시서 완료나 유료 시험 서비스 개시를 의미하지 않는다.

| 영역 | 현재 상태 | 남은 범위 |
|---|---|---|
| P0 환경·계정·영구 저장 | 서버 세션·CSRF·조직별 접근 제한·DB 마이그레이션·비공개 파일 저장 구현 | 클라우드 SMTP 연결, Docker Compose 실기동 검증 |
| P1 한글 편집·저장·검토 PDF | 앞·뒷면 편집, 한글·이미지 레이어, 서버 저장·재열기, 버전 충돌, 불변 리비전, 검토 PDF 구현·핵심 흐름 검증 | 실제 Windows IME 조합 입력, 네트워크 단절 복구 UI, 편집 조합별 브라우저/PDF 비교 |
| GitHub 저장소 | `phoenixai-sw/phoenix-packaging` 비공개 저장소·main 배포 연결 구성 | 후속 변경의 CI·배포 이력 관리 |
| Vercel 배포 | 웹과 API 실제 배포·클라우드 동작 확인 | 후속 변경의 자동 검증 |
| Supabase Pro 조직 | `phoenix-ai-studio` Pro 조직에 전용 프로젝트 생성·연결 | 실제 백업 별도 환경 복원 검증 |
| P2 실제 AI·정산·결제 | 후속 작업용 DB 큐·중복 요청 방지·실패 복구 기반만 구현 | 실제 이미지 API, 크레딧 원장, PG·구독·충전 구현 및 검증 |
| P3 제작·팀·관리자 | 미승인 제작 출력 차단과 기본 서버 역할 검사 구현 | 스탠드형·바코드·구멍·3D·승인·팀/관리자 기능 |
| P4 유료 시험 | 배포 환경 준비 일부 진행 | 제조사 자료·실물 입고·운영 결제·정책·백업 검증 |
| P5 박스·확장 | 미착수 | 승인 박스 1종·상품 복제·변형 |

## 연결된 서비스

- 웹: [Phoenix Packaging](https://phoenix-packaging.vercel.app)
- API: [Phoenix Packaging API](https://phoenix-packaging-api.vercel.app/v1/health)
- 저장소: [phoenixai-sw/phoenix-packaging](https://github.com/phoenixai-sw/phoenix-packaging) — 비공개
- Supabase 프로젝트: `phoenix-packaging` / 참조 `jflpitqwnhznxmvwecnm` / 서울 `ap-northeast-2` / MICRO

Supabase는 사용자 승인 범위인 월 약 US$10 MICRO 기본 컴퓨트로 구성했다. Pro 조직 비용과 추가 사용량을 포함한 무제한 예산을 승인받은 것으로 해석하지 않는다. 애플리케이션 환경은 `staging`이며 실제 DB와 비공개 Storage를 사용한다.

## 실제 검증 결과

- Python 51개, 편집 장면 테스트 4개 통과. Next.js 타입 검사와 배포 빌드 통과.
- 클라우드 가입·로그인, 한글과 줄바꿈 수정, Supabase 저장, 재열기, 구버전 저장 HTTP409 차단 통과.
- 영구 큐의 검토 PDF 생성과 비공개 다운로드 통과. PDF 2페이지 각각 230×310mm, 한글 텍스트 및 0.01mm 이내 치수 확인.
- 클라우드 PNG 업로드, 비공개 Storage 저장, HTTP307 서명 URL 연결, 원본 바이트·24×32 픽셀 일치 확인. 로그인 없는 자산 접근은 HTTP401로 차단했다.
- 업로드 추가 검수는 기존 검수 계정으로 PNG 1개(109바이트)만 생성했다. 결과는 Git 제외 `.local/cloud-upload-smoke-result.json`에 저장했으며 계정 비밀번호·세션·서명 URL은 출력하지 않았다.

전체 작업지시서의 32개 시나리오별 상태는 [검수 결과](acceptance-results.md)에 기록한다. 실제 Windows IME, 클라우드 SMTP 발송, Docker Compose 실기동, 별도 환경 백업 복원, 제조사 실물 검수는 아직 통과로 집계하지 않는다.

## 이미지·영상 작업과 서비스 AI의 구분

사용자가 제공한 API 연결로 GPT Image 2.5 Sunburst 고품질 이미지 3종, Seedance 2.5 영상 1종을 생성하고 메인·콘셉트·가입 화면에 적용했다. 영상은 1280×720, 8.04초, 무음이며 시작·중간·끝 프레임과 브라우저 재생·일시정지를 검증했다. 데스크톱 1440px 및 모바일 390px에서 이미지 로딩과 가로 넘침 없는 배치를 확인했다. 이 자산 제작은 플랫폼 고객이 직접 사용하는 AI 생성·편집, 사용량 정산 또는 과금 연결의 완료를 의미하지 않는다. 앱의 실시간 AI와 실제 결제는 계속 비활성이다. 앱에서 제공하는 예시 배경은 데모로 표시한다.
