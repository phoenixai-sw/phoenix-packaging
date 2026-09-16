FROM python:3.12-slim
WORKDIR /app
COPY services/api/requirements.txt /app/services/api/requirements.txt
COPY services/worker/requirements.txt /app/services/worker/requirements.txt
RUN pip install --no-cache-dir -r services/worker/requirements.txt
COPY services /app/services
COPY fixtures /app/fixtures
COPY config /app/config
CMD ["celery", "-A", "services.worker.app", "worker", "--loglevel=INFO"]
