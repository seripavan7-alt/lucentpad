# Prism API image. Build with the REPO ROOT as context (uv.lock and the workspace
# pyproject.toml live there):
#   docker build -f server/Dockerfile -t prism-server .

# ---- build: resolve from uv.lock into /app/.venv --------------------------------------------
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src

# Dependencies first (cached until the lockfile or a pyproject changes).
COPY pyproject.toml uv.lock ./
COPY server/pyproject.toml server/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package prism-server --no-install-workspace

# Then the server package itself, installed non-editable so /src is not needed at runtime.
COPY server/ server/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package prism-server --no-editable

# ---- runtime ---------------------------------------------------------------------------------
FROM python:3.12-slim

RUN groupadd --system --gid 10001 prism \
    && useradd --system --uid 10001 --gid prism --no-create-home --shell /usr/sbin/nologin prism

COPY --from=build --chown=root:root /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

USER prism
WORKDIR /app
EXPOSE 8000

CMD ["python", "-m", "prism_server"]
