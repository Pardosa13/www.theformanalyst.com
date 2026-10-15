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

## Cleanup
- [ ] **8 flaky tests in `tests/test_security_hardening.py`.** They fail only in
  a full `pytest tests` run and pass on their own: test isolation (shared
  in-memory SQLite / app import order), not a security regression.

## Open questions
- [ ] **Runners with no PFAI are not blended.** `analyzer.js` blends 25%
  analyzer + 75% PFAI only when PFAI >= 40. Anyone else keeps the pure analyzer
  score, which is on a different scale. Report first (see SQL in the Phase 1
  follow-up), fix later.
