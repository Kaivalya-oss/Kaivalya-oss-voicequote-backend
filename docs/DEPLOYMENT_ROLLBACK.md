# Deployment Rollback

**`backend-backend:latest` is not a valid rollback target for the current environment, and recreating the running container from it fails.** All results below were tested on throwaway databases on 2026-11-07. No rollback was run against the live environment.

## Why

The running container `dealership_backend` uses image `backend-backend:latest` from 2026-10-05, plus code and packages copied into the container by hand. That image only knows migrations up to `8d2e3f4a5b61`, but the live database is at `a1c4e7d2f903`.

| Image | Database at | Default start | Result |
| --- | --- | --- | --- |
| `backend-backend:latest` | Empty | Migrate and start | PASS: healthy, login and quotations work, on its own older schema |
| `backend-backend:latest` | `a1c4…` (live level) | Migrate and start | **Fails:** `Can't locate revision 'a1c4e7d2f903'`, exit 255 |
| `backend-backend:latest` | `a1c4…` | Skip migrations, run `uvicorn` | Starts, but **unsafe**: runs October code, loses email, comparison and export features, and writes loan figures with default payment mode CASH |
| `backend-backend:latest` | `b2d5…` | Migrate and start | **Fails:** same error, exit 255 |
| `voicequote-backend:30d80ca` | `a1c4…` | Migrate and start | **PASS:** healthy; login, customers and quotations work |
| `voicequote-backend:30d80ca` | `b2d5…` | Migrate and start | **Fails** until the schema is downgraded |

`voicequote-backend:30d80ca` is the clean rollback target. A file-by-file comparison on 2026-11-07 found every file under `app/`, `alembic/` and `scripts/` in the running container identical to commit `30d80ca`, so removing that container loses no application code. Its Python packages do differ (the running container has older versions), so the rollback flows above are the evidence that this image works.

## Preserving the current container

Do not use `docker commit` on the running container. It would store the container's environment, including real credentials, in an image. Export source files separately instead, leaving out the environment, keys, logs and customer data. That export was done on 2026-11-07 and stored outside the git repositories.

## Before deploying `3f4fb8e` or later over the current environment

The current compose file sets `APP_ENV=production`, so the default accounts in the live database stop working as soon as the new image starts. Create a real administrator first. For local demonstrations, use `APP_ENV=development` instead.

## Before any deployment

```bash
# 1. Back up the database. Volumes are kept by every step below.
docker exec dealership_postgres pg_dump -U postgres -Fc dealership_db -f /tmp/pre-deploy.dump
docker cp dealership_postgres:/tmp/pre-deploy.dump ./pre-deploy.dump
# 2. Record what is running.
docker inspect dealership_backend --format '{{.Image}}'
docker exec dealership_postgres psql -U postgres -d dealership_db -Atc "select version_num from alembic_version"
```

The commands in this document use the current container names; adjust them for another environment. They have not been run against the live database.

## Backup and restore

Verified on 2026-11-07 on an isolated test database with seeded data, including the loss of the original database. **Back up before every live schema migration, and copy the file off the database container.**

```bash
# Back up, then copy off the container.
docker exec dealership_postgres pg_dump -U postgres -Fc dealership_db -f /tmp/pre-deploy.dump
docker cp dealership_postgres:/tmp/pre-deploy.dump ./pre-deploy.dump
# Check that the file is readable.
docker exec dealership_postgres pg_restore --list /tmp/pre-deploy.dump
```

```bash
# Restore into a NEW database, never over the only copy.
docker exec dealership_postgres psql -U postgres -c "CREATE DATABASE dealership_restored"
docker cp ./pre-deploy.dump dealership_postgres:/tmp/restore.dump
docker exec dealership_postgres pg_restore -U postgres -d dealership_restored --no-owner --no-privileges --exit-on-error /tmp/restore.dump
```

Compare row counts or checksums of the restored database, then point `DATABASE_URL` at it.

| Check | Status |
| --- | --- |
| `pg_dump` succeeds, and the copy off the container is readable | PASS |
| Restored after the original database was dropped | PASS |
| All tables and schema version restored | PASS |
| Checksums of users, customers, quotations and leads identical | PASS |
| Restored to a different server | PASS |
| Application starts and signs in on the restored database | PASS |
| Run against the live database | Not done |
| Backup of a real cloud database, or scheduled backups | BLOCKED, none configured |

The dump holds customer data and password hashes. Store it encrypted and outside any repository. Files in `generated_pdfs` and `uploads` are in separate volumes, and aren't included.

## Rolling back

**Never run** `docker compose down -v`, or `docker volume rm` on `backend_pgdata`. `-v` deletes the database.

### New image started and upgraded the schema to `b2d5…`

Tested on a test database: the downgrade removed only the new `b2d5` columns, and customers and quotations were unchanged.

```bash
docker stop dealership_backend
# Removes users.central_user_id and the typed quotation_items columns, losing any values in them.
docker run --rm --network backend_default --env-file <production env file> \
  --entrypoint alembic voicequote-backend:44550ea downgrade a1c4e7d2f903
docker rm dealership_backend
docker run -d --name dealership_backend --network backend_default --restart unless-stopped \
  --env-file <production env file> -p 8000:8000 \
  -v backend_pdf_data:/app/generated_pdfs -v backend_uploads_data:/app/uploads \
  voicequote-backend:30d80ca
curl -f http://localhost:8000/health/db
```

Images from `3f4fb8e` onwards never migrate on start. `voicequote-backend:30d80ca` still uses its older start command, which runs migrations and the seed, as the running container did. The `<production env file>` must contain everything the compose file sets, including `DATABASE_URL`. Comparison quotations created after the upgrade keep their rows, but lose their typed columns (`details_json` still holds the figures).

### New image failed before the upgrade finished

A failed upgrade rolls back on its own (tested), so the schema stays at the previous version. Start `voicequote-backend:30d80ca` as above without the downgrade.

### Data is damaged

Stop the backend, restore into a **new** database with `pg_restore`, verify it, then point `DATABASE_URL` at it. Do not restore over the only copy.

## Avoiding schema incompatibility

- Every image refuses a database newer than it knows, so old images cannot run after an upgrade without a downgrade first.
- Take a backup and record the schema version before each deployment.
- Keep the image of the previous release, tagged by commit, not `latest`.
- From `3f4fb8e` onwards a restart can't upgrade a database by surprise, because migrating is an explicit step. Such an image refuses to run on an older schema, which means a failed migration leaves the service down until you migrate or roll back.
- Multi-tenant rollback is per showroom and is not automated: each showroom's database has its own version.
