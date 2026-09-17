from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column
from ..database import Base, new_id, utcnow


class FontAsset(Base):
    __tablename__ = 'font_assets'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey('users.id'))
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    original_name: Mapped[str] = mapped_column(String(160))
    family: Mapped[str] = mapped_column(String(120))
    subfamily: Mapped[str] = mapped_column(String(120))
    weight: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    glyph_count: Mapped[int] = mapped_column(Integer)
    fs_type: Mapped[int] = mapped_column(Integer)
    ascent_ratio: Mapped[float] = mapped_column(Float)
    descent_ratio: Mapped[float] = mapped_column(Float)
    license_name: Mapped[str] = mapped_column(String(200))
    license_text: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(2000))
    rights_holder: Mapped[str] = mapped_column(String(200))
    redistribution_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FontUploadSession(Base):
    __tablename__ = 'font_upload_sessions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    byte_size: Mapped[int] = mapped_column(Integer)
    declaration: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    font_asset_id: Mapped[str | None] = mapped_column(ForeignKey('font_assets.id'), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@event.listens_for(FontAsset, 'before_update')
@event.listens_for(FontAsset, 'before_delete')
def immutable_font(*_):
    raise ValueError('Published font assets and their rights records are immutable')
