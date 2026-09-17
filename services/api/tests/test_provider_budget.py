"""Estimated provider spend remains independent of customer credit refunds."""
from datetime import timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from services.api.config import Settings
from services.api.database import utcnow
from services.api.feature_models import AuditEvent, ProviderAttempt, ProviderBudget
from services.api.provider_budget import allowance_metadata, budget_summary
from services.api.image_provider import ProviderError
from services.api.tests.test_ai import ai, credits, job, image_result, run, state, quote
from services.api.tests.test_api import image_file, project, register


def live_settings(app, **values):
    settings = app.state.settings
    settings.ai_provider = "openai"
    settings.openai_api_key = "test-only-key"
    settings.ai_require_verified_email = False
    for key, value in values.items():
        setattr(settings, key, value)
    return settings


def test_parallel_claim_reserves_estimate_and_refunds_denied_unit_without_provider_call(ai):
    app, client, _, item = ai
    settings = live_settings(app, ai_daily_cost_limit_usd=2, ai_request_allowance_usd=2)
    created, _ = job(client, item, units=2)
    started, finish = Event(), Event()
    def provider(*_):
        started.set()
        assert finish.wait(10)
        return image_result()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, app, provider, 1)
        assert started.wait(10)
        try:
            assert run(app, lambda *_: pytest.fail("USD reservation must block this provider call"), 1) == 0
        finally:
            finish.set()
        assert first.result() == 1
    result = state(client, created["id"])
    assert result["credit_charged"] == 10 and result["credit_returned"] == 10
    assert credits(client)["available"] == 20
    with app.state.session_factory() as db:
        attempts = list(db.scalars(select(ProviderAttempt)))
        assert len(attempts) == 1 and attempts[0].usage["budget"]["allowance_usd"] == 2
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "provider_budget_blocked")).details["code"] == "AI_DAILY_COST_LIMIT"
        summary = budget_summary(db, settings)
        assert summary["day_guard_usd"] == .0065 and summary["day_unknown_attempts"] == 0


def test_uncertain_call_keeps_allowance_even_after_customer_refund(ai):
    app, client, _, item = ai
    settings = live_settings(app, ai_daily_cost_limit_usd=2)
    created, _ = job(client, item, units=2)
    count = 0
    def uncertain(*_):
        nonlocal count
        count += 1
        raise ProviderError("AI_TIMEOUT_UNCERTAIN", "uncertain", uncertain=True)
    run(app, uncertain, limit=2)
    assert count == 1 and credits(client)["available"] == 30
    with app.state.session_factory() as db:
        summary = budget_summary(db, settings)
        assert summary["day_guard_usd"] == 2 and summary["day_unknown_attempts"] == 1
        assert summary["alerts"][0]["code"] == "AI_DAILY_COST_LIMIT"
        assert db.scalar(select(ProviderBudget)).requested_units == 1


def test_recorded_cost_above_allowance_blocks_future_calls_and_month_alert_is_explicit(ai):
    app, client, _, item = ai
    settings = live_settings(app, ai_daily_cost_limit_usd=3, ai_monthly_budget_alert_usd=1)
    created, _ = job(client, item, units=2)
    def costly(*_):
        result = image_result()
        result.metadata["cost_usd"] = 4
        return result
    assert run(app, costly, limit=2) == 1
    with app.state.session_factory() as db:
        summary = budget_summary(db, settings)
        assert summary["day_guard_usd"] == 4 and summary["estimate_only"] is True
        assert {row["code"] for row in summary["alerts"]} == {"AI_DAILY_COST_LIMIT", "AI_MONTHLY_BUDGET_ALERT"}
        attempt = db.scalar(select(ProviderAttempt))
        attempt.created_at = utcnow() - timedelta(days=1)
        db.commit()
        assert budget_summary(db, settings)["day_guard_usd"] == 0
    assert state(client, created["id"])["credit_charged"] == 10


def test_unit_cap_still_blocks_before_any_usd_spend(ai):
    app, client, _, item = ai
    live_settings(app, ai_daily_units=0)
    created, _ = job(client, item)
    assert run(app, lambda *_: pytest.fail("unit cap must stop the call")) == 0
    assert state(client, created["id"])["result"]["units"][0]["error_code"] == "AI_DAILY_LIMIT"
    assert credits(client)["available"] == 30


@pytest.mark.parametrize("failure", ["reference_download", "permission_recheck"])
def test_failure_before_provider_releases_usd_allowance_and_customer_reservation(ai, monkeypatch, failure):
    from services.api import ai_jobs
    app, client, _, item = ai
    settings = live_settings(app, ai_daily_cost_limit_usd=2, ai_request_allowance_usd=2)
    reference = client.post("/v1/assets", data={"project_id": item["id"]},
                            files={"file": image_file()}).json()["data"]
    estimate = quote(client, item, action="image.edit.standard", reference_asset_id=reference["id"])
    response = client.post("/v1/jobs", headers={"Idempotency-Key": "pre-call-failure"}, json={"quote_id": estimate["id"]})
    assert response.status_code == 202, response.text
    original_recheck = ai_jobs.recheck_actor
    rechecks = 0

    def recheck(*args):
        nonlocal rechecks
        rechecks += 1
        if failure == "permission_recheck" and rechecks == 2:
            raise ProviderError("AI_WRITE_REVOKED", "Revoked before the paid call")
        return original_recheck(*args)

    class ReferenceStorage:
        def get(self, key):
            if failure == "reference_download":
                raise OSError("Reference storage is unavailable before the paid call")
            return app.state.storage.get(key)

    monkeypatch.setattr(ai_jobs, "recheck_actor", recheck)
    assert run(app, lambda *_: pytest.fail("provider must not be invoked"), limit=1, storage=ReferenceStorage()) == 1
    result = state(client, response.json()["data"]["id"])
    assert result["status"] == "failed"
    assert result["credit_charged"] == 0 and result["credit_returned"] == 10
    assert credits(client)["available"] == 30
    with app.state.session_factory() as db:
        attempt = db.scalar(select(ProviderAttempt))
        assert attempt.status == "canceled_before_provider" and attempt.cost_usd is None
        summary = budget_summary(db, settings)
        assert summary["day_guard_usd"] == 0 and summary["day_unknown_attempts"] == 0
    # A later request can use the released USD allowance; no provider retry was
    # needed for the failed reference or the revoked permission check.
    next_job, _ = job(client, item, key="after-pre-call-failure")
    assert run(app, lambda *_: image_result(), limit=1) == 1
    assert state(client, next_job["id"])["credit_charged"] == 10


def test_unclassified_provider_exception_keeps_allowance_after_refund(ai):
    app, client, _, item = ai
    settings = live_settings(app, ai_daily_cost_limit_usd=2, ai_request_allowance_usd=2)
    created, _ = job(client, item, units=2)
    calls = 0

    def no_response(*_):
        nonlocal calls
        calls += 1
        raise TimeoutError("Adapter did not return a definitive provider result")

    assert run(app, no_response, limit=2) == 1
    assert calls == 1 and credits(client)["available"] == 30
    assert state(client, created["id"])["credit_charged"] == 0
    with app.state.session_factory() as db:
        attempt = db.scalar(select(ProviderAttempt))
        assert attempt.status == "uncertain"
        summary = budget_summary(db, settings)
        assert summary["day_guard_usd"] == 2 and summary["day_unknown_attempts"] == 1


def test_attempt_uses_same_utc_claim_instant_as_budget_when_flush_crosses_midnight(ai, monkeypatch):
    from services.api import ai_jobs
    app, client, _, item = ai
    settings = live_settings(app)
    job(client, item)
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    claim = today - timedelta(microseconds=1)
    # The ORM's default clock is already in the next day. Its default must not
    # independently decide which daily budget owns this committed attempt.
    monkeypatch.setattr(ai_jobs, "utcnow", lambda: claim)
    assert run(app, lambda *_: image_result(), limit=1) == 1
    with app.state.session_factory() as db:
        attempt = db.scalar(select(ProviderAttempt))
        stored = attempt.created_at.replace(tzinfo=timezone.utc) if attempt.created_at.tzinfo is None else attempt.created_at
        assert stored == claim
        budget = db.scalar(select(ProviderBudget))
        assert budget.day == attempt.usage["budget"]["utc_day"] == claim.strftime("%Y-%m-%d")
        assert budget.requested_units == 1
        assert budget_summary(db, settings, now=claim)["day_guard_usd"] == .0065
        assert budget_summary(db, settings, now=today)["day_guard_usd"] == 0
        local = claim.astimezone(timezone(timedelta(hours=9)))
        assert allowance_metadata(settings, local)["utc_day"] == budget.day
        assert budget_summary(db, settings, now=local)["date"] == budget.day


def test_daily_usd_reservation_is_global_across_tenants(ai):
    app, first, _, item = ai
    settings = live_settings(app, ai_daily_cost_limit_usd=2, ai_request_allowance_usd=2)
    first_job, _ = job(first, item)
    started, finish = Event(), Event()
    with TestClient(app) as second:
        register(second, "other-budget-tenant@example.com")
        second_job, _ = job(second, project(second))

        def provider(*_):
            started.set()
            assert finish.wait(10)
            return image_result()

        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(run, app, provider, 1)
            assert started.wait(10)
            try:
                assert run(app, lambda *_: pytest.fail("other tenant must see reserved allowance"), 1) == 0
            finally:
                finish.set()
            assert future.result() == 1
        assert state(first, first_job["id"])["credit_charged"] == 10
        assert state(second, second_job["id"])["credit_returned"] == 10
        assert credits(first)["available"] == 20 and credits(second)["available"] == 30
        with app.state.session_factory() as db:
            assert len(list(db.scalars(select(ProviderAttempt)))) == 1
            assert budget_summary(db, settings)["day_guard_usd"] == .0065


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -1, 0])
def test_invalid_budget_settings_fail_closed(number):
    settings = Settings(ai_daily_cost_limit_usd=number)
    with pytest.raises(ValueError, match="USD budget"):
        settings.validate()
