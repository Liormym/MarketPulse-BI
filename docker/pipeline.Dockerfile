# syntax=docker/dockerfile:1.7
#
# Build from the repo root:  docker build -f docker/pipeline.Dockerfile -t marketpulse-pipeline .
# Cluster nodes are linux/amd64:  add --platform linux/amd64 when building on Apple silicon.

# Pinned to the multi-arch index digest. Bump the tag and digest TOGETHER; CI
# fails if this line differs from the one in webapp.Dockerfile.
ARG PYTHON_IMAGE=python:3.11.17-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89

# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build
COPY requirements.txt ./

# CPU-only PyTorch first (see webapp.Dockerfile for why), then the rest.
RUN pip install "torch==2.3.1+cpu" \
        --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple \
 && pip install -r requirements.txt

RUN python -c "import torch; assert '+cpu' in torch.__version__ and torch.version.cuda is None, torch.__version__; print('torch', torch.__version__)"

# FinBERT baked in at a fixed path, so a CronJob-spawned pod never downloads
# ~440 MB on its run. FINBERT_MODEL must match FINBERT_MODEL_NAME in k8s/configmap.yaml.
ARG FINBERT_MODEL=ProsusAI/finbert
ENV HF_HOME=/opt/hf-cache
RUN python -c "import os; from transformers import pipeline; pipeline('sentiment-analysis', model=os.environ['FINBERT_MODEL'])"

# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID=10001
RUN groupadd --system --gid ${APP_UID} app \
 && useradd --system --uid ${APP_UID} --gid app --no-create-home --home-dir /app --shell /usr/sbin/nologin app

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/hf-cache \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder --chown=app:app /opt/hf-cache /opt/hf-cache

WORKDIR /app
COPY --chown=app:app src/ src/
COPY --chown=app:app config/ config/
COPY --chown=app:app db/ db/
COPY --chown=app:app scripts/ scripts/
# Step 5 of the EOD update (compute_investment_scores.py) shares the live
# route's scoring pipeline, which lives in webapp/db.py and webapp/enrichment.py.
COPY --chown=app:app webapp/ webapp/

# The pipeline writes each day's raw extracts under data/raw. Created and owned
# here so it is writable as a non-root user; in a cluster, mount an emptyDir
# (or a volume) over it if the pod's root filesystem is read-only.
RUN mkdir -p /app/data/raw && chown -R app:app /app/data

USER ${APP_UID}:${APP_UID}

CMD ["python", "scripts/run_daily_update.py"]
