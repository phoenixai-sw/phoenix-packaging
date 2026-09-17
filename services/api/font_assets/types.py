from dataclasses import dataclass


@dataclass(frozen=True)
class FontSource:
    """Authorized, checksum-verified original bytes supplied to renderers."""
    asset_id: str
    sha256: str
    family: str
    weight: int
    data: bytes
    license_name: str
    redistribution_allowed: bool
