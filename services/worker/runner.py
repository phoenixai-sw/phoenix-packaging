"""Native development worker. Durable jobs live in PostgreSQL/SQLite, not memory."""
import time
from services.api.config import Settings
from services.api.database import build_database
from services.api.jobs import process_pending_jobs
from services.api.storage import build_storage

def main():
    settings = Settings()
    settings.validate()
    engine, sessions = build_database(settings)
    storage = build_storage(settings)
    print('Phoenix review worker started.', flush=True)
    try:
        while True:
            process_pending_jobs(sessions, storage, limit=2)
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()

if __name__ == '__main__':
    main()
