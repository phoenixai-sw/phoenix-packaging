from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from ..contracts.base import ContractModel


class Declaration(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    license_name: str = Field(min_length=2, max_length=200)
    license_text: str = Field(min_length=20, max_length=30000)
    source_url: str = Field(min_length=8, max_length=2000)
    rights_holder: str = Field(min_length=2, max_length=200)
    web_use_confirmed: bool = Field(strict=True)
    print_use_confirmed: bool = Field(strict=True)
    redistribution_allowed: bool = Field(default=False, strict=True)

    @field_validator('source_url')
    @classmethod
    def source(cls, value):
        from urllib.parse import urlsplit
        url = urlsplit(value)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('출처는 인증정보 없는 https 주소여야 합니다.')
        return value


class FontUploadBody(Declaration):
    name: str = Field(min_length=1, max_length=160)
    byte_size: int = Field(gt=0, le=20*1024*1024)


class FontData(ContractModel):
    id: str
    name: str
    family: str
    subfamily: str
    weight: int
    sha256: str
    byte_size: int
    glyph_count: int
    fs_type: int
    ascent_ratio: float
    descent_ratio: float
    license_name: str
    source_url: str
    rights_holder: str
    redistribution_allowed: bool
    rights_verification: Literal['user_attested']
    created_at: str
    url: str


class FontList(ContractModel):
    items: list[FontData]
    direct_upload: bool
    max_bytes: int


class GlyphBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(max_length=12000)


class GlyphReport(ContractModel):
    supported: bool
    missing_codepoints: list[str]
    unsupported_shaping: list[str]
