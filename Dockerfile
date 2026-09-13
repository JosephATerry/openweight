FROM node:26.8-bookworm-slim AS frontend-build

WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts

COPY frontend/ ./
ARG VITE_DEMO_MODE=false
ENV VITE_DEMO_MODE=${VITE_DEMO_MODE}
RUN npm run build

FROM python:3.12.11-slim-bookworm AS runtime

ARG OPENWEIGHT_PRELOAD_DEMO_EMBEDDINGS=false
ARG OPENWEIGHT_BUILD_SHA=unreleased

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    HF_HUB_DISABLE_TELEMETRY=1 \
    OPENWEIGHT_BUILD_SHA=${OPENWEIGHT_BUILD_SHA} \
    OPENWEIGHT_API_HOST=0.0.0.0 \
    OPENWEIGHT_API_PORT=8000

WORKDIR /app

COPY requirements.txt requirements-container.txt ./
RUN python -m pip install \
        --disable-pip-version-check \
        --no-cache-dir \
        --requirement requirements-container.txt \
    && groupadd --gid 10001 openweight \
    && useradd \
        --uid 10001 \
        --gid openweight \
        --create-home \
        --home-dir /home/openweight \
        --shell /usr/sbin/nologin \
        openweight

# A Space build may explicitly preload the public Qwen query encoder. Normal
# local builds stay small and retain the existing lazy local-model behavior.
RUN if [ "${OPENWEIGHT_PRELOAD_DEMO_EMBEDDINGS}" = "true" ]; then \
        python -c "import shutil; from huggingface_hub import snapshot_download; path = snapshot_download('Qwen/Qwen3-Embedding-0.6B', revision='97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3', local_dir='/app/models/qwen3-embedding-0.6b', allow_patterns=['*.json', '*.safetensors', '*.model', '*.txt']); shutil.rmtree(path + '/.cache', ignore_errors=True)"; \
    fi

COPY --chown=openweight:openweight src ./src
COPY --chown=openweight:openweight data/policies ./data/policies
COPY --chown=openweight:openweight deploy/huggingface/policy_index.json ./deploy/huggingface/policy_index.json
COPY --chown=openweight:openweight deploy/huggingface/policy_index.npz ./deploy/huggingface/policy_index.npz
COPY --chown=openweight:openweight scripts/setup_operations.py ./scripts/setup_operations.py
COPY --chown=openweight:openweight scripts/setup_security.py ./scripts/setup_security.py
COPY --chown=openweight:openweight scripts/migrate_database.py ./scripts/migrate_database.py
COPY --chown=openweight:openweight scripts/index_policy_corpus.py ./scripts/index_policy_corpus.py
COPY --chown=openweight:openweight docker/api/healthcheck.py ./docker/api/healthcheck.py
COPY --from=frontend-build --chown=openweight:openweight \
    /build/frontend/dist ./frontend/dist

USER openweight

EXPOSE 8000 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "/app/docker/api/healthcheck.py"]

STOPSIGNAL SIGTERM

CMD ["python", "-m", "openweight_platform.api.run"]
