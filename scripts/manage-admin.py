"""Explicit local operator command; tenant ownership never grants platform admin."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from services.api.config import Settings
from services.api.database import build_database
from services.api.models import User
from services.api.feature_models import AuditEvent

parser=argparse.ArgumentParser()
parser.add_argument("--email",required=True)
parser.add_argument("--revoke",action="store_true")
args=parser.parse_args()
engine,sessions=build_database(Settings())
with sessions() as db:
    user=db.scalar(select(User).where(User.email==args.email.strip().lower()))
    if user is None: raise SystemExit("Account must register before platform admin can be assigned.")
    user.is_admin=not args.revoke
    db.add(AuditEvent(tenant_id=user.tenant_id,actor_id=user.id,action="platform_admin_revoked" if args.revoke else "platform_admin_granted",entity_id=user.id,details={"source":"local_operator_cli"}))
    db.commit()
print("Platform admin assignment updated.")
engine.dispose()
