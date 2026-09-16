"""Redis wakes the worker; the relational database remains the durable queue."""
import os
from celery import Celery
from services.api.config import Settings
from services.api.database import build_database
from services.api.worker_service import process_all_jobs
from services.api.storage import build_storage

app = Celery('phoenix', broker=os.getenv('REDIS_URL', 'redis://localhost:6379/0'))
app.conf.update(timezone='UTC', task_acks_late=True, worker_prefetch_multiplier=1,
    task_time_limit=800, beat_schedule={'review-jobs': {'task': 'phoenix.review_jobs', 'schedule': 5.0}})

@app.task(name='phoenix.review_jobs')
def review_jobs():
    settings = Settings()
    settings.validate()
    engine, sessions = build_database(settings)
    try:
        return process_all_jobs(sessions, build_storage(settings), settings)
    finally:
        engine.dispose()
