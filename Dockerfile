# Haggle — one container that serves the API and the web app together.
# Works on any host that runs a container: Render, Railway, Fly.io, Hugging
# Face Spaces, Google Cloud Run, a VPS.
FROM python:3.12-slim

WORKDIR /app
COPY api /app/api
COPY web /app/web

RUN pip install --no-cache-dir ./api

# The static mount finds the web dir via this; the demo seed fills an empty shop.
ENV HAGGLE_WEB_DIR=/app/web
ENV HAGGLE_SEED=1
# Keep the event log on a writable path; mount a volume here to persist it.
ENV HAGGLE_DB=/app/data/haggle.db

EXPOSE 8000
CMD ["sh", "-c", "mkdir -p /app/data && uvicorn haggle.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
