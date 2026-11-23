FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
# onclappc02 umask 027 leaves checked-out files 640; nobody must read them
RUN uv sync --frozen --no-dev && chmod -R a+rX /app
USER nobody
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "bugsigdb_app.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
