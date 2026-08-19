# syntax=docker/dockerfile:1

# ---- frontend: compile SvelteKit to static files ----
FROM node:22-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- backend: FastAPI serving the API + the compiled frontend ----
FROM python:3.12-slim AS backend
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY backend/ ./
RUN uv sync --frozen --no-dev

COPY --from=frontend-builder /app/frontend/build /app/frontend/build

ENV PATH="/app/backend/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
