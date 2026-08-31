# Design Preview — The Form Analyst Skin

This folder contains **static-only visual previews** for a proposed dark premium sports-betting analytics skin. These files are intentionally standalone HTML/CSS mockups using fake data, so they can be opened locally in a browser without running Flask.

## Created files

- `dashboard.html` — global dashboard/home preview with navigation, sport tiles, recent edges, and performance summaries.
- `racing.html` — racing meeting preview with race selector, ranked runners, best bets, odds/value indicators, insights, scratched runner styling, and results-style table.
- `afl.html` — AFL preview with fixtures, ladder/value cards, player props table, and match prediction cards.
- `ufc.html` — UFC preview with upcoming fight cards, fighter comparison, confidence summary, and betting value table.
- `skin.css` — shared static design system for all preview pages.
- `screenshot.js` — optional Playwright helper that renders each static page to PNG.
- `screenshots/` — target folder for generated PNG screenshots.

## How to open the previews

Open any of these files directly in a browser:

```text
design-preview/dashboard.html
design-preview/racing.html
design-preview/afl.html
design-preview/ufc.html
```

Because the mockups are static, links between preview pages use local relative paths and all sample data is hard-coded.

## Optional screenshot generation

Playwright is not currently a project dependency. If you want local screenshots, install Playwright and Chromium first:

```bash
npm install --save-dev playwright
npx playwright install chromium
node design-preview/screenshot.js
```

Expected output files:

```text
design-preview/screenshots/dashboard.png
design-preview/screenshots/racing.png
design-preview/screenshots/afl.png
design-preview/screenshots/ufc.png
```

## Repo audit notes

Production files were inspected only; they were not modified.

- Base layout template: `templates/base.html`.
- Global styling/theme: currently inline in `templates/base.html`, with page-specific inline styles in templates such as `templates/dashboard.html`, `templates/afl.html`, `templates/mma.html`, and `templates/view_meeting.html`.
- Shared macros/components: no dedicated Jinja macro file was found under `templates/`; shared behavior appears to live in the base layout and repeated page-level patterns.
- Racing templates likely involved in a later migration: `templates/dashboard.html`, `templates/meeting.html`, `templates/view_meeting.html`, `templates/best_bets.html`, `templates/results.html`, and `templates/results_entry.html`.
- AFL template: `templates/afl.html`.
- UFC/MMA template: `templates/mma.html`.
- Dashboard/home template: `templates/dashboard.html`.
- JavaScript affecting tabs/cards/tables appears in inline template scripts, especially `templates/afl.html` and `templates/mma.html`; additional static JS exists at `static/js/betfair-live.js`, and the repository also contains `analyzer.js` for racing analysis support.

## Later migration plan for approved design

1. Extract stable tokens from `design-preview/skin.css` into the production theme layer, ideally a static stylesheet rather than more inline CSS.
2. Update `templates/base.html` last or behind a feature flag, since it affects every page.
3. Migrate one vertical at a time:
   - Dashboard shell and sport tiles into `templates/dashboard.html`.
   - Racing meeting/race cards into `templates/view_meeting.html`, `templates/meeting.html`, and related racing templates.
   - AFL fixture, prop, and prediction layouts into `templates/afl.html`.
   - UFC fight-card and edge-finder layouts into `templates/mma.html`.
4. Preserve existing Jinja variables, route names, API calls, form posts, table IDs, and JavaScript hooks during migration.
5. After each vertical migration, run smoke tests and manually verify that filters, tabs, odds refreshes, and form actions still work.

## Production files likely to change later

These are likely targets after design approval, but they were not changed in this preview-only branch:

- `templates/base.html`
- `templates/dashboard.html`
- `templates/view_meeting.html`
- `templates/meeting.html`
- `templates/best_bets.html`
- `templates/results.html`
- `templates/afl.html`
- `templates/mma.html`
- `static/js/betfair-live.js` if live odds UI hooks need styling/state updates
- A future production stylesheet under `static/` if the inline CSS is extracted
