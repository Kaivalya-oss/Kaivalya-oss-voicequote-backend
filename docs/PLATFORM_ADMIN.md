# Platform Admin

VoiceQuote has exactly one Platform Admin account across the whole platform, who manages platform metadata across all showrooms and has no access to any showroom's business data. There is no second Platform Admin, no Admin A or Admin B, no two-admin approval and no two-person authorization. This document covers what exists now: identity, sign-in, the boundary, onboarding and suspension. Screens, plans and payments come in later phases.

## Boundary

| Layer | Showroom | Platform Admin |
| --- | --- | --- |
| Accounts | `users` | `platform_admins`, which has no link to memberships |
| Signing key | `JWT_SECRET_KEY` | `ADMIN_JWT_SECRET_KEY`. Neither process holds the other's |
| Token audience | `voicequote-showroom` | `voicequote-admin`, plus an issuer check and a token type |
| Routes | `/api/v1/platform` (showroom accounts, despite the name) and business routes, in `app.main` | `/api/v1/admin` in `app.platform_admin.main` only |
| Process | `uvicorn app.main:app` | `uvicorn app.platform_admin.main:app`, same image |
| Database | Migration owner on the central database, and each showroom's database | A least-privilege login role on the central database only |

**The admin process has no route to a showroom database.** Its code doesn't import the tenant connection manager, secrets store or business models, and a test fails the build if that changes. Its database role can't read `tenant_connections.secret_reference`, `users.password_hash` or `tenant_memberships.invitation_token_hash`.

**At startup, the admin process refuses to run if:**
- It holds `JWT_SECRET_KEY`, `DATABASE_URL`, `TEST_DATABASE_URL`, `CONTROL_PLANE_DATABASE_URL`, `SECRETS_MASTER_KEY`, `SECRETS_LOCAL_DIR`, `SECRETS_BACKEND`, `SMTP_PASSWORD`, `WHATSAPP_ACCESS_TOKEN` or `GEMINI_API_KEY`.
- Its database role has any privilege it must not have, such as superuser or reading tenant secret references.
- The schema doesn't match the release.

**A limit of this design:** anyone with access to the server, the secrets manager or the database owner account can go around these controls. Production needs an infrastructure access policy as well.

## Only one admin

The database refuses a second row: `platform_admins.singleton` has one permitted value and a unique constraint. The admin process's role also has no `INSERT` or `DELETE` on that table, and no route creates an admin.

## Deployment steps

Run these in order, with `<...>` values kept outside the repository. Equivalent commands ran on isolated test databases, using `-e` values and `CREATE ROLE ... PASSWORD`. The `--env-file`, `-it` and `\password` forms shown here haven't been run. None has been run against a live database.

**1. Migrate the central database,** as the migration owner. This creates the `voicequote_platform_admin` group role, which can't log in:

```text
docker run --rm --network <network> --env-file <main env> --entrypoint alembic <image> -c alembic_control.ini upgrade head
```

The migration owner needs `CREATEROLE`. If your database provider doesn't allow that, create the group role first, as its administrator.

**2. Create the login role** in `psql` on the central database, entering the password at the prompt so it isn't stored in history:

```text
CREATE ROLE <login role> LOGIN IN ROLE voicequote_platform_admin;
\password <login role>
```

**3. Bootstrap the one admin.** Put `ADMIN_BOOTSTRAP_DATABASE_URL` (the migration owner's URL), `ADMIN_EMAIL` and `ADMIN_FULL_NAME` in a protected file. Add `-it` to be prompted for the password, or include `ADMIN_PASSWORD` in that file and delete it afterwards:

```text
docker run --rm -it --network <network> --env-file <bootstrap file> --entrypoint python <image> scripts/bootstrap_platform_admin.py
```

It refuses to run if an admin already exists. Passwords need at least 14 characters, and published ones are refused.

**4. Generate the admin process's own secrets,** and keep them apart from the showroom process's:

```text
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

```text
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**5. Start the process** with only the variables below:

```text
docker run -d --network <network> --env-file <admin env> --entrypoint uvicorn <image> app.platform_admin.main:app --host 0.0.0.0 --port 8000
```

**6. Sign in straight away and set up MFA.** Until that's done, the account's password alone can start enrolment, so don't leave a gap between steps 3 and 6. Store the 10 recovery codes offline, because they're shown once.

## Settings

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `ADMIN_DATABASE_URL` | Yes | None | `postgresql+asyncpg://`, as the login role from step 2 |
| `ADMIN_JWT_SECRET_KEY` | Yes | None | Secret. At least 32 characters |
| `ADMIN_MFA_ENCRYPTION_KEY` | Yes | None | Secret. Fernet key. **Losing it makes the enrolled MFA secret unreadable** |
| `ADMIN_ENV` | | `production` | `development` or `test` allow insecure cookies |
| `ADMIN_ALLOWED_ORIGINS` | For browsers | Empty | Comma separated. Empty means no browser can refresh a session |
| `ADMIN_TRUSTED_PROXY_IPS` | Behind a proxy | Empty | Comma separated. `X-Forwarded-For` is ignored unless the connection comes from one of these |
| `ADMIN_COOKIE_SECURE` | | `true` | Can't be disabled outside `development` or `test` |
| `ADMIN_ACCESS_TOKEN_MINUTES` | | `10` | |
| `ADMIN_MFA_TOKEN_MINUTES` | | `5` | Time allowed between password and code |
| `ADMIN_SESSION_IDLE_MINUTES` | | `30` | |
| `ADMIN_SESSION_ABSOLUTE_HOURS` | | `12` | |
| `ADMIN_THROTTLE_WINDOW_MINUTES` | | `15` | |
| `ADMIN_MAX_FAILURES_PER_IP` | | `5` | |
| `ADMIN_MAX_FAILURES_PER_ACCOUNT` | | `20` | |
| `ADMIN_MAX_MFA_FAILURES` | | `5` | |
| `ADMIN_SMTP_HOST`, `ADMIN_SMTP_FROM`, `ADMIN_OWNER_INVITE_URL` | To create showrooms | Empty | Without all three, creating a showroom returns 503 |
| `ADMIN_SMTP_PORT`, `ADMIN_SMTP_STARTTLS` | | `587`, `true` | STARTTLS can't be disabled in production |
| `ADMIN_SMTP_USER`, `ADMIN_SMTP_PASSWORD` | | Empty | Password is a secret, and separate from the showroom app's |
| `ADMIN_INVITATION_EXPIRE_HOURS` | | `72` | |

`ADMIN_BOOTSTRAP_DATABASE_URL`, `ADMIN_EMAIL`, `ADMIN_FULL_NAME` and `ADMIN_PASSWORD` are read only by the bootstrap script, and must not be set on the running process.

## API

All paths start with `/api/v1/admin`. Only those marked as public work without a full session.

| Method and path | Access | Purpose |
| --- | --- | --- |
| `POST /auth/login` | Public | Password step. Returns a short-lived MFA token, never a session |
| `POST /auth/mfa/enroll/start` | MFA enrolment token | Creates the TOTP secret. Only before enrolment |
| `POST /auth/mfa/enroll/confirm` | MFA enrolment token | Confirms a code, returns recovery codes once, opens a session |
| `POST /auth/mfa/verify` | MFA challenge token | Code or recovery code, opens a session |
| `POST /auth/refresh` | Refresh cookie and allowed origin | Rotates the refresh token |
| `POST /auth/logout`, `/auth/logout-all` | Session | Ends this session, or all of them |
| `GET /auth/sessions` | Session | Active sessions, without token hashes |
| `POST /auth/sessions/{id}/revoke` | Session | Ends one session |
| `GET /me` | Session | Own profile |
| `POST /showrooms` | Session | Create a showroom and invite its primary Owner |
| `GET /showrooms/{id}` | Session | Showroom metadata, from an explicit allow-list |
| `POST /showrooms/{id}/owner-invitation` | Session | Resend or replace, only before onboarding completes |
| `POST /showrooms/{id}/suspend`, `/reactivate` | Session | With a required reason |

`GET /health` on the process root reports only that it's running.

## Sessions and MFA

- Access tokens last 10 minutes. Every request also checks the session and account in the database, so revocation is immediate.
- The refresh token lives only in an `HttpOnly`, `Secure`, `SameSite=Strict` cookie scoped to `/api/v1/admin/auth`, and only its SHA-256 hash is stored.
- Each refresh replaces the token. Presenting a replaced token ends the session, because a copy must exist.
- TOTP secrets are encrypted at rest. A code can't be used twice. Recovery codes are single-use and stored as hashes.
- Failure counters live in the database, so they hold across restarts and processes.

## Audit

`audit_events` is append-only. Database triggers refuse update, delete and truncate for every role, including the table owner, and the admin role also has no privilege to attempt them. Only a migration can remove the triggers. Events never hold passwords, tokens, MFA secrets, recovery codes or database credentials.

## Onboarding

1. The Platform Admin enters the showroom's details and the primary Owner's contact details.
2. The API creates the central records in one transaction, and emails a single-use invitation that expires after `ADMIN_INVITATION_EXPIRE_HOURS`.
3. The Owner accepts and sets their own password. An existing account must sign in to accept, so a leaked link can't take it over.
4. The Owner connects their PostgreSQL database, which is verified and migrated.
5. The showroom becomes `ACTIVE` only after that succeeds.

**The Platform Admin never sees the token.** It's in no API response, audit event or log, and the admin's database role can write its hash but never read it. If email isn't configured, the API returns 503 and creates nothing. If sending fails, the records stay and the invitation can be sent again.

`onboarding_stage` moves through `CREATED`, `INVITED`, `DATABASE_SETUP` and `COMPLETE`, separately from the access `status`. There's no separate "complete setup" step yet: connecting a verified database completes onboarding. Plans aren't built yet either, so subscriptions start as `unassigned`, at 0.00.

## Suspension

Suspension takes effect on each showroom user's next request, because every request re-reads the status. It deletes nothing.

| While suspended | Allowed |
| --- | --- |
| All business routes, for every role | No |
| Inviting, role changes, deactivating, database changes | No, Owners included |
| Viewing status | Yes. The reason isn't shown, because it's the Platform Admin's note |
| An Owner disconnecting VoiceQuote from their database | Yes |

**Disconnecting removes VoiceQuote's stored connection reference and credentials, and closes its connections. It doesn't change the password on the customer's database server,** and the response says so.

Reactivation returns a showroom to `ACTIVE` only if onboarding is complete and its connection is still active. Otherwise it goes to `PENDING_SETUP`.

## Owner recovery

Once onboarding is complete, the API refuses to invite or replace an Owner, with `OWNER_RECOVERY_REQUIRED`. This stops an administrator from quietly placing an account they control into an active showroom.

With one Platform Admin, there's nobody else to approve a recovery, so these controls take that place. **This procedure is designed and documented, but its workflow isn't built yet:** recovery is simply refused today.

1. The Platform Admin initiates and authorizes the recovery.
2. The showroom's identity is verified independently, against its previously registered business details.
3. Existing Owners are notified where possible, and can object.
4. A 72-hour delay applies where possible.
5. If existing Owners can't be reached or can't approve, stronger business identity verification is required.
6. The evidence, decision, notification and execution are all recorded in the audit log.

## Known limits

| Limit | Detail |
| --- | --- |
| Lockout can be abused | Repeated failures from many addresses can temporarily lock out the only admin, by design of a shared account limit. Restricting the admin origin at the network level removes this |
| Enrolment window | Between bootstrap and first sign-in, the password alone can start MFA enrolment |
| Lost MFA with no codes | The server-side recovery procedure arrives with Settings |
| Single point of control | With one admin, no second person checks their actions. The audit trail and Owner notifications are the compensating controls |
| Password change | Arrives with Settings. The admin role can't change the password yet |
| Rotating `ADMIN_JWT_SECRET_KEY` | Signs the admin out, which is harmless. Rotating `ADMIN_MFA_ENCRYPTION_KEY` isn't supported yet |
