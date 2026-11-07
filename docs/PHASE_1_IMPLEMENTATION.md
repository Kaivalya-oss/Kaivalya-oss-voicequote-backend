# Phase 1: Multi-Tenant Control Plane

## Control-plane schema

| Table | Holds |
| --- | --- |
| `tenants` | Showroom name, slug, status, subscription status |
| `users` | Email, name, bcrypt hash, account status (no business data) |
| `tenant_memberships` | User, showroom, role, status, inviter, hashed invitation token |
| `tenant_connections` | Provider, secret reference, status, schema version, health metadata |
| `subscriptions`, `subscription_events` | Plan, amount, status, lifecycle events |
| `tenant_migration_runs` | Migration history for showroom databases: versions, status, error code |

Identity stays with VoiceQuote's existing JWT and bcrypt. `auth_provider_user_id` is reserved for an external provider.

## Registration

One transaction creates the user, tenant, Owner membership, pending subscription, event and pending connection. Unique constraints on slug and email mean repeated or concurrent requests create one showroom; the rest get 409. The showroom stays `PENDING_SETUP` until its database is verified.

## Roles

| Role | Today's access |
| --- | --- |
| OWNER | Everything, including team and database management |
| HEAD_OF_SALES | Management, finance and inventory screens, all records, read-only team list |
| SALESPERSON | Own customers, leads and quotations |

Former roles map as: Admin and Dealer Owner to OWNER; Sales Executive to SALESPERSON; Finance and Inventory to HEAD_OF_SALES. This gives former Finance and Inventory staff broader access than before, until the detailed permissions of a later phase.

## API (`/api/v1/platform`)

The two showroom-creation routes below were removed later. Showrooms are now created only by the Platform Admin: see `PLATFORM_ADMIN.md`.

| Method and path | Purpose | Who |
| --- | --- | --- |
| `POST /showrooms/register` | Register owner and showroom | Public |
| `POST /showrooms` | Add a showroom to an existing account | Signed in |
| `POST /auth/login`, `POST /auth/refresh` | Sessions | Public |
| `POST /auth/logout` | Ends this sign-in on the server. Added later | Signed in |
| `GET /me`, `GET /memberships` | Current user and showrooms | Signed in |
| `POST /tenants/select` | Switch showroom; returns a new token | Signed in, must be a member |
| `GET /tenants/current` | Current showroom | Member |
| `GET /tenants/current/setup-status` | Setup state (connection details for Owners only) | Member |
| `GET /tenants/current/members` | Team | Owner, Head of Sales |
| `POST /tenants/current/invitations` | Invite | Owner |
| `POST /invitations/accept` | Accept | Invitation token |
| `POST /tenants/current/members/{id}/deactivate` | Deactivate | Owner |
| `PUT /tenants/current/members/{id}/role` | Change role | Owner |

There is no email integration in this phase, so the Owner receives a one-time invitation link to pass on.

## Screens

`/showroom/register`, `/showroom/setup`, `/showroom/select`, `/showroom/invite`, `/team`. A gate on the private layout sends users without a selected showroom to selection, and non-operational showrooms to setup. The backend enforces the same rules independently.

## Acceptance criteria

Evidence: `tests_saas/test_phase1_control_plane.py`, and browser runs of registration, invitation, role-limited navigation, sign-in, and switching.

| # | Criterion | Status |
| --- | --- | --- |
| 1 | Two independent showrooms can register | Demonstrated |
| 2 | Each has its own tenant ID | Demonstrated |
| 3 | An Owner is created for each | Demonstrated |
| 4 | Users can belong to different showrooms | Demonstrated |
| 5 | Users can switch between authorized showrooms | Demonstrated (API and browser) |
| 6 | Unauthorized showroom cannot be selected | Demonstrated |
| 7 | Changing a tenant ID in a request gives no access | Demonstrated, including a validly signed token without membership |
| 8 | Role assignments persist | Demonstrated |
| 9 | Invitations work securely | Demonstrated: hashed, single use, expiring, no account takeover |
| 10 | No active database means no business operations | Demonstrated |
| 11 | No customer records centrally | Demonstrated by scanning all central tables after real workflows |
| 12 | Automated isolation tests | Demonstrated |
