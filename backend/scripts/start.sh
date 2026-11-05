#!/bin/sh
# Container entrypoint: apply the migrations for this mode, then start the server.
set -e
if [ "$MULTI_TENANT_ENABLED" = "true" ]; then
    # Showroom databases are migrated per showroom through provisioning, never from here.
    alembic -c alembic_control.ini upgrade head
else
    alembic upgrade head
    python scripts/seed.py
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
