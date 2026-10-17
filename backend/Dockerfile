FROM python:3.11-slim

ARG INSTALL_DEV=false

ARG EMBEDDING_MODEL=BAAI/bge-small-en-v1.5

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EMBEDDING_CACHE_DIR=/opt/embeddings

WORKDIR /app

# Install dependencies first so code edits do not invalidate this layer.
COPY pyproject.toml ./
RUN mkdir app scripts && touch app/__init__.py scripts/__init__.py \
    && if [ "$INSTALL_DEV" = "true" ]; then pip install -e ".[dev]"; else pip install -e .; fi

# Bake the embedding model into the image so the app and tests never download it at runtime.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('$EMBEDDING_MODEL', cache_dir='$EMBEDDING_CACHE_DIR')"

COPY . .

RUN useradd --create-home --uid 10001 app && chown -R app /app "$EMBEDDING_CACHE_DIR"
USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
