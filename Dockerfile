# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.11.2 AS uv

FROM python:3.12-slim-bookworm AS runtime

ARG CODEX_CLI_VERSION=0.157.1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    CODEX_HOME=/data/codex \
    CODEX_NON_INTERACTIVE=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates nodejs npm \
    && npm install --global "@openai/codex@${CODEX_CLI_VERSION}" \
    && test "$(codex --version)" = "codex-cli ${CODEX_CLI_VERSION}" \
    && rm -rf /var/lib/apt/lists/* /root/.npm \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app \
    && mkdir -p /app /data/telegram /data/codex /data/state /tmp/market-digest \
    && chown -R app:app /app /data/telegram /data/codex /data/state /tmp/market-digest

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=app:app src ./src
RUN uv sync --frozen --no-dev

USER app

CMD ["python", "-m", "market_digest"]
