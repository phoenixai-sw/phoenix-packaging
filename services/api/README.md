# Phoenix API · P0/P1

Run commands from the repository root after installing `requirements-dev.txt`:

```powershell
.\.venv\Scripts\python.exe -m alembic -c services/api/alembic.ini upgrade head
.\.venv\Scripts\python.exe -m uvicorn services.api.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m pytest services/api/tests -q
```

The API uses PostgreSQL when `DATABASE_URL` is set. SQLite is explicitly limited to development/test. Alembic migrations are required before hosted startup and before upgrading an existing local database. Staging requires HTTPS origins, secure cookies and private Supabase Storage. The PostgreSQL connection uses `NullPool` and disables prepared statements for Supavisor transaction pooling.

## Request contract

- All routes start with `/v1`. JSON responses are `{data, request_id}`; errors contain `code`, `message`, `field_errors`, `retryable`, `request_id`.
- Register/login issues an HttpOnly `phoenix_session` cookie. The DB stores only its SHA-256 hash. Passwords use Argon2id. The CSRF token is returned by authentication and `/me`; send it as `X-CSRF-Token` for authenticated mutations. Browser origins are checked against `ALLOWED_ORIGINS`.
- Tenant identity comes from the authenticated user; every project, asset, export and job lookup is scoped to it. `viewer` cannot write or request exports. All Supabase application tables enable RLS and revoke browser roles; backend SQL credentials stay on the server.
- `PATCH /projects/{id}/draft` requires `base_revision` and `scene`. A successful compare-and-swap advances the version; stale saves return HTTP409 with `field_errors.base_revision.server_revision`. `/revisions` creates immutable snapshots, and exports reference one snapshot.
- `POST /assets` accepts a multipart `file` with real PNG/JPEG/WebP bytes. SVG is deliberately rejected until a sanitizer is implemented. Limits are published by `/config`; Vercel staging should use `UPLOAD_LIMIT_BYTES=4194304`, while local defaults to20MiB. Original asset data stays private.
- `POST /exports` accepts `{project_id,base_revision,kind:"review"}` and returns HTTP202 with a durable queued job. `GET /jobs/{id}` polls it. A failed job can be explicitly retried by `POST /jobs/{id}/retry`. Repeated idempotency keys with a different body return HTTP409.
- `GET /exports/{id}/download` checks ownership and completion. Hosted downloads and asset content redirect to a private Supabase URL valid for60seconds; the web proxy must preserve redirects instead of downloading the binary itself. Local downloads return bytes.

## Jobs and recovery

The worker calls `services.api.jobs.process_pending_jobs(session_factory, storage, limit=...)`. The secured GET/POST `/v1/internal/jobs/process` consumes up to2 jobs and requires `Authorization: Bearer <WORKER_SECRET>`. It is intended for a backend dispatcher or a Vercel cron with matching `CRON_SECRET`. A periodic dispatcher is required even when web requests also wake the worker.

PostgreSQL workers use `FOR UPDATE SKIP LOCKED`, a compare-and-swap claim and a unique index allowing one running export per tenant. Expired leases recover after15minutes. Every lease has its own storage key and result guard, so a late original worker cannot overwrite the recovery result. No automatic retry loop repeats permanent export validation failures.

## Email and local seed

`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS`, `MAIL_FROM`, and `APP_URL` configure email. Mailpit uses host `mailpit`, port1025 and no TLS/auth inside Docker Compose. Without SMTP, development/test writes `.data/mail/*.eml` (override `MAIL_OUTBOX_DIR`). Hosted environments without SMTP report email unavailable and never pretend delivery succeeded.

Verification and reset tokens are hashed, expire, and are consumed once atomically. A newer link invalidates older links of the same purpose. Password reset revokes every existing session. Login and email request limits persist in the DB across restarts.

`python -m services.api.seed` creates one local example account and editable project idempotently, and refuses staging/production. It prints its local-only account/password. Do not deploy seeded credentials.

## Deliberate P1 boundaries

The API exposes only the unapproved three-side-seal demo template and review PDF. Production output is rejected. Fixture backgrounds are explicitly labeled as original demo artwork, and neither real AI generation nor live billing is claimed. Credits and payments are disabled until the ledger/provider phase is implemented. User email verification is recorded but does not block this staging editor workflow.
