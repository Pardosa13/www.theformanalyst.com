# PostgREST endpoint is publicly readable — action required

**Status: open. Not fixable from this repository** — PostgREST runs as its own
Railway service, so the fix is in Railway and in the database's role grants.

## What was found

`https://postgrest-production-c8b6.up.railway.app` answers unauthenticated
requests and exposes **all 45 tables in the `public` schema**, including:

| Table | Rows | Contains |
|---|---|---|
| `users` | 21 | `email`, `password_hash` |
| `chat_messages` | 93 | message bodies |
| `bets` | 86 | staking history |
| `budget_entries`, `budget_categories`, `debt_tracker` | 33+ | personal finances |
| `horses`, `races`, `results`, `meetings` | 200k+ | the full racing dataset |
| `backtest_*` | — | model history and metrics |

The OpenAPI document at `/` lists every table and its columns, so no guessing is
needed — the schema is self-describing to anyone with the URL. There are no
`securityDefinitions`.

## Severity

**Reads: open to the world.** Anyone with the URL can dump every table above.
The 21 rows in `users` include email addresses and password hashes, which is a
credential exposure affecting real people.

**Writes: denied.** `PATCH` and `DELETE` return
`42501: permission denied for table meetings`, so the anonymous role holds
`SELECT` only. The OpenAPI document advertises `POST`/`PATCH`/`DELETE` because
PostgREST generates that from the schema regardless of grants — it does not mean
those verbs work. Nothing can be modified or destroyed through this endpoint.

Verified with probes that could not alter data: an `INSERT` naming a nonexistent
column, and `PATCH`/`DELETE` filtered to `id=eq.-1`, which matches no row. No
credential or message content was read.

## What to do

**1. Close the public route (do this first — it is one click).**
Railway → the PostgREST service → Settings → Networking → remove the public
domain. The service stays reachable on the private network, so anything inside
the Railway project keeps working. Nothing in this repository calls it: the only
consumers are `scripts/ablation_readonly.py`, `scripts/audit_feature_forensics.py`
and `scripts/validate_holdout_75_25.py`, all of which take `POSTGREST_URL` as an
environment variable and can equally use `DATABASE_URL`.

**2. Assume the hashes are compromised and rotate.**
The exposure window is unknown. Force a password reset for all 21 users, and
rotate any API key or token stored in the database. How urgent this is depends on
the hash algorithm — check what `password_hash` holds. A modern bcrypt/argon2
hash with a good work factor buys real time; anything MD5/SHA-1 or unsalted
should be treated as already broken.

**3. Fix the grants, so re-exposing the service is no longer dangerous.**
The anonymous role currently has `SELECT` on everything in `public`. It should
have almost nothing:

```sql
-- Stop the anon role reading the whole schema.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM <anon_role>;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM <anon_role>;

-- Expose only what a reader actually needs, through a schema of views rather
-- than the base tables, so a new table is never public by accident.
CREATE SCHEMA IF NOT EXISTS api;
CREATE VIEW api.races AS SELECT id, meeting_id, race_number, distance FROM public.races;
GRANT USAGE ON SCHEMA api TO <anon_role>;
GRANT SELECT ON api.races TO <anon_role>;
```

Then point PostgREST at that schema and require a token for anything else:

```
PGRST_DB_SCHEMAS=api
PGRST_DB_ANON_ROLE=<anon_role>   # now near-powerless
PGRST_JWT_SECRET=<a long random secret>
```

`PGRST_DB_SCHEMAS=api` is the load-bearing line: while PostgREST serves
`public`, every table you ever add is exposed the moment it is created.

**4. If the endpoint is not needed at all, delete the service.**
It was stood up for the 2026-07 data audit. Nothing in the application depends on
it. The analysis scripts run just as well against `DATABASE_URL`.
