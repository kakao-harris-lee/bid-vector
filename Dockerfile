# syntax=docker/dockerfile:1.7
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble AS python-base

WORKDIR /app

# Runtime defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Copy dependency manifests first for better layer caching.
COPY requirements/ ./requirements/
COPY requirements.txt ./

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip setuptools wheel

FROM python-base AS deps-runtime
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r requirements/runtime.txt

FROM deps-runtime AS deps-embedding
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1+cpu && \
    python -m pip install -r requirements/ml-embedding.txt

FROM deps-runtime AS deps-training
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r requirements/ml-training.txt

FROM deps-embedding AS deps-ml-full
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r requirements/ml-training.txt

FROM node:24-bookworm-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --legacy-peer-deps
COPY frontend/ ./
RUN npm run build

FROM deps-runtime AS api-runtime
COPY . .
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
EXPOSE 3000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000"]

FROM deps-embedding AS api-embedding
COPY . .
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
EXPOSE 3000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000"]

FROM deps-training AS api-training
COPY . .
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
EXPOSE 3000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000"]

FROM deps-ml-full AS api-ml-full
COPY . .
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
EXPOSE 3000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000"]
