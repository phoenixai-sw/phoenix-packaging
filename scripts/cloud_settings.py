"""Validate a complete hosted environment before standalone release mutations."""
import os

from services.api.config import Settings


def validate_cloud_settings(values):
    if not isinstance(values, dict) or values.get("APP_ENV") not in {"staging", "production"}:
        raise ValueError("Cloud configuration requires APP_ENV=staging or production")
    if not isinstance(values.get("APP_URL"), str) or not values["APP_URL"].strip():
        raise ValueError("Cloud configuration requires an explicit APP_URL")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in values.items()):
        raise ValueError("Cloud environment names and values must be strings")
    # The JSON must stand alone. An unrelated shell APP_URL/secret must not
    # conceal a missing deployment variable. These are standalone CLI scripts.
    previous = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(values)
        settings = Settings()
        settings.validate()
        return settings
    finally:
        os.environ.clear()
        os.environ.update(previous)
