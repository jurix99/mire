# syntax=docker/dockerfile:1.7
# Images de production de Mire : trois cibles, un seul Dockerfile (contexte = racine du dépôt).
#
#   docker build --platform linux/amd64 --target api    --build-arg ASSET_VERSION=$(git rev-parse --short HEAD) -t mire-api .
#   docker build --platform linux/amd64 --target worker -t mire-worker .
#   docker build --platform linux/amd64 --target agent  -t mire-agent .
#
# api     API FastAPI + front (frontend/), sans modèle d'embeddings : légère, démarrage à froid rapide.
#         Sert aussi au job mire-migrate (python -m app.jobs migrate).
# worker  jobs mire-daily et mire-worker : fastembed + modèle multilingue téléchargé AU BUILD.
# agent   jobs mire-agent-* : agent de recherche (Claude Code non interactif, agent/run_agent.py), sans le
#         backend : il ne parle à Mire que par l'API de l'agent (HTTPS + jeton).
# linux/amd64 : seule architecture exécutée par Container Apps (profil Consumption).
# Le développement local n'utilise pas ce fichier (docker-compose.yml monte le code dans l'image uv).

ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.23-python3.12-trixie-slim

FROM ${UV_IMAGE} AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH
WORKDIR /app
RUN groupadd --system --gid 10001 mire && useradd --system --uid 10001 --gid mire --no-create-home mire

# --- Dépendances (couches mises en cache tant que uv.lock ne change pas) --------------------------

FROM base AS api-deps
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM base AS worker-deps
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group worker --no-install-project
# Modèle d'embeddings téléchargé maintenant (jamais au premier appel) ; voir backend/app/embeddings.py
ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ENV FASTEMBED_CACHE_PATH=/opt/models \
    EMBEDDING_MODEL=${EMBEDDING_MODEL}
RUN python -c "import os; from fastembed import TextEmbedding; \
m = TextEmbedding(os.environ['EMBEDDING_MODEL'], cache_dir=os.environ['FASTEMBED_CACHE_PATH']); \
v = list(m.embed(['Ingénieur backend Python']))[0]; assert len(v) == 384, len(v)" \
    && chmod -R a+rX /opt/models

# --- Images finales -------------------------------------------------------------------------------

FROM api-deps AS api
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/app ./app
COPY frontend /srv/frontend
ARG ASSET_VERSION=dev
ENV FRONTEND_DIR=/srv/frontend \
    ASSET_VERSION=${ASSET_VERSION} \
    RUN_BACKGROUND_THREADS=false \
    LOG_FORMAT=json
USER mire
EXPOSE 8000
# --proxy-headers : derrière l'ingress Container Apps (X-Forwarded-Proto = https)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*", "--no-server-header"]

FROM worker-deps AS worker
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/app ./app
# HF_HUB_OFFLINE : le modèle est dans l'image, aucun téléchargement à l'exécution
ENV RUN_BACKGROUND_THREADS=false \
    LOG_FORMAT=json \
    HF_HUB_OFFLINE=1
USER mire
CMD ["python", "-m", "app.jobs", "worker"]

FROM ${UV_IMAGE} AS agent
# Node.js pour Claude Code (version figée : une mise à jour est un changement d'image, testé comme le reste)
ARG CLAUDE_CODE_VERSION=2.1.301
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
    && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force \
    && apt-get purge -y npm && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
# Claude Code écrit sa configuration dans $HOME : utilisateur non root avec un dossier personnel
RUN groupadd --system --gid 10002 agent && useradd --system --uid 10002 --gid agent --create-home agent
WORKDIR /app
COPY --chown=agent:agent agent/run_agent.py agent/mire_api.py agent/prompt.md agent/prompt-offres.md \
     agent/prompt-recherche.md ./agent/
RUN mkdir -p agent/tmp && chown agent:agent agent/tmp
ENV PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    DISABLE_AUTOUPDATER=1 \
    DISABLE_TELEMETRY=1 \
    HOME=/home/agent
USER agent
ENTRYPOINT ["python", "agent/run_agent.py"]
CMD ["decouverte"]
