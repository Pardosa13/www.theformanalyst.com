# Strategy Rethink — deferred items

Things decided but parked for a later phase. Tick them off as they land.

## Phase 6 (automation)
- [ ] **CSV start times may be local time.** PuntingForm's CSV `start time`
  has no time zone. Today it is read as Melbourne time
  (`scoring_integrity.csv_start_time_utc`). For WA, QLD, SA and NT that can
  place a race up to 3 hours early. That only blocks a rescore too soon, never
  too late, but automation needs the real time. Handle it per state: Ladbrokes
  `start_time` (UTC) first, then the CSV read in the track's own time zone.
  The Betfair BSP files carry `STATE_CODE` and `SCHEDULED_RACE_TIME` per race
  and can fill history.

## Data quality
- [ ] **ML market-blend data from mid-Sep to 15 Oct 2026 is low quality.** The
  Railway odds scraper ran once a day (02:00 UTC, ~1pm Melbourne) instead of
  every 5 minutes, so almost no race had a live price under 15 minutes old.
  ML scores, value edge and Kelly from that period were mostly priced off the
  model alone, with no market blend. Treat them as unreliable in any analysis.
  Fixed on 15 Oct 2026 (`*/5 0-12,23 * * *` UTC). Railway logs only go back
  to mid-September, so it may have started earlier.

## Shared name matcher
- [ ] **One horse-name matcher for results, odds and BSP imports.** Today each
  import cleans names its own way: `ladbrokes.normalize_runner_name`
  (apostrophe -> space), `bsp_import.clean_name` (apostrophe dropped, country
  suffix removed), and the old results paths (lowercase only). Odds runs miss
  about 25% of runners by name (e.g. 62 of ~300 on 15 Oct). Build one matcher:
  same cleaning everywhere, TAB number first when the feed has it, name check
  as confirmation, and every miss logged.

## Cleanup
- [ ] **8 flaky tests in `tests/test_security_hardening.py`.** They fail only in
  a full `pytest tests` run and pass on their own: test isolation (shared
  in-memory SQLite / app import order), not a security regression.

## Open questions
- [ ] **Runners with no PFAI are not blended.** `analyzer.js` blends 25%
  analyzer + 75% PFAI only when PFAI >= 40. Anyone else keeps the pure analyzer
  score, which is on a different scale. Report first (see SQL in the Phase 1
  follow-up), fix later.
