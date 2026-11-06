# Base image pinned by digest. To update: docker pull python:3.13-slim, record its digest here in all three places.
FROM python:3.13-slim@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc AS builder

WORKDIR /app

# Install system dependencies needed for compiling native packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt


# Everything the running application needs, and nothing else
FROM python:3.13-slim@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc AS runtime

WORKDIR /app

# Install runtime dependencies for postgres client & pdf tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user. Only the storage directories are writable by it; application code stays root-owned.
RUN useradd -m -u 1000 appuser && \
    mkdir -p uploads generated_pdfs && \
    chown appuser:appuser uploads generated_pdfs

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1

# An explicit list, so a new file is left out of the image unless someone chooses to add it.
COPY alembic.ini alembic_control.ini ./
COPY alembic ./alembic
COPY alembic_control ./alembic_control
COPY app ./app
COPY scripts/create_admin.py scripts/start.sh ./scripts/

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["sh", "scripts/start.sh"]


# Tests and development seeding. Build with --target dev; never deployed.
FROM runtime AS dev

USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements-dev.txt
COPY tests ./tests
COPY tests_saas ./tests_saas
COPY pytest.ini pytest_saas.ini ./
COPY scripts ./scripts
USER appuser


# The default image: the last stage, so a plain `docker build` produces this.
FROM runtime AS production
