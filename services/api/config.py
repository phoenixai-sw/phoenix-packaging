"""Explicit settings; secrets never leave the backend."""
from dataclasses import dataclass, field
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", f"sqlite:///{ROOT / '.data' / 'phoenix.db'}"))
    storage_backend: str = field(default_factory=lambda: os.getenv("STORAGE_BACKEND", "local"))
    storage_dir: Path = field(default_factory=lambda: Path(os.getenv("STORAGE_DIR", str(ROOT / '.data' / 'storage'))))
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_service_role_key: str = field(default_factory=lambda: os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))
    supabase_storage_bucket: str = field(default_factory=lambda: os.getenv("SUPABASE_STORAGE_BUCKET", "phoenix-private"))
    supabase_upload_bucket: str = field(default_factory=lambda: os.getenv("SUPABASE_UPLOAD_BUCKET", ""))
    allowed_origins: tuple[str, ...] = field(default_factory=lambda: tuple(x.strip().rstrip("/") for x in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,http://testserver").split(",") if x.strip()))
    cookie_secure: bool = field(default_factory=lambda: os.getenv("COOKIE_SECURE", "false").lower() == "true")
    worker_secret: str = field(default_factory=lambda: os.getenv("WORKER_SECRET", ""))
    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "fixture"))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    image_model: str = field(default_factory=lambda: os.getenv("IMAGE_MODEL", "gpt-image-2.5-sunburst"))
    ai_image_models: tuple[str, ...] = field(default_factory=lambda: tuple(x.strip() for x in os.getenv("AI_IMAGE_MODELS", "gpt-image-2.5-sunburst,gpt-image-2.5-flare").split(",") if x.strip()))
    ai_daily_units: int = field(default_factory=lambda: int(os.getenv("AI_DAILY_UNIT_LIMIT", "30")))
    ai_high_enabled: bool = field(default_factory=lambda: os.getenv("AI_HIGH_ENABLED", "false").lower() == "true")
    ai_allowed_emails: tuple[str, ...] = field(default_factory=lambda: tuple(x.strip().lower() for x in os.getenv("AI_ALLOWED_EMAILS", "").split(",") if x.strip()))
    ai_require_verified_email: bool = field(default_factory=lambda: os.getenv("AI_REQUIRE_VERIFIED_EMAIL", "true").lower() == "true")
    enable_production_export: bool = field(default_factory=lambda: os.getenv("ENABLE_PRODUCTION_EXPORT", "false").lower() == "true")
    policy_approved: bool = field(default_factory=lambda: os.getenv("OPERATING_POLICY_APPROVED", "false").lower() == "true")
    demo_mode: bool = field(default_factory=lambda: os.getenv("DEMO_MODE", "true").lower() == "true")
    app_url: str = field(default_factory=lambda: os.getenv("APP_URL", "http://localhost:3000").rstrip("/"))
    google_client_id: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_ID", "").strip())
    admin_emails: tuple[str, ...] = field(default_factory=lambda: tuple(x.strip().lower() for x in os.getenv("ADMIN_EMAILS", "").split(",") if x.strip()))
    session_days: int = 7
    upload_limit: int = field(default_factory=lambda: int(os.getenv("UPLOAD_LIMIT_BYTES", str(20 * 1024 * 1024))))

    def validate(self) -> None:
        if self.environment not in {"development", "test", "staging", "production"}:
            raise ValueError("APP_ENV must be development, test, staging or production")
        if self.storage_backend not in {"local", "supabase"}:
            raise ValueError("STORAGE_BACKEND must be local or supabase")
        if self.storage_backend == "supabase" and not (self.supabase_url.startswith("https://") and self.supabase_service_role_key):
            raise ValueError("Private Supabase Storage requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
        if self.environment in {"staging", "production"}:
            if self.database_url.startswith("sqlite"):
                raise ValueError("Hosted environments require PostgreSQL; local SQLite is development only")
            if not self.cookie_secure:
                raise ValueError("Hosted environments require COOKIE_SECURE=true")
            if not self.allowed_origins or any(not x.startswith("https://") for x in self.allowed_origins):
                raise ValueError("Hosted environments require explicit HTTPS ALLOWED_ORIGINS")
            if self.app_url not in self.allowed_origins:
                raise ValueError("APP_URL must match an allowed HTTPS origin")
            if self.storage_backend == "local":
                raise ValueError("Hosted environments require durable private object storage")
        if self.environment == "production" and self.demo_mode:
            raise ValueError("Fixture/demo mode is forbidden in production; deploy demos as staging")
        if self.ai_provider not in {"fixture", "openai", "disabled"}:
            raise ValueError("Unsupported AI_PROVIDER")
        if self.environment == "production" and self.ai_provider == "fixture":
            raise ValueError("Fixture image provider is forbidden in production")
        if self.ai_provider == "openai" and not self.openai_api_key:
            raise ValueError("OpenAI image generation requires a server API key")
        if self.image_model not in {"gpt-image-2.5-sunburst", "gpt-image-2.5-flare"}:
            raise ValueError("Only explicitly supported GPT Image 2.5 presets are enabled")
        if not self.ai_image_models or set(self.ai_image_models) - {"gpt-image-2.5-sunburst", "gpt-image-2.5-flare"} or self.image_model not in self.ai_image_models:
            raise ValueError("AI_IMAGE_MODELS must allow supported models and include IMAGE_MODEL")
        if self.ai_daily_units < 0 or self.ai_daily_units > 10000:
            raise ValueError("AI daily unit limit is invalid")
        if self.enable_production_export and not self.policy_approved:
            raise ValueError("Production export requires confirmed operating policies")
