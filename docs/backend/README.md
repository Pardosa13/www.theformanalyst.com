# Back end

A Flask app (`app.py`) on Railway, with Postgres. Scoring runs in Node
(`analyzer.js`); the ML models run in Python. Python modules sit in the
repository root because Railway starts the app from there (`gunicorn app:app`).

## Core

| File | Job |
|---|---|
| `app.py` | The Flask app: login, racing pages, analytics APIs, Best Bets, chat |
| `models.py` | Database tables (users, meetings, races, horses, predictions, results, bets) |
| `analyzer.js` | The scoring algorithm, run by `app.py` through Node |
| `notes_parsing.py` | Turns analyzer notes into scoring components |
| `scoring_versions.py` | Keeps scores comparable across formula versions |
| `scratchings.py` | Scratching rules |
| `strike_rate_matching.py` | Jockey and trainer strike-rate matching |

## Racing data and odds

| File | Job |
|---|---|
| `puntingform_service.py` | PuntingForm API client (meetings, form, results) |
| `ladbrokes.py` | Ladbrokes live odds and Next To Go |
| `odds_ingest.py` | Saves live pre-race odds (run by a GitHub workflow) |
| `market_probability.py` | Market win chances, corrected for favourite-longshot bias |
| `book_quality.py` | Checks a race's price data is complete enough to learn from |
| `admin/betfair_mapping.py` | Betfair market mapping (see [betfair.md](betfair.md)) |

## Machine learning

| File | Job |
|---|---|
| `backtest.py` | Nightly backtest and model training |
| `ml_predict.py` | Scores runners with the active champion model |
| `ml_shadow_routes.py` | Shadow ML pages and APIs |
| `model_classes.py` | Shared model classes and Kelly staking maths |
| `models/` | Saved model files |

## Race Animations

`race_animation_routes.py` (page and APIs), `race_animation_scoring.py`
(prediction score), `race_animation_calibration.py` (fits the race to the
result), `race_animation_tuning.py` (picks weightings from history).

## AFL

| File | Job |
|---|---|
| `afl_routes.py` | AFL pages and APIs |
| `afl_db.py` | AFL tables and helpers |
| `afl_data.py` | AFL data sources |
| `afl_sync.py` | Nightly sync (Railway cron) |
| `afl_backtest.py` | AFL model training and scoring |
| `afl_weather.py` | Weather backfill |
| `afl_setup.py` | One-time setup |
| `afl_fix_2026_ids.py` | One-off 2026 player ID repair (run by a workflow) |
| `data/` | AFL stats CSVs |

## UFC / MMA

`mma_routes.py` (pages), `mma_models.py` (tables), `mma_data.py` (odds),
`mma_sync.py` (weekly sync), `mma_backtest.py` (accuracy report),
`mma_name_utils.py` (fighter names), `mma_seed.py` (one-time import).

## Personal tools

`bet_tracker.py` (bet log, admin only) and `budget_tracker.py` (household budget).

## Scripts and jobs

| Where | What |
|---|---|
| `scripts/` | One-off audits, repairs and model promotion tools |
| `scripts/migrations/` | One-off database column and data repairs |
| `migrations/` | Alembic schema migrations |
| `.github/workflows/` | Tests on every push; odds ingest, MMA sync, AFL stats fetch |

## Tests

`tests/` holds the Python and Node tests. Run them with `python -m pytest tests`.
They also run on every push.
