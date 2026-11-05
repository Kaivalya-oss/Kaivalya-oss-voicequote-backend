# Database Ownership

Each showroom owns and pays for its own PostgreSQL database. VoiceQuote connects to it to do the showroom's work, and does not keep a copy.

| Data | Where it lives |
| --- | --- |
| Customers, phone numbers, addresses | Showroom database |
| Vehicles, offers, accessories, inventory | Showroom database |
| Quotations and items, leads, follow-ups, test rides | Showroom database |
| Showroom audit log and notifications | Showroom database |
| Staff row (name, email, role; no password) | Showroom database, needed for its foreign keys |
| Staff accounts (email, name, password hash) | Central |
| Showroom name, memberships, roles, subscription | Central |
| Connection reference, provider, schema version, health | Central |
| Database credentials | Secrets store |
| Migration run history (versions, status, error code) | Central |

## Enforcement

- Business and control-plane tables use separate SQLAlchemy metadata and separate migrations.
- Tests assert the exact set of central tables, and scan every central table for customer details after real workflows.
- In multi-tenant mode quotation PDFs are rendered in memory on request and are not written to VoiceQuote's disk.
- Server logs drop customer names, phones and email recipients in the quotation, email and WhatsApp paths.

## What a showroom controls

- It can revoke at any time. VoiceQuote deletes the secret and drops connections, and the data stays in place.
- It can also cut access itself by changing the password or firewall rules. Requests then fail with a clear error.
- It can move to another database through Replace, but existing data does not move automatically.

## Not yet decided

- Backups of a showroom's database are the showroom's responsibility. No guidance or tooling exists yet.
- Any future platform analytics must use minimal aggregated or anonymized figures under an explicit written policy. None exist today.
- Moving the existing single-showroom data into a showroom database is out of scope for these phases.
