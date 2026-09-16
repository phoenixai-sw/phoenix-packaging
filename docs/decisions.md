# 구현 결정 기록

## 2026-09-16 최초 구현

- 작업 경로: `C:\codex\agent\package design`. 사용자 원본 작업지시서는 보존한다.
- 우선 P0–P1을 검증한다. P2–P5는 명세의 후속 단계이며 완료로 표시하지 않는다.
- 사용자 지정 배포: GitHub 비공개 저장소, Vercel 웹·API, Supabase Pro 조직의 전용 프로젝트에 PostgreSQL과 비공개 파일 저장소를 연결한다.
- Next.js와 FastAPI 업무 계층을 유지한다. Vercel Services 비공개 베타에 의존하지 않도록 웹/API를 개별 Vercel 프로젝트로 구성하며 웹의 동일 출처 API 프록시로 세션 쿠키를 전달한다.
- 로컬 Docker Compose는 PostgreSQL·Redis·worker·메일을 제공한다. Docker가 없는 개발 환경에서는 명시적 SQLite 개발 모드로 기능을 검증할 수 있다. 클라우드에서는 SQLite나 로컬 파일을 영구 저장소로 사용하지 않는다.
- 초기 공개 배포는 검토용 개발 시험판이다. 결제와 제작용 출력은 비활성화한다. 모의 AI 이미지는 데모로 명확히 표시한다.
- 참조: https://vercel.com/docs/frameworks/backend/fastapi 및 https://supabase.com/docs/guides/troubleshooting/using-sqlalchemy-with-supabase-FUqebT

## 2026-09-16 전체 기능 확장

- P2–P5 기능을 구현했다. 실제 결제 계약·SMTP·제조사 승인·실물 검수와 운영 정책은 개발 테스트와 별도로 남긴다.
- 3D는 Three.js를 직접 연결한다. 현재 React19.3과 맞지 않는 React Three Fiber peer 의존성을 강제로 설치하지 않는다.
- Vercel Pro의800초 함수와 PostgreSQL 영구 큐를 사용한다. 응답 후 호출과 보호된 매분 Cron이 복구를 지원한다. 불확실한 AI 공급자 응답은 중복 유료 호출로 재시도하지 않는다.
- 실제 모델은 GPT Image2.5 Sunburst, 품질 high다. 표준1024와 고해상도1536×1024를 구분한다. 고해상도에는 체험 크레딧을 사용할 수 없다. 이메일 인증·허용 계정·일일30단위 호출 상한을 둔다.
- 20MiB 직접 업로드는 비공개 검역 버킷에만 허용한다. 검증 후 다른 키로 복사하여 업로드 URL 재사용이 확정 파일을 바꾸지 못하게 한다. 확정 버킷은 제작ZIP을 위해200MiB, Supabase 전역 파일 한도는256MiB로 설정했다.
- 제작 엔진은 지원하는 RGB/임베드 글꼴/면별 PDF 조건만 승인할 수 있다. 제조사 요구를 추정해 PDF/X·CMYK 등을 지원한다고 표시하지 않는다.
- 백업은 암호화된 애플리케이션 논리 스냅샷과 격리 SQLite 값·외래키·객체 해시 검증을 제공한다. PostgreSQL 물리/PITR 복구와 복원 앱 재열기는 별도 운영 검증이다.
