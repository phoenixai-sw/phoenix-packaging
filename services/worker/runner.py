"""Native development worker. Durable jobs live in PostgreSQL/SQLite, not memory."""
import time
from services.api.config import Settings
from services.api.database import build_database
from services.api.worker_service import process_all_jobs
from services.api.storage import build_storage

def main():
    settings = Settings()
    settings.validate()
    engine, sessions = build_database(settings)
    storage = build_storage(settings)
    print('Phoenix platform worker started.', flush=True)
    try:
        while True:
            process_all_jobs(sessions, storage, settings)
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()

if __name__ == '__main__':
    main()
