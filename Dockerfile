FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 先安装锁定依赖，业务代码变化时可以复用 Docker 构建缓存。
COPY pyproject.toml uv.lock README.md ./
COPY app/__init__.py app/__init__.py
RUN uv sync --frozen --no-dev --no-install-project

COPY app app
COPY resources resources
COPY api_main.py ./
RUN uv sync --frozen --no-dev

RUN mkdir -p /app/outputs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD [".venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD [".venv/bin/python", "api_main.py"]
