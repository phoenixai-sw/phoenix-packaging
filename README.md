# Phoenix Packaging

국내 식품 브랜드를 위한 포장 디자인 작업 공간입니다. 현재 P0–P1 개발 시험판이며, 제조사 미승인 3면 실링 봉투의 앞·뒷면을 편집하고 서버에 저장한 뒤 실제 크기의 한글 검토 PDF를 만듭니다.

## 연결된 환경

- 작업 폴더: `C:\codex\agent\package design`
- GitHub: [phoenixai-sw/phoenix-packaging](https://github.com/phoenixai-sw/phoenix-packaging) (비공개)
- 웹: [Phoenix Packaging](https://phoenix-packaging.vercel.app)
- API: [Phoenix Packaging API](https://phoenix-packaging-api.vercel.app/v1/health)
- Supabase: `phoenix-ai-studio` Pro 조직 / `phoenix-packaging` / 서울 `ap-northeast-2`, MICRO
- 프로젝트 참조: `jflpitqwnhznxmvwecnm`

클라우드는 실제 PostgreSQL·비공개 Storage를 사용합니다. 결제, 실제 AI, 제조사 승인 제작 출력은 켜져 있지 않습니다. Vercel의 production 주소에 배포하지만 애플리케이션 환경은 의도적으로 `staging`입니다.

Supabase 전용 프로젝트는 사용자 승인 범위인 월 약 US$10의 MICRO 기본 컴퓨트로 생성했습니다. 전체 단계의 진행 상태와 미완료 항목은 [진행 현황](docs/progress.md)을 확인하세요.

## Windows 로컬 실행

Node.js 22 또는 24, Python 3.12 이상이 필요합니다. 현재 검증 환경은 Node24·Python3.14이며 Vercel API는 Python3.12입니다.

```powershell
cd 'C:\codex\agent\package design'
npm ci
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r services/api/requirements-dev.txt
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
```

http://localhost:3000 을 엽니다. 계정을 직접 생성합니다. `.env.example`을 `.env`로 복사하면 실행 설정을 변경할 수 있습니다. 기본 로컬 DB는 `.data/phoenix.db`, 자산은 `.data/storage`입니다. 서버를 중단해도 이 파일은 보존됩니다.

```powershell
# 로컬 시험 데이터 생성 (개발 환경 전용)
.\.venv\Scripts\python.exe -m services.api.seed
# 종료 및 재시작
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 -Stop
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
```

시드 계정은 `demo@example.com`이며 비밀번호는 seed 명령에 표시됩니다. 이 계정은 개발 환경 전용으로 클라우드에 만들지 않습니다. 로그는 `.local/*.log`에서 확인합니다. 같은 포트에 이미 서비스가 있으면 기존 프로세스를 먼저 종료합니다.

## Docker Compose

```sh
make dev
make seed
make down
```

웹·API·PostgreSQL·Redis·Celery worker·scheduler·Mailpit을 시작합니다. 로컬 메일함은 http://localhost:8025 입니다. Docker가 설치되지 않은 현재 호스트에서는 Compose 실행 검증을 하지 않았습니다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m pytest services/api/tests tests/geometry_pdf -q
npm test --workspace @phoenix/web
npm run typecheck
npm run build
```

현재 Python 51개와 편집 장면 테스트 4개, 타입 검사·배포 빌드를 통과했습니다. 실제 클라우드에서 가입·로그인 → 한글 수정·서버 저장 → 재열기 → 구버전 저장 차단 → 2페이지 검토 PDF 다운로드를 확인했습니다. PNG의 실제 업로드·비공개 저장·서명 다운로드·바이트 일치·비로그인 접근 차단도 확인했습니다.

검수 범위와 미실시 항목은 [검수 결과](docs/acceptance-results.md)에 기록합니다. 실제 Windows 한글 IME 입력, 클라우드 SMTP 발송, 별도 환경 백업 복원과 제조사 실물 검수는 아직 미실시입니다. 자동 텍스트 입력이나 백업 절차 문서만으로 통과를 대신하지 않습니다.

## 배포 구조

`apps/web`는 Next.js 프로젝트, 저장소 루트의 `api/index.py`는 FastAPI 프로젝트입니다. 웹과 API는 Vercel에 배포했으며, 두 프로젝트 모두 같은 GitHub 저장소의 `main`에 연결되어 있습니다. 웹의 `/api/v1/*`가 서버에서 API를 호출하며 비밀키는 브라우저에 전달하지 않습니다.

출력 접수는 DB 작업을 만든 뒤 즉시 202를 반환합니다. 별도 API 함수가 작업을 처리하며 Next.js의 응답 후 호출과 매분 Vercel Cron이 작업을 깨웁니다. 작업 소유권·만료·조직별 동시 실행은 PostgreSQL에서 관리합니다. 로컬에서는 별도 worker가 처리합니다. 이후 장시간 AI 작업은 P2에서 별도 지속 실행 worker를 연결해야 합니다.

배포 시 API의 `requirements.txt`는 `services/api/requirements.txt`와 동일하게 유지합니다. Vercel의 포함 requirements 해석 차이 때문에 루트에 동일한 고정 의존성 목록을 둡니다.

## 외부 연결과 현재 제한

- 실제 OpenAI 이미지 API와 Toss 결제는 후속 단계입니다. 자체 생성 예시 배경에는 데모 표시를 붙입니다.
- GPT Image 2.5 Sunburst 고품질 이미지 3종과 Seedance 2.5 제품 영상 1종을 실제 생성해 메인·콘셉트·가입 화면에 적용했습니다. 제작 기록은 `docs/media-production.md`에 있습니다. 고객용 실시간 AI 생성·편집 및 사용량 과금은 별도 후속 범위입니다.
- 클라우드 업로드는 Vercel 요청 제한에 맞춰 PNG·JPEG·WebP 각 4MiB까지입니다. 20MiB 직접 업로드는 후속 단계입니다.
- 다운로드는 권한 확인 후 Supabase의 60초 서명 URL로 연결됩니다. 버킷은 비공개입니다.
- SMTP 미설정 클라우드에서는 이메일 확인·비밀번호 재설정 발송이 비활성입니다. 로컬은 `.data/mail/*.eml` 또는 Mailpit을 사용합니다.
- 스탠드형, 3D, 바코드, 실제 AI 생성, 과금 원장, 제작 출력, 팀 관리, 박스는 아직 완료되지 않았습니다.
- 검토 PDF의 구조는 제조사 미승인입니다. 실제 제조에 사용하지 않습니다.

## 데이터 보호와 운영 준비

자산·프로젝트·리비전·출력은 조직 권한을 확인합니다. DB 공개 API 역할의 테이블 접근을 차단하고 RLS를 적용합니다. 비밀번호는 Argon2id, 세션·재설정 토큰은 해시로 저장합니다. `.env`, `.local`, `.data`는 Git과 배포 소스에서 제외됩니다.

백업·복원 및 외부 운영 조건은 [유료 운영 준비 상태](docs/production-readiness.md)를 확인하세요. P2–P5는 일부 기반 작업 외에 구현·검증이 남아 있으며, 유료 시험은 아직 개시하지 않습니다.
