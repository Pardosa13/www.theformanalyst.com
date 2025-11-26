# Betfair Integration README

This document explains how to enable and test the Betfair integration.

Summary
- Small Flask microservice `betfair_service.py` polls Betfair and exposes SSE `/stream`.
- Supports certificate login (optional) and username/password login.
- The feature is guarded with `BETFAIR_ENABLED`. Default is `false`. Nothing runs unless you enable and provide credentials.

One-time setup (Railway)
1. Add Railway variables (Project -> Settings -> Variables):
   - SQLALCHEMY_DATABASE_URI — the same DB your app uses (so the poller can read market ids)
   - BETFAIR_USERNAME — your Betfair username
   - BETFAIR_PASSWORD — your Betfair password
   - BETFAIR_APP_KEY — your Betfair app key
   - (optional) BETFAIR_PEM_B64 — Base64 of client PEM file if using cert login
   - BETFAIR_MARKET_IDS — optional comma-separated market IDs to monitor
   - BETFAIR_POLL_INTERVAL — optional (default 2)
   - BETFAIR_TLD — .com (or .com.au)
   - BETFAIR_ENABLED — false (set to true only after you have credentials & migration)

Run migration to add nullable columns
- Run locally (recommended for testing):
  - export SQLALCHEMY_DATABASE_URI="postgresql://user:pass@host/db"
  - python migrate_add_result_columns.py
- On Railway (one-off run):
  - Use Railway "Run" / "New Run" UI and run: python migrate_add_result_columns.py

Enable and test
1. Set BETFAIR_ENABLED=true (only after credentials and migrations done).
2. Redeploy service.
3. Visit a meeting page in the site and open DevTools -> Network -> filter EventSource or look for /stream to confirm SSE.
4. Upload a CSV with a meeting — the upload process will attempt best-effort mapping to Betfair markets and populate Race.market_id and horse.betfair_selection_id when confident.
5. Use the admin UI `/admin/betfair-mapping` to map any unmapped races manually.

Security
- Do NOT commit any secrets to GitHub. Use Railway (or another secrets manager) to store credentials.
- If a credential is exposed, rotate it at Betfair immediately.

Notes for maintainers
- If your table/column names differ from assumptions here (horses/races), adapt `betfair_service.py`, `admin/betfair_mapping.py`, and `migrate_add_result_columns.py`.
- The simple mapping logic uses fuzzy matching (rapidfuzz). Tweak thresholds and audit logs as needed.
