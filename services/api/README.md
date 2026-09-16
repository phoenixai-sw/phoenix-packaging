# Phoenix API

Run from the repository root with the development requirements installed:

```powershell
.\.venv\Scripts\python.exe -m alembic -c services/api/alembic.ini upgrade head
.\.venv\Scripts\python.exe -m uvicorn services.api.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m pytest services/api/tests services/api/billing/tests tests/geometry_pdf -q
```

Hosted startup requires migrated PostgreSQL, private Supabase Storage, secure cookies and explicit HTTPS origins. Supavisor uses NullPool without prepared statements. SQLite is development/test only. Migration head: `0006_direct_uploads`.

## Request contract

- Routes start with `/v1`. Responses use `{data, request_id}`; errors include code, message, field_errors and retryable.
- Authentication uses an HttpOnly cookie. Mutations require `X-CSRF-Token` from auth or `/me`, plus an allowed Origin.
- Tenant, active membership, role and workspace access are verified on the server. Tenant owner is not platform administrator. Supabase public roles cannot bypass backend checks.
- Draft saves require `base_revision`; stale writes return409. Exports bind immutable revisions. Brand bindings preserve manually detached text.
- Three-side-seal, stand-up-pouch and folding-box share canonical geometry and hashes. Template version contributes to identity. EAN-13 check digits and quiet zones are validated.
- Multipart `/assets` supports the proxy limit (4MiB hosted). `/assets/uploads` signs a20MiB private quarantine upload. Completion verifies bytes, image decoding and access, then copies to a fresh final key. Reusing upload tokens cannot change finalized assets.
- `/exports` enqueues review PDF. `/quotes` and `/jobs` handle image or production operations. Canonical inputs, expiry and idempotency prevent stale or altered charges. Reservations are captured per successful unit and released for failures.
- Downloads check access then redirect to60-second signed private URLs. Production exports are ZIP bundles; review exports are PDF.

## Worker and billing

`worker_service.process_all_jobs` dispatches review, production, AI, invoice/reconciliation, outbox and upload cleanup. Protected `/v1/internal/jobs/process` is invoked after web job creation and every minute by Vercel Cron. Functions allow up to800seconds. Local and Celery workers use the same service.

PostgreSQL claims use locked rows and lease/result guards. AI attempts are persisted; uncertain responses are not blindly retried. Daily budgets, tenant concurrency, actor access and email gates apply before calls. Credentials stay out of logs.

See [billing](../../docs/billing.md) for lots, FEFO, invoices, encrypted billing keys, provider idempotency and refunds. Hosted mock billing is forbidden. Live billing requires production, credentials and code/data policy gates.

## Email, approval and recovery

SMTP variables configure verification, reset and invitations. Development without SMTP writes `.data/mail`; hosted missing SMTP reports unavailable. Tokens are hashed, expire and are consumed once. Reset revokes sessions.

Platform approval needs real evidence, manufacturer, licensed source, exact dimensions and a supported profile. Demo templates cannot become approved production templates. All faces require review; unsupported conditions fail closed.

`scripts/manage-admin.py` grants a chosen registered operator account. `scripts/backup-platform.py` creates encrypted logical snapshots and isolated SQLite integrity checks; it does not assert PostgreSQL/PITR recovery. See [operating readiness](../../docs/production-readiness.md).
