# 구현 결정 기록

## 2026-09-16 최초 구현

- 작업 경로: `C:\codex\agent\package design`. 사용자 원본 작업지시서는 보존한다.
- 우선 P0–P1을 검증한다. P2–P5는 명세의 후속 단계이며 완료로 표시하지 않는다.
- 사용자 지정 배포: GitHub 비공개 저장소, Vercel 웹·API, Supabase Pro 조직의 전용 프로젝트에 PostgreSQL과 비공개 파일 저장소를 연결한다.
- Next.js와 FastAPI 업무 계층을 유지한다. Vercel Services 비공개 베타에 의존하지 않도록 웹/API를 개별 Vercel 프로젝트로 구성하며 웹의 동일 출처 API 프록시로 세션 쿠키를 전달한다.
- 로컬 Docker Compose는 PostgreSQL·Redis·worker·메일을 제공한다. Docker가 없는 개발 환경에서는 명시적 SQLite 개발 모드로 기능을 검증할 수 있다. 클라우드에서는 SQLite나 로컬 파일을 영구 저장소로 사용하지 않는다.
- 초기 공개 배포는 검토용 개발 시험판이다. 결제와 제작용 출력은 비활성화한다. 모의 AI 이미지는 데모로 명확히 표시한다.
- 참조: https://vercel.com/docs/frameworks/backend/fastapi 및 https://supabase.com/docs/guides/troubleshooting/using-sqlalchemy-with-supabase-FUqebT
