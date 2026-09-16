# Frontend integration contract

The browser uses `/api/v1`; FastAPI routes use `/v1`. Pydantic request models and `/openapi.json` are the authoritative field definitions. Responses use `{data,request_id}`, collection routes use `{items:[]}` where applicable, and authenticated mutations require CSRF and Origin checks.

- `/me`: user role, explicit platform-admin flag, active tenant and memberships. Role checks remain server-side.
- `/brands`, `/products`, `/workspaces`, `/team`: catalog and tenant management. Product updates preserve stable variant IDs. Workspace access depends on active membership and plan entitlement.
- `/projects/{id}/bindings/preview` and `/apply`: preview first; apply carries the current base revision. Manual text edits detach their binding.
- `/quotes` → `/jobs`: image quote carries action, units, prompt, face, project revision and optional reference. Job submission uses quote ID and an Idempotency-Key. Jobs expose per-result status and reserved/charged/returned credits.
- `/assets/uploads` → signed PUT → `/assets/uploads/{id}/complete`: hosted20MiB private direct upload; local fallback uses multipart `/assets`. Finalization validates image bytes and project access.
- `/geometry/validate`, `/geometry/barcode`, `/templates`, `/print-profiles`: structural and barcode authority. Registered exact dimensions and supported output capabilities constrain production.
- `/preflight` → `/exports`: review or production, immutable revision, all reviewed face IDs and production quote where required. Private download endpoint returns PDF or ZIP according to job kind.
- `/admin/overview`, `/admin/evidence`, `/admin/template-versions`, `/admin/print-profiles`: platform administrator only. Approval takes evidence IDs plus named approver and notes; exact schema is in the API models.
- `/printer-intakes`: explicit test/manufacturer source and technical/aesthetic disposition. Real metrics deduplicate immutable jobs and exclude unreplied submissions.
- `/billing`: policy, wallet, subscription, orders and payment capabilities. `/billing/orders`, `/billing/confirm`, `/billing/billing-key/confirm`, `/billing/change-plan`, `/billing/cancel-renewal`, `/billing/orders/{order_id}/refund` implement payment transitions.

The UI waits for server results and displays unavailable/test states. It never fabricates payment, delivery, AI or manufacturing success.
