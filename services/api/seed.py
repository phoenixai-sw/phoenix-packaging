"""Add one sample project to an existing Google user in local development only."""
import argparse
from sqlalchemy import select

from .config import Settings
from .database import Base, build_database
from .main import initial_scene, snapshot_revision
from .models import Project, User

def main():
    parser = argparse.ArgumentParser(description="Create a local sample after Google sign-in")
    parser.add_argument("--email", required=True, help="Existing Google account email")
    args = parser.parse_args()
    settings = Settings()
    if settings.environment not in {"development", "test"}:
        raise SystemExit("Seed is disabled outside local development/test. No hosted accounts were changed.")
    settings.validate()
    engine, factory = build_database(settings)
    Base.metadata.create_all(engine)
    with factory() as db:
        user = db.scalar(select(User).where(User.email == args.email.strip().lower()))
        if user is None or not user.google_sub or not user.is_active:
            raise SystemExit("Sign in with Google locally before creating sample projects. No account or credentials were created.")
        project = db.scalar(select(Project).where(Project.tenant_id == user.tenant_id, Project.name == "산들 현미 · 시작 예제"))
        if project is None:
            project = Project(tenant_id=user.tenant_id, created_by=user.id, name="산들 현미 · 시작 예제", product_name="유기농 현미", brand_name="산들", description="직접 한글을 수정하고 검토 PDF를 받는 로컬 예제", template_id="three-side-seal", width_mm=160, height_mm=230)
            project.scene = initial_scene(project)
            db.add(project)
            db.flush()
            snapshot_revision(db, project, "seed")
        db.commit()
    engine.dispose()
    print("Local sample project is ready in the existing Google account. No credentials were created.")


if __name__ == "__main__":
    main()
