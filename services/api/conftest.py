import pytest

from services.api.billing import policy

# Ledger tests count credits against this fixed table; prices still come from the shipped seed.
# The shipped credit amounts are checked in tests/test_pricing_seed.py.
LEDGER_TEST_TRIAL_CREDITS = 30
LEDGER_TEST_PLAN_CREDITS = {"starter": 500, "pro": 1500, "partner": 4500}


@pytest.fixture(autouse=True)
def ledger_test_credits(monkeypatch):
    shipped = policy.seed_pricing()
    pinned = {**shipped, "trial": {**shipped["trial"], "credits": LEDGER_TEST_TRIAL_CREDITS},
              "plans": [{**plan, "credits": LEDGER_TEST_PLAN_CREDITS[plan["id"]]} for plan in shipped["plans"]]}
    monkeypatch.setattr(policy, "seed_pricing", lambda: pinned)
