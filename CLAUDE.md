# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

The Form Analyst (theformanalyst.com): an invite-only Flask site for Australian
horse racing analysis, with AFL and UFC hubs and two personal tools (bet
tracker, household budget). It runs on Railway with Postgres. Pushing to `main`
deploys the live site.

`docs/backend/README.md` and `docs/frontend/README.md` say what each Python
file and each page does. Read those rather than guessing from file names.
`docs/strategy-rethink-todo.md` holds the decisions log and parked work.

## Commands

CI uses Python 3.12 and Node 20. Use Python 3.12: the pinned `numpy==1.26.4`
does not install on 3.13.

```
pip install -r requirements.txt pytest
python -m pytest tests -q                              # everything
python -m pytest tests/test_best_bet_rules.py -q       # one file
python -m pytest tests/test_place_bet.py -k some_name  # one test
node --test tests/race-animation-engine.test.js        # Node suites on their own
python app.py                                          # run locally
```

- Tests need no secrets and no Postgres. They use SQLite and stubbed network
  calls. `tests/conftest.py` sets placeholder `PUNTINGFORM_API_KEY` and
  `SECRET_KEY`, because importing `app.py` fails without them.
- The Node suites (`tests/*.test.js`) are run by pytest through
  `tests/test_race_animation_engine.py`. They are skipped, not failed, when
  `node` is missing.
- There is no linter and no build step. `package.json` exists only for
  `papaparse`, which `analyzer.js` uses.
- With no `DATABASE_URL` the app falls back to a local SQLite file. Some
  queries are Postgres-only (JSON operators such as `csv_data->>'...'`,
  `SPLIT_PART`) and are wrapped in `try/except` so they return empty on SQLite.

## Architecture

### Flat layout, one big app

Every Python module sits in the repository root because Railway starts
`gunicorn app:app` from there (`Procfile`). Do not move modules into packages.

`app.py` is about 12,000 lines: login, racing pages, analytics APIs, Best Bets,
chat. Other areas attach through `register_*_routes(app, ...)` functions
(`afl_routes`, `mma_routes`, `race_animation_routes`, `ml_shadow_routes`,
`pattern_routes`, `bet_tracker`, `budget_tracker`), not Flask blueprints. Search
`app.py` by function name; do not read it top to bottom.

### Database changes happen at startup, not through Alembic

`app.py` runs `db.create_all()` on import, then a long series of
"if the column is missing, `ALTER TABLE ... ADD COLUMN`" blocks. To add a
column: add it to the model in `models.py` **and** add a matching guarded
`ALTER TABLE` block in `app.py`. `migrations/` and `scripts/migrations/` are
old one-offs and are not part of the deploy.

AFL tables live in `afl_db.py` and UFC tables in `mma_models.py`, not in
`models.py`.

### Three ratings per runner

1. **Analyzer** (`analyzer.js`): the original hand-built scoring algorithm.
   `run_analyzer()` in `app.py` pipes JSON to `node analyzer.js` over stdin and
   reads JSON back. Its free-text `notes` are turned into scoring components by
   `notes_parsing.py`.
2. **PFAI**: PuntingForm's own score, read from the CSV/API data stored on
   `Horse.csv_data`.
3. **ML** (`ml_predict.py`): the active "champion" model, blended with the
   market. `backtest.py` trains challengers weekly and promotes a champion.
   Having no active champion is a supported state: the site then shows no ML
   picks.

Data model: `Meeting` → `Race` → `Horse` → `Prediction` (scores, prices, ML
fields, Best Bets tracking) and `Result`.

### Rules that keep the numbers honest

These exist because each one was broken before. Keep them intact.

- **No look-ahead.** Training and stats may only use what was known before the
  race. `point_in_time_rates.py` rebuilds jockey/trainer form as it stood then.
  ML is judged only on forward data (`ml_scored_at` before the start); ML scores
  from before 16 Oct 2026 were computed after the race.
- **Started races are never rescored.** See `scoring_integrity.py` and the
  rescore path in `daily_run.py`.
- **Champion Scores from different formula versions are not comparable.** Use
  `scoring_versions.py` helpers before putting scores on one chart or table.
- **Races with incomplete results are excluded** from training and stats
  (`Race.results_incomplete`, `book_quality.py`).
- **Model classes must live in `model_classes.py`**, never inside
  `backtest.py`. A class defined in a script pickles as `__main__.X` and then
  fails to load under gunicorn.
- **One horse-name matcher:** `runner_names.py`. Do not add another name
  cleaner for a new import.
- **Stored raw fields stay raw** (`predicted_odds`, `win_probability`,
  `ml_win_probability`). Pages show calibrated fair prices worked out at
  display time; stats, value edge and Kelly read the raw fields.

### Best Bets

`best_bet_rules.py` holds the current rules (a favourite the ML also picks,
plus one LAY rule). They need live Ladbrokes prices, so picks are made and
recorded when the Best Bets page is opened before the jump. Route keys are
stored in `Prediction.best_bet_routes` and counted by prefix in
`betfair_check.py` (`place*`, `lay*`, everything else is a win bet), so a new
key must not be a substring of another. `place_bet.py` keeps the retired place
rules' record for the ML Data page.

### Jobs that run outside the web app

Each is a root script with a `__main__` block, run as its own Railway cron
service: `daily_run.py` (every 30 minutes: import, score, rescore, re-price),
`odds_ingest.py` (live odds every 5 minutes), `backtest.py` (weekly training),
`pattern_finder.py`, `calibrate.py`, `results_settle.py`, `afl_sync.py`,
`mma_sync.py`. GitHub Actions run the tests, fetch AFL stats with R twice a day
(committing `data/afl_2026_stats.csv` back to the repository), and run a weekly
MMA sync.

### Read-only audits

`audit/` is a separate Railway job with its own `Dockerfile`. It opens Postgres
read-only, runs the tasks named in `AUDIT_TASKS` (files in `audit/tasks/`, `.py`
or `.sql`), and prints each row to the logs as `AUD|<run id>|...`. It is the way
to ask questions of production data. A task that imports a root module needs
that module added to the `COPY` lines in `audit/Dockerfile`.

## Front end rules

Pages are Jinja templates extending `templates/base.html`, with Bootstrap 5 and
page-specific CSS and JS inline in each template.

- Use the colour tokens in `base.html` `:root` (`--accent`, `--text-muted`),
  not raw hex values. Do not darken `--text-muted`; it is tuned for contrast.
- Outside libraries load from a CDN with a pinned version and an `integrity`
  hash.
- Anything that changes data must be a `POST`. A `before_request` hook in
  `app.py` blocks cross-site posts by checking `Origin`/`Referer`.
- Escape data before putting it into `innerHTML` (each page has an `esc()`
  helper).
- Analytics pages, results entry, database export and model download are admin
  only.

## House style

- Text shown on the site is written in plain words for a punter, not in
  statistics jargon. Say what a number counts.
- Commit subjects read `Area: what changed in plain words`, for example
  `Best Bets: add a LAY box, kept apart from the picks`.
- Betting findings are reported with the number of bets and the price they were
  measured at (Betfair after 8% commission, or starting price). No rule here is
  described as a proven profit.
