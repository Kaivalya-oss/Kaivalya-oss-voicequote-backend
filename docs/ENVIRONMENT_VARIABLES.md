# Environment Variables

The source of truth is `backend/app/core/config.py`. No value here is a production recommendation unless stated. **Secret** values belong in a secrets manager or a protected environment, never in git or the image.

## Core

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `APP_ENV` | `development` | Yes in production | | `production` turns on the startup safety checks |
| `DEBUG` | `True` | Set `False` in production | | Controls log level. Default is unsafe for production |
| `APP_NAME`, `HOST`, `PORT` | see config | | | `start.sh` always binds `0.0.0.0:8000`, ignoring these two |
| `DATABASE_URL` | local dev URL | Yes, single-showroom | Yes | asyncpg URL. `start.sh` migrates this database on start |
| `TEST_DATABASE_URL` | local dev URL | Tests only | Yes | Must differ from `DATABASE_URL`, or tests refuse to run. `backend/docker-compose.yml` wrongly sets them equal |
| `JWT_SECRET_KEY` | empty | Yes in production | Yes | 32+ characters and not a published value, or the app refuses to start |
| `JWT_ALGORITHM` | `HS256` | | | |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `60`, `7` | | | |
| `FRONTEND_URL` | localhost origins | Yes | | CORS allow-list, comma separated |
| `UPLOAD_DIR`, `PDF_STORAGE_DIR` | `uploads`, `generated_pdfs` | | | Single-showroom mode needs a persistent volume at the PDF path |

## Multi-tenant

| Variable | Default | Required | Secret | Notes |
| --- | --- | --- | --- | --- |
| `MULTI_TENANT_ENABLED` | `false` | | | Currently unset on the live environment |
| `CONTROL_PLANE_DATABASE_URL` | empty | Yes | Yes | Central database |
| `SECRETS_BACKEND` | `local` | Yes | | Production refuses `local`. **No other backend is implemented, so production multi-tenant cannot start yet** |
| `SECRETS_LOCAL_DIR`, `SECRETS_MASTER_KEY` | `.secrets`, empty | Local store only | Key: yes | Development only. The store is lost with the container unless the directory persists |
| `TENANT_DB_ALLOW_PRIVATE_HOSTS` | `false` | | | Production refuses `true` |
| `INVITATION_EXPIRE_HOURS` | `72` | | | |
| `SAAS_DEFAULT_PLAN_ID`, `SAAS_DEFAULT_PLAN_AMOUNT`, `SAAS_DEFAULT_CURRENCY` | `standard-monthly`, `0.00`, `INR` | Pricing not decided | | |
| `TENANT_DB_POOL_SIZE`, `TENANT_DB_MAX_OVERFLOW` | `2`, `2` | | | Per showroom, per worker |
| `TENANT_DB_POOL_TIMEOUT`, `TENANT_DB_POOL_RECYCLE` | `10`, `300` | | | Seconds |
| `TENANT_DB_CONNECT_TIMEOUT`, `TENANT_DB_COMMAND_TIMEOUT` | `5`, `30` | | | Seconds |
| `TENANT_DB_ENGINE_TTL`, `TENANT_DB_IDLE_SECONDS` | `900`, `600` | | | Seconds |
| `TENANT_DB_MAX_ENGINES`, `TENANT_DB_MAX_CONCURRENT_SESSIONS`, `TENANT_DB_SCHEDULER_CONCURRENCY` | `50`, `100`, `5` | | | Per worker |

## External services

| Variable | Secret | When empty |
| --- | --- | --- |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM_NAME` | | Defaults to Gmail |
| `SMTP_USER`, `SMTP_PASSWORD` | Yes | Email is simulated, and nothing is sent |
| `ADMIN_NOTIFY_EMAIL` | | Falls back to `SMTP_USER` |
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | Token: yes | WhatsApp is simulated |
| `WHATSAPP_API_VERSION` | | `v18.0` |
| `GEMINI_API_KEY` | Yes | AI uses the rule-based fallback |

## Frontend (build time, visible to browsers, so never secrets)

| Variable | Notes |
| --- | --- |
| `VITE_API_BASE_URL` | Backend URL, HTTPS in production |
| `VITE_MULTI_TENANT` | `true` turns on showroom screens |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` | Legacy. Still imported by the password-reset pages, `src/services/repository.ts` and `src/lib/pdf.ts` |

## Operations

| Area | Current state |
| --- | --- |
| Health checks | `GET /health` (image `HEALTHCHECK`, 30 s) and `GET /health/db`. The 5 s start period is shorter than first start (about 33 s), but the container did become healthy |
| Workers | One uvicorn process, not configured. **More workers would run scheduler jobs in each**, and multiply connection limits |
| Logging | Plain text to stdout, no shipping or retention configured |
| Shutdown | SIGTERM handled: scheduler stops, pools close, exit 0 (tested) |
| HTTPS | Not provided by the application. A reverse proxy is required, and uvicorn runs without `--proxy-headers` |
| Static outbound IP | Not configured. Showrooms need it to allowlist VoiceQuote |
| Database requirements | PostgreSQL 13+; tested on 16 only |
