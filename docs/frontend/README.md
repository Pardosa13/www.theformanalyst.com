# Front end

Everything the browser sees. Pages are Jinja templates rendered by Flask,
styled with Bootstrap 5 plus the site's own design tokens.

## Where things live

| Folder | What it holds |
|---|---|
| `templates/` | Every page on the site (HTML + page-specific CSS and JS) |
| `static/js/` | Shared browser scripts (Race Animations page, live Betfair odds) |
| `static/` | iOS home-screen icons and the horse-art test page |
| `admin/templates/admin/` | Betfair mapping admin page |

## Pages

**Shared**

| File | Page |
|---|---|
| `base.html` | Site shell: nav bar, design tokens, Next To Go ticker, chat widget. Every page extends it. |
| `login.html` | Login |
| `404.html`, `500.html` | Error pages |

**Racing**

| File | Page |
|---|---|
| `dashboard.html` | Dashboard / CSV upload |
| `import_from_api.html` | Import meetings from PuntingForm |
| `history.html` | Meeting history |
| `view_meeting.html` | One meeting's analysis |
| `ml_meetings.html`, `MLRaceMeetings.html` | ML race meetings list and detail |
| `best_bets.html` | Best Bets |
| `results.html`, `results_entry.html` | Results list and entry (admin) |
| `race-animations-predictions.html` | Race Animations & Predictions |

**Analytics (admin)**

| File | Page |
|---|---|
| `data.html` | Data Analytics |
| `ml_data.html` | ML Analytics |
| `ml_shadow.html` | Shadow ML scoring |
| `backtest.html` | Backtest engine |
| `admin.html` | Users and strategy components |

**Other sports and tools**

| File | Page |
|---|---|
| `afl.html` | AFL Hub |
| `mma.html` | UFC Hub |
| `bet_tracker.html`, `bet_tracker_history.html`, `bet_tracker_stats.html` | Bet Tracker |
| `budget_tracker.html` | Budget Tracker (two accounts only) |

`meeting.html` is not rendered by any route and looks unused.

## Shared scripts (`static/js/`)

| File | Used by |
|---|---|
| `race-animation.js` | Race track and race engine |
| `race-animation-scoring.js` | Browser half of the prediction maths |
| `race-animation-audio.js` | Race sound |
| `race-horse-art.js` | Horse and jockey drawings |
| `betfair-live.js` | Live odds and results on the meeting page |

## Rules

- **Colours:** use the tokens in `base.html` `:root` (`--accent`, `--text-muted`
  and so on), not raw hex values. `--text-muted` is tuned to 4.5:1 contrast on
  every surface. Don't darken it.
- **Outside libraries:** load them from a CDN with a pinned version and an
  `integrity` hash. Never load an unpinned "latest" URL.
- **Forms that change data** must use `POST`. The server blocks cross-site posts.
- **Escape data** before putting it into `innerHTML` (use the page's `esc()` helper).
