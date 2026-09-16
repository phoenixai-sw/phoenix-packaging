"""Idempotent, explicit development-only example account and editable project."""
from sqlalchemy import select

from .auth import PASSWORDS
from .config import Settings
from .database import Base, build_database
from .main import initial_scene, snapshot_revision
from .models import Project, Tenant, User

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "Phoenix-local-demo-2026!"


def main():
    settings = Settings()
    if settings.environment not in {"development", "test"}:
        raise SystemExit("Seed is disabled outside local development/test. No hosted accounts were changed.")
    settings.validate()
    engine, factory = build_database(settings)
    Base.metadata.create_all(engine)
    with factory() as db:
        user = db.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            tenant = Tenant(name="피닉스 로컬 데모")
            db.add(tenant)
            db.flush()
            user = User(tenant_id=tenant.id, name="로컬 데모", email=DEMO_EMAIL, password_hash=PASSWORDS.hash(DEMO_PASSWORD), role="owner")
            db.add(user)
            db.flush()
        project = db.scalar(select(Project).where(Project.tenant_id == user.tenant_id, Project.name == "산들 현미 · 시작 예제"))
        if project is None:
            project = Project(tenant_id=user.tenant_id, created_by=user.id, name="산들 현미 · 시작 예제", product_name="유기농 현미", brand_name="산들", description="직접 한글을 수정하고 검토 PDF를 받는 로컬 예제", template_id="three-side-seal", width_mm=160, height_mm=230)
            project.scene = initial_scene(project)
            db.add(project)
            db.flush()
            snapshot_revision(db, project, "seed")
        db.commit()
    engine.dispose()
    print(f"Local demo ready: {DEMO_EMAIL}\nLocal-only password: {DEMO_PASSWORD}\nNever use this seeded account in a hosted environment.")


if __name__ == "__main__":
    main()
