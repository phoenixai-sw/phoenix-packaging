"""SMTP/Mailpit delivery and explicit development-only local mail outbox."""
from datetime import timedelta
from email.message import EmailMessage
import secrets
import smtplib
import ssl
from urllib.parse import urlencode
from uuid import uuid4

from sqlalchemy import update

from .auth import hash_token
from .database import utcnow
from .errors import APIError
from .models import AuthToken


def mail_available(settings):
    return bool(settings.smtp_host) or settings.environment in {"development", "test"}


def deliver(message: EmailMessage, settings):
    if settings.smtp_host:
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as client:
                if settings.smtp_starttls:
                    client.starttls(context=ssl.create_default_context())
                if settings.smtp_username:
                    client.login(settings.smtp_username, settings.smtp_password)
                client.send_message(message)
            return "smtp"
        except (OSError, smtplib.SMTPException):
            raise APIError(503, "MAIL_UNAVAILABLE", "인증 메일을 보내지 못했습니다. 잠시 후 다시 시도해 주세요.", retryable=True) from None
    if settings.environment in {"development", "test"}:
        settings.mail_outbox_dir.mkdir(parents=True, exist_ok=True)
        (settings.mail_outbox_dir / f"{uuid4()}.eml").write_bytes(message.as_bytes())
        return "local_outbox"
    raise APIError(503, "MAIL_NOT_CONFIGURED", "이메일 인증 서비스 연결을 준비하고 있습니다.", retryable=False)


def issue_email_token(db, user, kind, settings):
    if not mail_available(settings):
        raise APIError(503, "MAIL_NOT_CONFIGURED", "이메일 인증 서비스 연결을 준비하고 있습니다.")
    token = secrets.token_urlsafe(48)
    now = utcnow()
    # Any prior link of the same purpose becomes unusable when a new link issues.
    db.execute(update(AuthToken).where(AuthToken.user_id == user.id, AuthToken.kind == kind, AuthToken.consumed_at.is_(None)).values(consumed_at=now))
    db.add(AuthToken(token_hash=hash_token(token), user_id=user.id, kind=kind, expires_at=now + timedelta(minutes=30 if kind == "reset" else 1440)))
    label = "비밀번호 재설정" if kind == "reset" else "이메일 확인"
    link = settings.app_url + "/auth?" + urlencode({"mode": kind, "token": token})
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = user.email
    message["Subject"] = f"Phoenix Packaging {label}"
    message.set_content(f"{user.name}님, 아래 링크에서 {label}을 완료해 주세요.\n\n{link}\n\n본인이 요청하지 않았다면 이 메일을 무시해 주세요.\n이 링크는 한 번만 사용할 수 있습니다.")
    delivery = deliver(message, settings)
    db.commit()
    return delivery
