# Deployment Verification

Results for image `voicequote-backend:44550ea`, tested 2026-11-07. **PASS** means verified by an actual test, **PARTIAL** means implemented but not fully verified, and **BLOCKED** means it needs external configuration or more implementation.

This does not establish production readiness. See Deployment blockers.

## Isolation of the test harness

`backend/scripts/verify/docker-compose.verify.yml` creates throwaway databases on an internal Docker network with no published ports. All data is in tmpfs.

| Check | Result |
| --- | --- |
| Live database, live backend, Docker host and Gmail from the test network | Unreachable (each connection attempt failed) |
| Real `.env` values in test containers | None. No `env_file`; SMTP, Gemini and WhatsApp settings empty inside the app |
| Source mounted into the app | None (`Mounts` empty), so code came from the image |
| Live database afterwards | Same schema `a1c4e7d2f903`, no new rows, only its own backend connected |

## Single-showroom mode (`MULTI_TENANT_ENABLED=false`, `APP_ENV=production`)

| Test | Status |
| --- | --- |
| Start on an empty database: migrations, seed, healthy | PASS (about 33 s) |
| API checks in `verify_single.py`: auth, permissions, customers, CASH and FINANCE, comparison, history, details, PDF, leads, dashboards, export | PASS, 37 of 37 |
| WhatsApp and email with no credentials: simulated, not claimed as delivered | PASS |
| Real WhatsApp or email delivery | Not tested (no credentials used) |
| Restart: no migrations re-run | PASS |
| Graceful stop (SIGTERM reaches uvicorn as PID 1) | PASS, about 2 s, exit 0 |
| Single-showroom pytest suite inside the image | PASS, 66 of 66 |

## Multi-tenant mode

| Test | Status |
| --- | --- |
| Refuses to start in production with local secrets, or with private hosts allowed | PASS |
| Control-plane migrations and connectivity (`APP_ENV=staging`) | PASS |
| Black-box setup: registration, membership, migrations on two TLS servers, health, routing, isolation, comparison, PDF | PASS, 24 of 24 |
| TLS in use (`pg_stat_ssl`) | PASS on both servers |
| No customer data or credentials in the central database or app logs | PASS for the markers tested |
| Multi-tenant pytest suite inside the image | PASS, 80 of 80 |
| Name resolves but database hangs | PASS, with a slow first request (see Defects) |
| Database hostname stops resolving | PARTIAL (see Defects) |
| Recovery after an outage; other showroom unaffected throughout | PASS |
| Production mode with a real secrets manager | BLOCKED, none exists |
| Real cloud PostgreSQL providers | BLOCKED, none available |
| Pooling under realistic load | PARTIAL, small bursts only |

## Defects found

| Defect | Severity | Detail |
| --- | --- | --- |
| Default seeded accounts in production | **High** | `start.sh` runs `seed.py` in single-showroom production mode. It creates `admin@dealership.com` and others with passwords published in the repository, and login with them worked in `APP_ENV=production` |
| Outage error when DNS fails | Medium | The first request gets 503. Later ones get 422 `TENANT_DB_HOST_UNRESOLVED`, which reads as a user error. No details leak |
| Health check when DNS fails | Medium | Returns 422 and records no health result, because `build_engine` runs outside its error handling |
| Hanging database | Medium | First request took 32 s (the 30 s command timeout during pre-ping); the next took 5 s. Other showrooms were unaffected |
| Trusted `X-Forwarded-For` | Medium | `get_client_ip` trusts the header, so audit log IPs can be forged unless a proxy overwrites it |
| Control-plane migrations not logged | Low | `alembic_control.ini` has no logging config, so a successful run prints nothing |

## Migration verification

| Check | Result |
| --- | --- |
| Control-plane migrations separate | PASS: `alembic_control/`, own version table, own URL |
| No destructive `upgrade()` in any migration | PASS: reviewed, plus an automated test |
| Existing data kept by `a1c4 → b2d5` | PASS: checksums of customers and quotations identical before and after |
| Failed upgrade is atomic | PASS: forced a column conflict; version unchanged, no partial columns, server did not start. Retry succeeded after the conflict was cleared |
| Commands target the intended database | PASS in tests. See warning |

Two upgrades change data by design: `8d2e…` backfills customer creators and follow-up dates, and `a1c4…` backfills `payment_mode`.

**Warning:** in single-showroom mode `start.sh` migrates whatever `DATABASE_URL` points to, automatically on start. A new image pointed at an existing database upgrades it with no approval step.

### Migrating a showroom database

New showroom database, as its Owner (nothing to run by hand): Database setup screen, or
`POST /api/v1/platform/tenants/current/database/connect`.

Existing showroom database at an older version, as its Owner: **Run migrations**, or
`POST /api/v1/platform/tenants/current/database/migrate`. Take a backup first; there is no fleet-wide rollout yet.

Existing single-showroom database:

```bash
pg_dump -Fc "$LIBPQ_URL" > before-migration.dump   # backup first
docker run --rm --network <network> -e APP_ENV=production -e JWT_SECRET_KEY=<secret> \
  -e DATABASE_URL=<asyncpg url> --entrypoint alembic voicequote-backend:44550ea upgrade head
```

Control-plane database:

```bash
docker run --rm --network <network> -e MULTI_TENANT_ENABLED=true -e APP_ENV=<env> \
  -e JWT_SECRET_KEY=<secret> -e SECRETS_BACKEND=<backend> -e CONTROL_PLANE_DATABASE_URL=<asyncpg url> \
  --entrypoint alembic voicequote-backend:44550ea -c alembic_control.ini upgrade head
```

The same `alembic` commands were run against test databases only, never a live one. The `pg_dump` line and these `docker run` wrappers were not run as written. In production multi-tenant mode the app refuses `SECRETS_BACKEND=local`, so the control-plane command is blocked until a real manager exists.

## Deployment blockers

| Blocker | Status |
| --- | --- |
| Remove default seeded accounts from production startup | BLOCKED, code change |
| Secrets manager adapter and account | BLOCKED, external |
| HTTPS / reverse proxy | BLOCKED, external |
| Static outbound IP | BLOCKED, external |
| `.dockerignore`, `.gitattributes`, dependency lock | BLOCKED, code change |
| Login rate limiting, email verification, password reset in multi-tenant mode | BLOCKED, code change |
| Per-showroom Gmail and WhatsApp | BLOCKED, later phase |
| Production host, central database and its backups, monitoring, log shipping | BLOCKED, not configured |
| Security review | BLOCKED, not done |

## Reproducing the tests

```bash
docker run --rm -u 0 --network none -v <certs>:/out --entrypoint python voicequote-backend:44550ea \
  scripts/gen_saas_test_certs.py /out
CERT_DIR=<certs> docker compose -p vqv -f scripts/verify/docker-compose.verify.yml up -d
docker run -d --name vqv-single --network vqv_net -e APP_ENV=production -e DEBUG=False \
  -e MULTI_TENANT_ENABLED=false -e JWT_SECRET_KEY=<random> \
  -e DATABASE_URL=postgresql+asyncpg://postgres:vqv-test-only@postgres:5432/vqv_single \
  voicequote-backend:44550ea
docker run --rm -i --network vqv_net -e BASE=http://vqv-single:8000 --entrypoint python \
  voicequote-backend:44550ea - < scripts/verify/verify_single.py
```

See `scripts/verify/verify_multitenant.py` for the multi-tenant phases. Its `down` phase checks the corrected behaviour, so against this image two checks fail until the DNS defects above are fixed: the health check response and the repeat request. Tear down with `docker compose -p vqv -f scripts/verify/docker-compose.verify.yml down -v`.
