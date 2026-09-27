FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2 build (binary wheel usually covers this, kept minimal).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY src/ src/
COPY config/ config/
COPY webapp/ webapp/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Cache FinBERT's weights into the image - the live-refresh path classifies
# headlines inline, so a freshly-scheduled pod shouldn't pull ~440MB from
# Hugging Face on its first request.
RUN python -c "from transformers import pipeline; pipeline('sentiment-analysis', model='ProsusAI/finbert')"

EXPOSE 5050

WORKDIR /app/webapp

# gunicorn, not the Flask dev server - a stateless Deployment expects
# multiple worker processes and graceful reloads, not `app.run(debug=True)`.
CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "2", "--timeout", "60", "app:app"]
