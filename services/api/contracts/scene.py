"""Read models preserve immutable stored scenes instead of re-running input edits.

In particular legacy scenes contain IEEE-754 coordinates such as
36.800000000000004. The input model rounds new writes, but reading an old
revision must never rewrite its coordinates or synthesize barcode ownership.
Field types/constraints and face membership validation still apply.
"""
from pydantic import field_validator, model_validator
from ..schemas import Scene, Face, SceneObject, ImageCrop


class StoredImageCrop(ImageCrop):
    @model_validator(mode='after')
    def within_source(self):
        from ..image_crop import normalized_crop
        normalized_crop(self.model_dump())
        return self


class StoredSceneObject(SceneObject):
    crop: StoredImageCrop | None = None

    @field_validator('x_mm', 'y_mm', 'width_mm', 'height_mm', 'rotation_deg')
    @classmethod
    def normalize_mm(cls, value):
        return value

    @model_validator(mode='after')
    def validate_kind(self):
        # Input write validation and output preflight remain authoritative.
        # This is the read representation, not a second mutation of history.
        return self


class StoredFace(Face):
    objects: list[StoredSceneObject]


class StoredScene(Scene):
    faces: list[StoredFace]
