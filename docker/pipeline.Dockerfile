FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2 build (binary wheel usually covers this, kept minimal).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY db/ db/
COPY scripts/ scripts/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Cache the FinBERT model weights into the image so a CronJob-spawned Pod
# doesn't re-download ~440MB from HuggingFace on every scheduled run.
RUN python -c "from transformers import pipeline; pipeline('sentiment-analysis', model='ProsusAI/finbert')"

CMD ["python", "-m", "marketpulse.pipeline"]
