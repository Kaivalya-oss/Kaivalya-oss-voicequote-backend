# Showroom Database Migrations

Each showroom owns its database, so a new release can leave showrooms on an older schema until they're migrated. Until then, staff get a clear temporary error instead of failures from missing columns. No showroom is ever migrated automatically.

## What people see

| State | Staff requests | Owner's `setup-status` |
| --- | --- | --- |
| Schema current | Work | `migration_required: false` |
| Older schema | 503 `TENANT_DB_SCHEMA_OUTDATED` | `migration_required: true`, `next_step: RUN_MIGRATION` |
| Migration running | 503 `TENANT_DB_MAINTENANCE` | `next_step: WAIT_FOR_MAINTENANCE` |
| Migration failed | Still 503 `TENANT_DB_SCHEMA_OUTDATED` | `last_error_code` is set, and it can be retried |

The schema check runs once for each database engine, which is rebuilt after a restart, so a deployment always rechecks. The stored `schema_version` is refreshed by a migration or by the Owner's health check.

## Who can migrate

| Who | How | Scope |
| --- | --- | --- |
| Operator with server access | `scripts/migrate_showrooms.py` in the main process's environment | One showroom, or all that need it |
| The showroom's Owner | `POST /api/v1/platform/tenants/current/database/migrate` | Their own showroom |
| Platform Admin | **Not possible.** Their process can't read tenant credentials | None |

## The command

Report only, which changes nothing:

```text
python scripts/migrate_showrooms.py --plan
```

One showroom:

```text
python scripts/migrate_showrooms.py --tenant <showroom id> --execute
```

Every showroom that needs it, one at a time:

```text
python scripts/migrate_showrooms.py --all --execute
```

Without `--execute`, it only reports. Exit code 1 means at least one showroom failed.

## Procedure for a release with a showroom schema change

1. Tell Owners in advance, and ask them to back up their databases. VoiceQuote can't back up a database it doesn't own.
2. Migrate the central database if needed, and deploy the new image.
3. Run `--plan`, and check the list.
4. Run one showroom first, and check it.
5. Run `--all --execute`.
6. Rerun for any failures, once their cause is fixed.

## Failure and recovery

- Each showroom's upgrade runs in one transaction, so a failure rolls that database back to its previous schema.
- A failure is recorded in `tenant_migration_runs`, `last_error_code` and the audit log, and the next showroom still runs.
- Rerunning is safe, because a showroom already at the latest version has nothing to apply.
- If a process crashes mid-migration, the showroom shows maintenance until the 10-minute stale limit passes. After that, it can be migrated again.
- Downgrades aren't offered by these tools. Recovering a showroom database from a bad upgrade means restoring the Owner's own backup.

## Isolation

Each showroom is migrated through its own connection and credentials. Nothing here reads one showroom's data while working on another.
