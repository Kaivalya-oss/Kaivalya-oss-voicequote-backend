# Deployment Setup

## Single-showroom mode (default)

Nothing changes. Leave `MULTI_TENANT_ENABLED` unset. Run `alembic upgrade head`; it adds additive columns (`users.central_user_id` and typed `quotation_items` columns).

## Multi-tenant mode

### Backend environment

| Variable | Value |
| --- | --- |
| `MULTI_TENANT_ENABLED` | `true` |
| `CONTROL_PLANE_DATABASE_URL` | Central PostgreSQL, e.g. `postgresql+asyncpg://…/voicequote_control` |
| `JWT_SECRET_KEY` | 32+ random characters |
| `FRONTEND_URL` | The frontend's origin |
| `SECRETS_BACKEND` | A real manager in production (see below). `local` is development only |
| `SECRETS_MASTER_KEY`, `SECRETS_LOCAL_DIR` | Local store only. Generate the key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `TENANT_DB_ALLOW_PRIVATE_HOSTS` | `true` for local testing only; production refuses it |
| `TENANT_DB_*` | Optional pool and limit overrides; see `app/core/config.py` |

`scripts/start.sh` runs the correct migrations for the mode, then starts the server.

### Frontend environment

| Variable | Value |
| --- | --- |
| `VITE_MULTI_TENANT` | `true` |
| `VITE_API_BASE_URL` | Backend URL, over HTTPS in production |

`.env.saas` holds the local values: `npm run dev -- --mode saas`.

### Still required before production

1. **Secrets manager:** implement `SecretStore` for the chosen cloud and register it in `get_secret_store()`. Not built yet.
2. **HTTPS** in front of the API and frontend. Database passwords travel from the browser through this path once.
3. **Static egress IP**, so showrooms can allowlist VoiceQuote.
4. **Pricing:** set `SAAS_DEFAULT_PLAN_AMOUNT`; it defaults to 0.
5. **Rebuild the image:** `docker compose build`. It must include `cryptography`.
6. **Pool sizing** against your worker count.

## Showroom requirements

- PostgreSQL 13 or newer, reachable from VoiceQuote, with TLS and a certificate valid for its host name.
- An empty database, and a user that is not a superuser and can create tables in `public`. Example:

```sql
CREATE ROLE voicequote LOGIN PASSWORD '<strong password>' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE voicequote_showroom;
\c voicequote_showroom
GRANT ALL ON SCHEMA public TO voicequote;
```

- If the provider's certificate isn't publicly trusted (AWS RDS, for example), paste its CA certificate during setup.
- Transaction-mode poolers are supported at runtime, since prepared statement caching is off. Use a direct connection for first setup, because migrations hold an advisory lock.

These provider notes are from general knowledge and have not been verified against live Supabase, Neon, RDS or Azure accounts.

## Running the tests

Multi-tenant tests use two disposable TLS servers:

```bash
python scripts/gen_saas_test_certs.py .saas-test-certs
docker compose -f docker-compose.saas-test.yml up -d
```

From a container on the `backend_default` network, with `DATABASE_URL` pointing at a server where a `_test` control database can be created, and `SAAS_TEST_CERT_DIR` set if the certificates are elsewhere:

```bash
python -m pytest -c pytest_saas.ini
```

Single-showroom tests stay as they were: `python -m pytest`, with `TEST_DATABASE_URL` on a dedicated database.

## Failure handling

| Symptom | Meaning | Action |
| --- | --- | --- |
| 403 `TENANT_NOT_OPERATIONAL` | No verified database | Owner completes setup |
| 503 `TENANT_DB_UNAVAILABLE` | Database down or credentials changed | Owner checks health, rotates the password if it changed |
| 503 `TENANT_DB_BUSY` | Process session limit reached | Raise limits or add capacity |
| `TENANT_DB_MIGRATION_FAILED` | Rolled back completely | Retry; the central run log has the exception class |
| `TENANT_DB_SCHEMA_NEWER` | Older application than the database | Deploy the newer release |
| Stuck `PROVISIONING` | Worker crashed during setup | Retry after 10 minutes |
