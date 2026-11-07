# Tenant Security

## Tenant resolution

The active showroom comes only from the signed access token's `tid` claim. Every request re-checks, against the database, that the account is active and that an active membership exists. Tenant IDs in headers, query strings or bodies are ignored. Membership removal and role changes apply immediately, including to tokens already issued.

Other people's records answer 404 rather than 403, so they cannot be probed. Selecting an unknown showroom and an unauthorized one give the same response.

## Controls

| Threat | Control |
| --- | --- |
| Credential exposure | Never returned by any API; `SecretStr` on input; hidden from `repr`; sent once by the setup form and then cleared |
| Credentials at rest | Secrets store with a reference held centrally. Development store uses Fernet encryption |
| Insecure production config | App refuses to start with the local store or private hosts when `APP_ENV=production` |
| SSRF to internal systems | Every resolved address must be public (private, loopback, link-local, CGNAT, metadata and IPv6-mapped forms refused); host is a bare name, not a URL; ports below 1024 refused |
| DNS rebinding | Connects to the IP that was checked, and verifies the certificate against the original host name |
| Eavesdropping | TLS 1.2+ always, with chain and host name verified. There is no unverified mode |
| Over-privileged access | Superuser accounts refused |
| Overwriting data | Databases with tables VoiceQuote did not create are refused |
| Connection exhaustion | Capped engines, pools and concurrent sessions |
| Cross-tenant sessions | A new session per request from that tenant's own engine |
| Error leakage | Driver errors become fixed messages and stable codes; only exception class names are recorded |
| Invitation abuse | Random token, stored hashed, single use, expiring. An existing account must be signed in to accept. Link token is in the URL fragment |
| Lockout | The last active Owner cannot be removed or demoted, with row locks against races |

## Assumptions and open risks

These are not solved by the current code, and should be addressed before commercial launch:

1. **Secrets manager:** no production manager is connected yet.
2. **Tokens in `localStorage`:** an XSS bug could steal them. httpOnly cookies would be safer. In multi-tenant mode, `POST /api/v1/platform/auth/logout` ends the session on the server, refresh tokens rotate, and reusing an old one ends the session. The frontend doesn't call that route yet. In single-showroom mode, logout still doesn't revoke anything.
3. **No rate limiting** on showroom sign-in or invitation acceptance. The Platform Admin's sign-in has its own limits.
4. **Password reset** doesn't work yet in multi-tenant mode, because its pages still use the legacy Supabase project.
5. **Application access:** VoiceQuote's application can read showroom data while connected. Ownership is contractual and architectural, and it does not stop an operator with production access.
6. **Application logs:** request logs record paths, which contain record IDs but no customer details. Only the quotation, email and WhatsApp paths were reviewed for customer data, not every log line in the application.
7. **Other shared services:** Gmail, WhatsApp and Gemini use global credentials, and customer data passes through them when used.
8. **Static egress IPs** depend on hosting and are not configured, so customer-side allowlisting is not available yet.
9. **No independent security review** or penetration test has been done.
