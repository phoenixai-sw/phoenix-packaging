"""Deterministic, millimetre-based geometry for the unapproved demo pouch."""

from .validation import (
    DEMO_TEMPLATE_ID,
    GeometryValidationError,
    default_scene,
    normalize_mm,
    validate_dimensions,
    validate_scene,
)

__all__ = ["DEMO_TEMPLATE_ID", "GeometryValidationError", "default_scene", "normalize_mm", "validate_dimensions", "validate_scene"]
