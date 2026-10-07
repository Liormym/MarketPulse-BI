# syntax=docker/dockerfile:1.7
#
# Build from the repo root:  docker build -f docker/webapp.Dockerfile -t marketpulse-webapp .
# Cluster nodes are linux/amd64:  add --platform linux/amd64 when building on Apple silicon.

# Pinned to the multi-arch index digest, so every build gets the identical base
# on amd64 (cluster) and arm64 (local). Bump the tag and digest TOGETHER; CI
# fails if this line differs from the one in pipeline.Dockerfile.
ARG PYTHON_IMAGE=python:3.11.17-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89

# ---------------------------------------------------------------------------
# builder: everything that needs the network or is only needed to install
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build
COPY requirements.txt requirements-web.txt ./

# CPU-only PyTorch first. The default PyPI build drags in several GB of CUDA
# libraries a CPU-only cluster can never use. The explicit "+cpu" version exists
# only on PyTorch's own index, so the choice is deterministic; PyPI stays
# available for torch's own dependencies. The "-r" install below then sees
# torch==2.3.1 as already satisfied (the "+cpu" local version matches) and does
# not replace it.
# No compiler is installed: every dependency ships a manylinux wheel except
# sgmllib3k, a pure-Python package pip builds without one.
RUN pip install "torch==2.3.1+cpu" \
        --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple \
 && pip install -r requirements-web.txt

# Fail the build, loudly, if a CUDA build of torch ever sneaks back in.
RUN python -c "import torch; assert '+cpu' in torch.__version__ and torch.version.cuda is None, torch.__version__; print('torch', torch.__version__)"

# Bake FinBERT into the image at a fixed path. The runtime stage points HF_HOME
# here and forbids network model lookups, so a restarted worker (or a freshly
# scheduled pod) never re-downloads ~440 MB. FINBERT_MODEL must match the
# FINBERT_MODEL_NAME the app runs with (k8s/configmap.yaml).
ARG FINBERT_MODEL=ProsusAI/finbert
ENV HF_HOME=/opt/hf-cache
RUN python -c "import os; from transformers import pipeline; pipeline('sentiment-analysis', model=os.environ['FINBERT_MODEL'])"

# ---------------------------------------------------------------------------
# runtime: venv + model cache + app code, running as a non-root user
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
COPY --chown=app:app webapp/ webapp/

# Numeric, so Kubernetes' runAsNonRoot can verify it without resolving a name.
USER ${APP_UID}:${APP_UID}

EXPOSE 5050
WORKDIR /app/webapp

# gunicorn, not the Flask dev server - a stateless Deployment expects multiple
# worker processes and graceful reloads, not `app.run(debug=True)`.
CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "2", "--timeout", "60", "app:app"]
