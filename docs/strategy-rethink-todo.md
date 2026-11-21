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

## Phase 6 follow-ups
- [x] Show "provisional" on the meeting page and add a rating/rail form that
  calls `POST /api/meetings/<id>/set-conditions` (endpoint exists).
- [x] Set `TELEGRAM_ADMIN_CHAT_ID` (and `TELEGRAM_BOT_TOKEN`) on the Daily
  Racing Run service so alerts reach James privately.

## Data quality
- [ ] **ML market-blend data from mid-Sep to 15 Oct 2026 is low quality.** The
  Railway odds scraper ran once a day (02:00 UTC, ~1pm Melbourne) instead of
  every 5 minutes, so almost no race had a live price under 15 minutes old.
  ML scores, value edge and Kelly from that period were mostly priced off the
  model alone, with no market blend. Treat them as unreliable in any analysis.
  Fixed on 15 Oct 2026 (`*/5 0-12,23 * * *` UTC). Railway logs only go back
  to mid-September, so it may have started earlier.

## Shared name matcher
- [x] **One horse-name matcher for results, odds and BSP imports.** Done: `runner_names.py`. Today each
  import cleans names its own way: `ladbrokes.normalize_runner_name`
  (apostrophe -> space), `bsp_import.clean_name` (apostrophe dropped, country
  suffix removed), and the old results paths (lowercase only). Odds runs miss
  about 25% of runners by name (e.g. 62 of ~300 on 15 Oct). Build one matcher:
  same cleaning everywhere, TAB number first when the feed has it, name check
  as confirmation, and every miss logged.

## Decisions log (15 Oct 2026)
- Lay rule tested at Betfair BSP (clean races, Betfair winner, 8% commission):
  analyzer -6.4% on 2,226 lays; ML only -9.7% (300), both -3.4% (399). Price
  bands are noise. The +8.6% in the brief came from laying at bookmaker SP.
  **Phase 3 (lay alert) parked.**
- Site stats stay on the PuntingForm SP. Betfair data is the check on results
  (incomplete flags) and the price for Phase 5 backtests.
- ML judged only on forward data (ml_scored_at before the jump), 8+ weeks.

## Decisions log (16 Oct 2026)
- Meeting pages, Best Bets and the print guide show the **calibrated fair
  price** (no 10% margin) for the analyzer and the ML model. The stored raw
  fields (predicted_odds, win_probability, ml_win_probability) are unchanged,
  so stats, backtests, value edge and Kelly still read the raw numbers.
- ML calibration uses forward data only (scored before the start). It needs
  500 clean races; until then the ML page keeps its old 110% book price. The
  calibration job reruns every Sunday and picks up new data.

- Backtest run 268: backing ANY runner the ML rates above its price lost 20-35%
  for every model. Backing only the TOP PICK when it beats the price was the
  one lead (some cuts +5% to +12% on 150-270 bets, within noise after testing
  ~95 cuts). It is now tracked live on the ML Data page ("Live Test: Top Pick
  When It Beats the Price"), races from 16 Oct only. Judge it after a few
  hundred bets.
- New ML champion #207 (LightGBM, 15% model / 85% market) replaced #163 once
  the champion was re-tested on the same races as the challengers.

## Decisions log (21 Nov 2026)
- Read-only sweep of the favourite, the analyzer, ML and PFAI top picks, the
  gap to each one's 2nd pick and the price band (4,694 races with all three
  ratings, mostly Jul-Nov 2026, starting price and Betfair after 8%).
- A bigger gap to the 2nd pick lifts the strike rate but the price shortens to
  match. Big gaps on all three ratings were the worst favourites: 212 bets,
  -26% SP, -21% Betfair. A big analyzer gap lost in every price band.
- Added as a **trial** on Best Bets (`agree_fav_ml_pf_2to3`): the favourite at
  $2-$3 that the ML and PFAI both have on top and the analyzer does not. 130
  bets, 47% won, +12.9% SP, +21.2% Betfair (87 priced). Lost in Aug and Sep,
  loses under $2 and at $3-$4, and was the best of ~960 cuts, so it is
  flattered. The quiet ML favourite rule stays. Compare the two after ~200
  live bets each at Betfair prices.
- PFAI top pick alone at $10+ (+19% SP on 494) was one $151 winner; without it
  -11%. Dropped. Analyzer note components (class drop, fast last race and 211
  others) gave no clean filter on either rule.

## Missing runners (fixed 16 Oct 2026)
- [x] **The CSV reader dropped runners.** `parseCSV` split on every comma, so
  any row with a quoted comma (a name, gear or comment) was silently thrown
  away, and the runner never reached the database. That is why 3,148 Betfair
  runners had no horse in our races. Fixed with the csv module (reader and
  writer). Past races missing a runner are now flagged incomplete (Betfair
  shows a starter we have no horse for); the lost runners cannot be recovered.

## Cleanup
- [x] **8 flaky tests in `tests/test_security_hardening.py`.** Fixed: another module dropped the shared in-memory tables. They fail only in
  a full `pytest tests` run and pass on their own: test isolation (shared
  in-memory SQLite / app import order), not a security regression.

## Open questions
- [x] ~~Runners with no PFAI are not blended.~~ Dropped: every runner has PFAI. `analyzer.js` blends 25%
  analyzer + 75% PFAI only when PFAI >= 40. Anyone else keeps the pure analyzer
  score, which is on a different scale. Report first (see SQL in the Phase 1
  follow-up), fix later.
