"""Vercel Python function entrypoint for the dedicated API project."""
from services.api.main import app

__all__ = ["app"]
