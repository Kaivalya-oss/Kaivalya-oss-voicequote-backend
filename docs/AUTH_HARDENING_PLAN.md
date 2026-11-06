# Authentication Hardening Plan

**Status: plan only. Nothing here is implemented.** All four areas are required before a public commercial launch. They should ship together, because token storage and email flows depend on each other, and a partial version would give false confidence.

## Current state

| Area | Today |
| --- | --- |
| Login attempts | No limit on either login endpoint |
| Email ownership | Never checked. Registration accepts any address and, in multi-tenant mode, signs the user in immediately |
| Password reset | Forgot and reset pages call Supabase, which no longer holds these accounts, so reset does not work in multi-tenant mode |
| Token storage | Access and refresh tokens in `localStorage` (`src/lib/api.ts`), readable by any script on the page |
| Session control | Stateless JWTs: 60-minute access, 7-day refresh. Refresh tokens are not rotated and cannot be revoked. Sign-out does not end the session on the server |

## Order of work

Sessions come first, because the other three all need somewhere to record state and a way to end sessions.

### Phase A: server-side sessions and safer token storage

1. Add a central `auth_sessions` table: id, user id, hash of the refresh token, family id, created, last used, expiry, revoked at, user agent. Only hashes are stored.
2. Rotate refresh tokens on every use. Reuse of an old token revokes the whole family and is logged as a possible theft.
3. Move the refresh token to a cookie that is `HttpOnly`, `Secure`, `SameSite=Lax` and scoped to `/api/v1/platform/auth`. Keep the access token in memory only, and shorten it to about 15 minutes.
4. Add CSRF protection to cookie-authenticated endpoints: a double-submit token, plus an `Origin` check against an explicit allow-list.
5. Make sign-out revoke the session on the server. Add "sign out everywhere", and revoke all sessions on password change.
6. Add a Content-Security-Policy, since an access token in memory is still reachable by injected script.

**Decision needed:** frontend and API must share a site for `SameSite` cookies, so the production domain layout must be known first (for example `app.` and `api.` under one domain).

### Phase B: login rate limiting

1. Count failures by normalised email and by client IP, with separate thresholds. Suggested starting values: 5 failures per account in 15 minutes, 30 per IP in 15 minutes.
2. Respond with `429` and `Retry-After`. Use temporary delays, not permanent lockouts, so that an attacker cannot lock a real owner out.
3. Apply the same limits to registration, password reset, email verification resend and invitation acceptance.
4. Use shared storage, either Redis or a Postgres table. In-process counters stop working with more than one worker.
5. **Prerequisite:** fix `get_client_ip`, which trusts `X-Forwarded-For` today. Otherwise anyone can bypass IP limits by changing a header. It should only trust the header from a configured proxy.

**Decision needed:** Redis, which is a new service, or Postgres, which is simpler and adequate at this scale. Recommendation: Postgres first.

### Phase C: email verification

1. Add `email_verified_at` to central users, and a `one_time_tokens` table holding hashed, single-use, expiring tokens with a purpose.
2. Registration creates the account as unverified and sends a link. Until verified, an owner can't connect a database or invite staff.
3. Invitations count as verification, because the link already went to that address.
4. Registration must not reveal whether an email exists. Today it returns `ACCOUNT_EXISTS`, which needs replacing with a neutral response and an email to the existing owner.
5. Existing accounts need a migration decision: mark as verified, or ask them to verify at next sign-in.

**Prerequisite:** a platform-owned sending service with SPF, DKIM and DMARC on a VoiceQuote domain. The personal Gmail account is not suitable. This is a BLOCKED external dependency.

### Phase D: working password reset

1. Add `POST /platform/auth/forgot-password` and `POST /platform/auth/reset-password`, using the Phase C token table with a short expiry of about 30 minutes.
2. The forgot endpoint always gives the same response, whether or not the account exists.
3. A successful reset revokes all sessions and notifies the user by email.
4. Rewrite both frontend pages against these endpoints, and remove the remaining Supabase auth code.
5. Reuse the published-password refusal from `app/core/dev_accounts.py`, and add a shared minimum password policy across registration, invitation and reset.

Single-showroom mode currently has no self-service reset. `scripts/create_admin.py --reset-password` is the operator fallback. Decide whether single-showroom mode needs the same flow, or remains operator-only.

## Testing required

| Area | Must prove |
| --- | --- |
| Sessions | Rotation; reuse revokes the family; sign-out takes effect on the server; a cookie can't be read from JavaScript; a cross-site request is refused |
| Rate limits | Limits hold across several workers; a forged `X-Forwarded-For` doesn't help; a locked account recovers; other accounts are unaffected |
| Verification | Unverified owners are blocked from setup; tokens are single-use and expire; responses don't reveal who has an account |
| Reset | Token only works once; expired tokens fail; all old sessions end; nothing appears in logs |
| Browser | Full login, refresh and sign-out flow in a real browser, with the production cookie settings over HTTPS |

## Dependencies and rollout

| Dependency | Needed for |
| --- | --- |
| Production domain layout and HTTPS | Secure cookies, CORS |
| Reverse proxy configuration | Trustworthy client IP |
| Transactional email provider | Verification, reset |
| Central database migration | New tables and columns, using the explicit migration procedure |

Rollout will sign everyone out once, when token storage changes. Plan it for a quiet period and announce it.

## Rough size

| Phase | Backend | Frontend |
| --- | --- | --- |
| A: sessions and storage | Large | Medium |
| B: rate limiting | Small to medium | Small |
| C: verification | Medium | Small |
| D: password reset | Small to medium | Small |

## Decisions needed before starting

1. Production domain layout.
2. Email provider.
3. Postgres or Redis for rate limits.
4. Whether existing accounts count as verified.
5. Whether single-showroom mode gets self-service reset.
