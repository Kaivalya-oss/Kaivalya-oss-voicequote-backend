# Deployment Verification

Results for image `voicequote-backend:3f4fb8e`, tested 2026-11-07 on isolated test databases only. **PASS** means verified by an actual test, **PARTIAL** means implemented but not fully verified, and **BLOCKED** means it needs external configuration or more implementation.

This does not establish production readiness. See Deployment blockers.

Reproduce with:

```bash
docker run --rm -u 0 --network none -v <certs>:/out --entrypoint python voicequote-backend:3f4fb8e-dev scripts/gen_saas_test_certs.py /out
```

```bash
CERT_DIR=<certs> docker compose -p vqv -f scripts/verify/docker-compose.verify.yml up -d --wait
```

```bash
IMAGE=voicequote-backend:3f4fb8e DEV_IMAGE=voicequote-backend:3f4fb8e-dev ROLLBACK_IMAGE=voicequote-backend:30d80ca CERT_DIR=<certs> scripts/verify/verify_image.sh all
```

```bash
docker compose -p vqv -f scripts/verify/docker-compose.verify.yml down -v
```

## Deploying a release

Starting a container never changes a database. A deployment has these separate steps, in order:

1. **Back up** every database being migrated (`DEPLOYMENT_ROLLBACK.md`).
2. **Migrate the central database** (multi-tenant) or the showroom database (single-showroom).
3. **Create the first account**, if this is a new installation.
4. **Start the new image.** It refuses to start if the schema doesn't match, and names the command to run.
5. **Migrate each existing showroom database** (multi-tenant).

### Migrating the central database

```bash
docker run --rm --network <network> --env-file <env file> --entrypoint alembic voicequote-backend:3f4fb8e -c alembic_control.ini upgrade head
```

### Migrating a single-showroom database

```bash
docker run --rm --network <network> --env-file <env file> --entrypoint alembic voicequote-backend:3f4fb8e upgrade head
```

### Migrating showroom databases (multi-tenant)

- **New showroom:** the Owner connects the database in the setup screen (`POST /api/v1/platform/tenants/current/database/connect`). It is migrated and verified as part of connecting.
- **Existing showroom:** the Owner presses **Run migrations** (`POST /api/v1/platform/tenants/current/database/migrate`). Each is separate and recorded in `tenant_migration_runs`.

**There is no operator command to migrate all showrooms.** See Defects.

## Creating the first account

No account is ever created automatically, and there is no default password.

### Single-showroom: an operator runs `create_admin.py`

Put the values in a protected file that is deleted afterwards, so the password isn't left in shell history or in `docker inspect`:

```bash
docker run --rm --network <network> --env-file <env file> --env-file <admin file> --entrypoint python voicequote-backend:3f4fb8e scripts/create_admin.py
```

The admin file holds `ADMIN_EMAIL`, `ADMIN_FULL_NAME` and `ADMIN_PASSWORD`. Or leave `ADMIN_PASSWORD` out and add `-it` to be prompted. The script:

- Creates the staff roles if they are missing, and an active `ADMIN`.
- Refuses published emails and passwords in every environment, and passwords under 12 characters.
- Refuses to overwrite an existing account without `--reset-password`.

This account is an `ADMIN`, not a `DEALER_OWNER`. An administrator assigns other roles with `PUT /api/v1/users/{id}/role`, after the person registers and is approved.

### Multi-tenant: the Owner registers

The Owner registers at `POST /api/v1/platform/showrooms/register` with their own password, which creates the showroom and their `OWNER` membership. Staff join by invitation. **PARTIAL:** nothing verifies email ownership yet, so someone can register with an address they don't own. See `AUTH_HARDENING_PLAN.md`.

## Results

### Isolation

| Check | Status |
| --- | --- |
| Internal Docker network with no published ports, data in tmpfs | PASS |
| No real `.env` values in test containers. Test passwords no longer reuse the live password | PASS |
| Live containers afterwards: same start time, 0 restarts, schema `a1c4e7d2f903` | PASS |

### Production startup safety

| Check | Status |
| --- | --- |
| Refuses to start on an unmigrated database, and creates no tables | PASS |
| Migrating creates no accounts | PASS |
| Starting and restarting creates no accounts or roles | PASS |
| `SEED_DEV_DATA=true` outside development: server refuses to start | PASS |
| Seed script absent from the production image | PASS |
| Seed refused in the test image for `production`, `staging`, `prod` | PASS |
| All 5 default accounts refused on an empty database | PASS |
| All 5 default accounts refused on a database that already holds them | PASS |
| Valid token for a default account refused | PASS |
| All 5 refused on multi-tenant platform login | PASS |
| `create_admin.py` refuses published and weak values, then creates a working administrator | PASS |
| Password never reaches the logs | PASS |

### Application

| Test | Status |
| --- | --- |
| Single-showroom black-box API, `APP_ENV=production`, no seeded data | PASS, 54 of 54 |
| Single-showroom pytest in the test image | PASS, 107 passed, 21 skipped because build files aren't in the image |
| Those build-file tests, run from the repository | PASS, 19 passed, 1 skipped because the image has no git. That one was checked with `git check-attr` on the host |
| Multi-tenant black-box setup and isolation | PASS, 24 of 24 |
| Multi-tenant pytest in the test image | PASS, 98 of 98 |
| No customer data or credentials in the central database or logs | PASS for the markers tested |
| Real WhatsApp or email delivery | Not tested (no credentials used) |
| Production multi-tenant with a real secrets manager | BLOCKED, none exists |
| Real cloud PostgreSQL providers | BLOCKED, none available |
| Pooling under realistic load | PARTIAL, small bursts only |

### Database outage and recovery

Real outages on real TLS servers, not mocks.

| Scenario | Result | Status |
| --- | --- | --- |
| Hostname stops resolving | Every request 503 `TENANT_DB_UNAVAILABLE` in about 0.01 s, previously 422 from the second request | PASS |
| Health check, hostname not resolving | 200 with `healthy: false`, `TENANT_DB_HOST_UNRESOLVED`, stored centrally | PASS |
| Server hangs (paused) | 503 in 7.0 s, then 5.0 s, previously 32 s | PASS |
| Health check, server hangs | 200 with `healthy: false` in 5.0 s | PASS |
| Server stopped | Clear 503, no details leaked | PASS |
| Reconnect or resume | Requests served again with data intact, health clears the stored error | PASS |
| Other showroom during every outage | 200 in about 0.02 s | PASS |
| Outages cause no unhandled errors | 0 tracebacks | PASS |

A query already running when a server hangs can still wait for the 30 s command timeout. That was not changed.

### Migrations

| Check | Status |
| --- | --- |
| Control-plane migration is explicit, logs progress, uses its own version table | PASS |
| Central database holds no business tables, and showrooms hold no control version table | PASS |
| New showroom migrated when connected | PASS |
| Existing showroom on an older schema: app restart doesn't migrate it | PASS |
| Existing showroom migrated by its Owner: data kept, recorded centrally | PASS |
| Migrating one showroom leaves the other showroom and the central version unchanged | PASS |
| New image on an older single-showroom database: refuses, changes nothing | PASS |
| Failed upgrade (forced column conflict): stays at old version, no partial columns | PASS |
| Successful upgrade and downgrade: table checksums identical | PASS |
| Upgrades contain no drops (automated test) | PASS |

`8d2e…` and `a1c4…` change data by design, backfilling customer creators, follow-up dates and `payment_mode`.

## Defects

| Defect | Severity | Detail |
| --- | --- | --- |
| No way to migrate all showrooms | **High** | Only each Owner can migrate their own showroom. A release with a schema change depends on every Owner acting |
| Older showroom gives a generic 500 | **High** | Until migrated, staff get `INTERNAL_SERVER_ERROR` on requests such as the customer list (`column users.central_user_id does not exist`). No details leak, and health reports `TENANT_DB_VERIFICATION_FAILED`, but staff get no useful message |
| `X-Forwarded-For` trusted | Medium | Audit log IPs can be forged. Also blocks reliable rate limiting |
| `JWT_SECRET_KEY` check only for exact `production` | Medium | `APP_ENV=prod` or `staging` with no secret falls back to the published development secret |
| First hung request takes 7 s, not 5 s | Low | About 2 s beyond the connect timeout. Cause not investigated |

## Deployment blockers

| Blocker | Status |
| --- | --- |
| Migrating all showrooms; clear error for an older schema | BLOCKED, code change |
| Login rate limiting, email verification, password reset, safer tokens (`AUTH_HARDENING_PLAN.md`) | BLOCKED, code change |
| Secrets manager adapter and account | BLOCKED, external |
| HTTPS and reverse proxy, including client IP handling | BLOCKED, external |
| Static outbound IP | BLOCKED, external |
| Production host, central database and its backups, monitoring, log shipping | BLOCKED, not configured |
| Per-showroom Gmail and WhatsApp | BLOCKED, later phase |
| OS package scanning, pinned `apt` versions | BLOCKED, not done |
| Load testing, security review | BLOCKED, not done |
