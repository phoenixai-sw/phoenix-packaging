# Phoenix Packaging

국내 식품 브랜드를 위한 패키지 디자인 작업 공간입니다. 한글·이미지 편집, 서버 저장, 세 가지 포장 구조, 3D 미리보기, AI 이미지 생성·편집, 검토 PDF, 브랜드·상품·팀 관리와 크레딧·결제 업무를 구현했습니다. 현재 배포 환경은 **staging**입니다. 실제 결제 및 제조사 승인 제작 출력은 외부 검증과 운영 정책 확정 전까지 잠겨 있습니다.

## 연결된 환경

- 작업 폴더: `C:\codex\agent\package design`
- [GitHub 비공개 저장소](https://github.com/phoenixai-sw/phoenix-packaging)
- [웹](https://phoenix-packaging.vercel.app) · [API 상태](https://phoenix-packaging-api.vercel.app/v1/health)
- Supabase: `phoenix-ai-studio` Pro 조직 / `phoenix-packaging` / 서울 / MICRO

실제 PostgreSQL과 비공개 Storage를 사용합니다. Supabase MICRO 기본 비용은 사용자 승인 범위인 월 약 US$10이며 추가 사용량의 무제한 예산을 뜻하지 않습니다.

## 구현 범위

- 3면 실링, 스탠드 파우치, 접이식 박스의 면별 편집과 Three.js 3D 미리보기
- 한글·이미지·EAN-13 바코드, mm 단위 구조·구멍 검증, 저장 충돌 보호와 불변 리비전
- 브랜드·상품·변형의 자동 연결, 수동 수정 문구 보존, 프로젝트 복제
- GPT Image 2.5 고품질 생성·편집, 결과별 적용, 크레딧 예약·확정·반환과 사용 이력
- Sunburst·Flare 모델과 low/medium/high/xhigh/max/auto 품질 선택, 견적에 고정된 설정 및 결과 이력. [이미지 생성 업그레이드 안내](docs/image-generation-upgrade.ko.md)
- 실제 크기 검토 PDF, 제작 사전 검사와 승인 증빙 관리, 제작 묶음 생성 엔진
- 팀·좌석·작업 공간 권한, 초대, 플랫폼 관리자, 제조사 접수 기록
- Toss 결제·정기결제·환불 어댑터와 중복 방지 원장. 클라우드 실제 결제는 비활성
- 비공개 이미지 직접 업로드(20MiB), 격리 검사, 파일 해시 및 권한 확인

메인에는 실제 GPT Image 2.5 Sunburst 이미지와 Seedance 2.5 영상을 적용했습니다. [미디어 제작 기록](docs/media-production.md)을 참고하세요. 고객용 AI 접근은 서버의 인증 이메일 및 허용 계정 정책을 따릅니다.

## 로컬 실행

Node.js22 또는24, Python3.12 이상이 필요합니다. 현재 호스트는 Node24·Python3.14, Vercel API는 Python3.12입니다.

```powershell
cd 'C:\codex\agent\package design'
npm ci
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r services/api/requirements-dev.txt
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
```

`.env.example`을 `.env`로 복사하고 `GOOGLE_CLIENT_ID`를 설정한 뒤 http://localhost:3000 에서 Google로 로그인합니다. 운영 계정은 `ADMIN_EMAILS`로 지정합니다. Google 웹 클라이언트에 접속 원본도 등록해야 합니다. 비밀번호·인증메일·재설정은 없습니다. 로컬은 `.data/phoenix.db`와 `.data/storage`를 사용하며 종료 시 데이터를 지우지 않습니다. 로컬 AI 기본값은 명시적인 fixture이고 결제 기본값은 disabled입니다. 중단은 `scripts/dev.ps1 -Stop`을 사용합니다.

Docker Compose는 `make dev`, `make down`을 제공합니다. PostgreSQL·Redis·Celery를 포함하지만 이 호스트에는 Docker가 없어 Compose 실기동은 검증하지 않았습니다. 메일 서비스는 제거했습니다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m pytest services/api/tests services/api/billing/tests tests/geometry_pdf -q
npm test
npm run typecheck
npm run build
```

[검수 결과](docs/acceptance-results.md)와 [P2–P5 검증 근거](docs/p2-p5-validation.md)에 실제 실행과 미실시 항목을 구분합니다. 자동화된 제조사 승인 fixture는 실제 제조사 승인으로 집계하지 않습니다.

## 배포와 운영

Next.js 웹과 FastAPI API는 별도 Vercel 프로젝트이며 같은 저장소의 `main`을 배포합니다. 웹의 동일 출처 API 프록시가 세션을 전달하고 비밀키는 서버에만 둡니다. DB 작업 큐, 응답 후 worker 호출, 매분 보호된 Cron이 AI·출력·결제 복구를 처리합니다. 함수 제한은 Pro의800초이며 작업 임대와 공급자 중복 호출 방지를 별도로 적용합니다.

마이그레이션은 `0007_google_auth`까지 있습니다. API의 루트 `requirements.txt`와 `services/api/requirements.txt`를 동일하게 유지합니다. Google이 검증한 계정 중 서버 `ADMIN_EMAILS`에 등록된 계정만 운영 관리자가 됩니다. 클라이언트나 기존 DB의 is_admin 값으로 권한을 부여하지 않습니다.

Google·Toss 설정은 [연결 안내](docs/owner-setup.ko.md), 남은 운영 조건은 [유료 운영 준비 상태](docs/production-readiness.md)에 있습니다. 제조사 자료 요청 절차는 공개 기본 규격 조사와 엔진 검증으로 대체했습니다. 키·백업·계정 정보는 Git 제외 경로에 보관합니다.
