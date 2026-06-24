# Cloud Run image for the GD Skill Match serving app (app.py).
# On startup the app pulls analysis artifacts from GCS (or bootstraps them by
# scraping gsv.fun) via server/cloudstore.py, then serves API + static frontend.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pipeline ./pipeline
COPY server ./server
COPY web ./web

# Cloud Run sets $PORT (default 8080); app.py binds 0.0.0.0:$PORT when PORT is set.
EXPOSE 8080
CMD ["python", "server/app.py"]
