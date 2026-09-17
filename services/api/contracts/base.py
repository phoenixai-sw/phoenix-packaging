"""Shared HTTP envelopes and the application's actual error response shape."""
from typing import Generic, TypeVar
from pydantic import BaseModel, ConfigDict, Field, JsonValue

T = TypeVar("T")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Envelope(ContractModel, Generic[T]):
    data: T
    request_id: str


class APIErrorBody(ContractModel):
    code: str
    message: str
    # Validation messages are strings; revision conflicts also include structured
    # context such as {"base_revision": {"server_revision": 7}}.
    field_errors: dict[str, JsonValue] = Field(default_factory=dict)
    retryable: bool
    request_id: str


ERROR_RESPONSES = {
    status: {"model": APIErrorBody, "description": description}
    for status, description in {
        400: "Malformed request", 401: "Authentication required", 403: "Access denied",
        404: "Resource unavailable", 409: "Revision or idempotency conflict",
        410: "Retired operation or unavailable asset", 413: "Request too large",
        422: "Input or semantic validation failed", 423: "Editor lease required",
        429: "Rate or quota limit", 500: "Internal error", 503: "Service unavailable",
    }.items()
}


def binary_responses(*media_types: str, redirect: bool = True):
    """Opaque bytes, or an authenticated redirect; neither is a JSON envelope."""
    result = {**ERROR_RESPONSES, 200: {
        "description": "Authenticated file bytes",
        "content": {kind: {"schema": {"type": "string", "format": "binary"}} for kind in media_types},
        "headers": {"Content-Disposition": {"schema": {"type": "string"}}},
    }}
    if redirect:
        result[307] = {"description": "Short-lived signed private object URL",
                       "headers": {"Location": {"schema": {"type": "string", "format": "uri"}}}}
    return result
