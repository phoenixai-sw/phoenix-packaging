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
    allowed_origins: tuple[str, ...] = field(default_factory=lambda: tuple(x.strip().rstrip("/") for x in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,http://testserver").split(",") if x.strip()))
    cookie_secure: bool = field(default_factory=lambda: os.getenv("COOKIE_SECURE", "false").lower() == "true")
    worker_secret: str = field(default_factory=lambda: os.getenv("WORKER_SECRET", ""))
    demo_mode: bool = field(default_factory=lambda: os.getenv("DEMO_MODE", "true").lower() == "true")
    app_url: str = field(default_factory=lambda: os.getenv("APP_URL", "http://localhost:3000").rstrip("/"))
    smtp_host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "1025")))
    smtp_username: str = field(default_factory=lambda: os.getenv("SMTP_USERNAME", ""))
    smtp_password: str = field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))
    smtp_starttls: bool = field(default_factory=lambda: os.getenv("SMTP_STARTTLS", "false").lower() == "true")
    mail_from: str = field(default_factory=lambda: os.getenv("MAIL_FROM", "Phoenix Packaging <noreply@phoenix.local>"))
    mail_outbox_dir: Path = field(default_factory=lambda: Path(os.getenv("MAIL_OUTBOX_DIR", str(ROOT / '.data' / 'mail'))))
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
            if self.smtp_host and self.app_url not in self.allowed_origins:
                raise ValueError("Email links require APP_URL matching an allowed HTTPS origin")
            if self.storage_backend == "local":
                raise ValueError("Hosted environments require durable private object storage")
        if self.environment == "production" and self.demo_mode:
            raise ValueError("Fixture/demo mode is forbidden in production; deploy demos as staging")
