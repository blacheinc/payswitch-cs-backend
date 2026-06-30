FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Build application and dependency wheels once. Production does not install
# editable packages or the development toolchain.
COPY pyproject.toml ./
COPY app ./app
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.1.2" && \
    python -m pip wheel --no-cache-dir --wheel-dir /wheels .


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --system app && \
    useradd --system --gid app --home-dir /app app

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels payswitch-credit-scoring && \
    rm -rf /wheels

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini entrypoint.sh pyproject.toml ./
COPY scripts/create_superuser.py ./scripts/create_superuser.py

RUN chmod +x entrypoint.sh && chown -R app:app /app

EXPOSE 8000

USER app

ENTRYPOINT ["./entrypoint.sh"]
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
