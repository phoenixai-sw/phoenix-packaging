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
from .structures import build_geometry, geometry_for_scene, new_scene, TEMPLATES
from .barcodes import barcode_geometry, validate_ean13
from .collisions import collision_report, holes_for_face
__all__ += ["build_geometry", "geometry_for_scene", "new_scene", "TEMPLATES", "barcode_geometry", "validate_ean13", "collision_report", "holes_for_face"]
