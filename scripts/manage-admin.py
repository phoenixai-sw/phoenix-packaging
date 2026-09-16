"""Read-only operations-access check. ADMIN_EMAILS is the only allowlist source."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from services.api.auth import platform_admin
from services.api.config import Settings
from services.api.database import build_database
from services.api.models import User

parser = argparse.ArgumentParser(description="Check Google operations access; never changes database roles")
parser.add_argument("--email", required=True)
parser.add_argument("--revoke", action="store_true", help="Retired: remove the address from ADMIN_EMAILS in the deployment environment")
args = parser.parse_args()
if args.revoke:
    raise SystemExit("No permission was changed. Remove the email from server ADMIN_EMAILS and restart/redeploy. Database is_admin flags are not used.")
settings = Settings()
engine, sessions = build_database(settings)
try:
    with sessions() as db:
        user = db.scalar(select(User).where(User.email == args.email.strip().lower()))
        if user is None:
            raise SystemExit("Account must sign in with Google first. Configure ADMIN_EMAILS in the server environment; this command does not grant access.")
        if not user.is_active or not platform_admin(user, settings):
            raise SystemExit("Operations access is not enabled. It requires active Google identity, authoritative verified email and ADMIN_EMAILS membership. No permission was changed.")
        print("Operations allowlist check passed. Workspace roles and membership restrictions still apply. No permission was changed.")
finally:
    engine.dispose()
