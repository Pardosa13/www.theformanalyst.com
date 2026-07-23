# Frontend Design Audit — The Form Analyst

Date: 2026-05-12
Scope: `templates/base.html`, every `templates/*.html` page, and `admin/templates/admin/betfair_mapping.html`.

## Executive Summary

The site has already moved toward a premium dark dashboard aesthetic, but it is still operating as a collection of heavily styled individual pages rather than a unified product interface. The biggest opportunity is not a single page redesign; it is establishing a durable design system that standardizes hierarchy, density, decision states, table patterns, mobile alternatives, and betting-intelligence presentation.

The audit found three recurring themes:

1. **The product value is strong, but the interface does not always guide the bettor through a clear decision flow.** Pages surface a lot of valuable information, but users must frequently infer what matters next.
2. **The visual language is directionally good but inconsistent.** The base template defines tokens, cards, buttons, tables, alerts, and dark mode foundations, while most pages add large local style blocks that redefine patterns in slightly different ways.
3. **The frontend architecture has scaling risk.** Many pages contain large inline CSS and JavaScript sections, inline style attributes, duplicated component concepts, and page-specific interaction code. A design overhaul should preserve endpoints and Jinja logic while extracting reusable presentation primitives gradually.

## Audit Inventory

### HTML templates reviewed

- `templates/base.html`
- `templates/dashboard.html`
- `templates/history.html`
- `templates/meeting.html`
- `templates/view_meeting.html`
- `templates/results.html`
- `templates/results_entry.html`
- `templates/data.html`
- `templates/best_bets.html`
- `templates/backtest.html`
- `templates/ml_shadow.html`
- `templates/admin.html`
- `templates/import_from_api.html`
- `templates/login.html`
- `templates/afl.html`
- `templates/mma.html`
- `templates/404.html`
- `templates/500.html`
- `admin/templates/admin/betfair_mapping.html`

### Frontend scale indicators

| Indicator | Finding |
|---|---:|
| Total reviewed HTML lines | 17,198 |
| Templates extending `base.html` | 15 |
| Standalone templates outside base shell | 4 |
| Templates with page-local `<style>` blocks | 18 |
| Templates with page-local `<script>` blocks | 13 |
| Inline `style=` attributes across templates | 662 |
| Inline `onclick=` handlers across templates | 95 |

These numbers are not automatically bad, but they confirm that the frontend has outgrown page-by-page styling.

## Navigation Map

### Global authenticated navigation

- Dashboard
- Race Meetings
- Results
- Data
- Backtest
- Best Bets
- AFL
- UFC
- ML, admin only
- Admin, admin only
- User pill
- Logout

### Primary product areas

- **Dashboard:** high-level product entry, shortcuts, recent meetings, upload/analyze action.
- **Race Meetings / History:** meeting list and entry points to analyzed race cards.
- **View Meeting:** core racing intelligence page with meeting overview, race navigation, rankings, speed maps, sectionals, notes, and print/PDF behavior.
- **Results / Results Entry:** operational workflow for entering and completing race results.
- **Best Bets:** betting recommendation surface and manual posting workflow.
- **Data Analytics:** model, betting, and performance analytics.
- **Backtest / ML Shadow:** advanced model validation and internal intelligence tooling.
- **AFL / UFC:** adjacent sports betting products inside the same shell.
- **Admin:** user and component management.
- **Login / Errors / Betfair mapping:** supporting shell and admin surfaces.

### Implied user goals

- Quickly understand what meetings or events require attention today.
- Identify the highest-value betting opportunities.
- Compare model confidence, market price, risk, and supporting evidence.
- Move from analysis to action without losing context.
- Validate previous performance and trust the system.
- Enter results and maintain data quality efficiently.
- Use the product on mobile while close to race time.

### Intended audience inferred from the UI

- Racing bettors who want sharper form interpretation.
- Data-led punters who compare model output against market price.
- Admin or operator users maintaining meetings, results, mappings, and model data.
- Expanding sports-betting users for AFL and UFC.

## PASS 1 — UX Architecture Audit

### What is working

- The global shell creates a recognizable product home and persistent navigation.
- Core pages are organized around operational domains: meetings, results, data, best bets, backtesting, and admin.
- Race-level pages include jump navigation, overview cards, ranked tables, speed maps, sectionals, and supporting analysis, which are appropriate for an expert betting workflow.
- Dashboard shortcuts help users reach high-value sections quickly.
- Results entry pages appear optimized around completion state and repetitive workflow.

### Main UX architecture issues

#### 1. The decision flow is present but not explicitly staged

The betting workflow should make the next decision obvious:

1. What is happening today?
2. Which race/event matters most?
3. Which selection is actionable?
4. Why should I trust it?
5. What price or edge is required?
6. What action should I take?
7. How did the system perform afterward?

Today, pages show many of these data points, but the order and visual emphasis varies by page. The result is cognitive overhead: users scan for value instead of being led to value.

**Recommendation:** create a standard betting-decision layout:

- **Context band:** meeting/event, time, track/venue, status.
- **Action band:** top pick, confidence, edge, price, risk, recommended action.
- **Evidence band:** model factors, speed map, sectionals, market movement, notes.
- **Audit band:** result, closing price, variance, model learning.

#### 2. Global navigation is becoming too broad

The navigation mixes daily user workflows, admin tooling, model tooling, and multiple sports. On desktop it is manageable, but it risks crowding, weak grouping, and unclear hierarchy as more modules are added.

**Recommendation:** group navigation by intent:

- **Racing:** Dashboard, Meetings, Best Bets, Results, Data.
- **Models:** Backtest, ML Shadow.
- **Sports:** AFL, UFC.
- **System:** Admin, User, Logout.

This can be implemented visually with dropdowns or grouped pills without changing routes.

#### 3. Page headers do not use one consistent information model

Different pages use different header patterns, title sizes, meta placement, actions, stats, and tab structures. This reduces product polish and makes users relearn each screen.

**Recommendation:** standardize every page header into:

- Eyebrow / module label.
- H1 page title.
- One-sentence utility description.
- Primary action.
- Secondary actions.
- Optional status chips.

#### 4. Data-heavy pages need stronger “answer first” hierarchy

Pages like meeting view, data analytics, AFL, UFC, backtest, and admin present large volumes of tables, cards, tabs, charts, and controls. Expert users tolerate density, but only if the top-level answer is immediate.

**Recommendation:** introduce an **insight summary pattern** at the top of each data-heavy page:

- “Best opportunity.”
- “Largest risk.”
- “Market mismatch.”
- “Confidence trend.”
- “Needs action.”

#### 5. Interaction priority is uneven

Some page actions are styled with strong buttons even when they are low-frequency admin tasks, while some betting-critical interactions are embedded inside dense cards or tables.

**Recommendation:** define action priority rules:

- Primary action: one per view.
- Secondary actions: max three visible before overflow.
- Destructive actions: always visually isolated.
- Betting action: always adjacent to confidence, edge, and market price.
- Admin/maintenance actions: lower emphasis unless the task is blocking.

## PASS 2 — Visual Design System Audit

### What is working

- The site has a strong dark-mode foundation.
- The base template defines useful tokens for brand, surfaces, borders, text, and status.
- Outfit and DM Mono are a good pairing for dashboard readability and numerical data.
- Gradients and dark cards give the product a premium analytics feel.
- The product already uses status colors for success, warning, danger, and info.

### Main visual issues

#### 1. Token usage is incomplete

The base template defines color tokens, but page-local styles still frequently introduce hard-coded colors, gradients, spacing, borders, and shadows. This makes the same concept look slightly different on each page.

**Recommendation:** expand and enforce semantic tokens:

```css
--surface-page
--surface-card
--surface-card-raised
--surface-control
--surface-critical
--border-subtle
--border-strong
--text-primary
--text-secondary
--text-tertiary
--text-inverse
--intent-positive
--intent-negative
--intent-warning
--intent-info
--intent-model
--intent-market
--intent-edge
--intent-action
```

Use semantic tokens for meaning, not just color names.

#### 2. Spacing lacks a visible scale

Cards, tables, nav pills, headers, modals, and chart sections use many one-off spacing values. The result is not chaotic, but it prevents the interface from feeling fully designed.

**Recommendation:** create spacing tokens:

```css
--space-1: 4px;
--space-2: 8px;
--space-3: 12px;
--space-4: 16px;
--space-5: 20px;
--space-6: 24px;
--space-8: 32px;
--space-10: 40px;
```

Then define layout primitives using those tokens.

#### 3. Typography hierarchy needs sharper rules

The UI uses headings, labels, badges, mono numbers, uppercase labels, emojis, and icons. These all help scanning, but without strict rules they can compete.

**Recommendation:** define type roles:

- Display metric.
- Page title.
- Section title.
- Card title.
- Table header.
- Body copy.
- Muted metadata.
- Mono data.
- Chip label.

Each role should specify font size, weight, line height, color, and casing.

#### 4. Elevation is overused as decoration rather than hierarchy

Many cards use shadows, gradients, borders, glows, and hover lifts. Dark dashboards need subtle elevation, but repeated premium effects can flatten the hierarchy because everything looks important.

**Recommendation:** define elevation levels:

- Level 0: page background.
- Level 1: default card.
- Level 2: interactive card.
- Level 3: sticky/overlay/nav.
- Level 4: modal/chat.

Use glow only for active, selected, live, or truly recommended states.

#### 5. Dark mode consistency is close but not complete

Most base styles are dark, but standalone error/admin pages and some generated or inline content do not consistently inherit the shell. Some table-generated content and JS-generated HTML hard-code colors.

**Recommendation:** make all standalone pages extend or visually match the base shell, and move JS-generated HTML styles into classes.

## PASS 3 — Frontend Engineering Audit

### What is working

- Most app templates extend `base.html`, giving the site a common shell.
- Existing Jinja and endpoint usage can be preserved during a visual overhaul.
- Bootstrap provides stable base components and responsive utilities.
- Page-specific CSS avoids accidental global regressions in the short term.

### Engineering risks

#### 1. Page-local CSS is the dominant styling model

Nearly every page has its own `<style>` block. Several templates are over 1,000 lines, and some contain very large combined HTML/CSS/JS implementations.

**Risk:** every new design change requires page-by-page edits, which raises inconsistency and regression risk.

**Non-breaking recommendation:** introduce CSS in layers:

1. Keep all templates and Jinja logic intact.
2. Add `static/css/tokens.css`.
3. Add `static/css/components.css`.
4. Add `static/css/pages.css` only for page-specific exceptions.
5. Gradually replace local styles with reusable classes.

#### 2. Inline styles make consistency difficult

The templates contain hundreds of inline style attributes. Inline styles override CSS architecture and make responsive states harder.

**Risk:** visual redesigns become brittle because the cascade cannot reliably control components.

**Non-breaking recommendation:** migrate inline styles opportunistically:

- First migrate repeated inline styles to utility classes.
- Then migrate component-level styles to component classes.
- Leave truly one-off layout patches until the final cleanup phase.

#### 3. Inline event handlers limit componentization

Inline `onclick` handlers are common in data-heavy pages. They are not necessarily broken, but they make behavior harder to test, move, and reuse.

**Risk:** design components cannot be reused cleanly because markup and behavior are coupled.

**Non-breaking recommendation:** do not rewrite all JS at once. Instead:

- Preserve function names and endpoints.
- Add `data-action` and `data-*` attributes for new components.
- Move new behavior to delegated listeners.
- Convert old handlers only when touching a specific component.

#### 4. JS-generated HTML uses presentation markup

Meeting charts, speed maps, heatmaps, and analytics sections generate chunks of HTML with embedded classes and sometimes inline styles.

**Risk:** the design system cannot fully govern generated UI.

**Recommendation:** define classes for all generated states, then have JS output semantic class names only.

#### 5. Base template is carrying too many responsibilities

`base.html` defines tokens, global CSS, navigation, flash messages, Bootstrap loading, page content, chatbot markup, chatbot CSS, and chatbot JS.

**Risk:** the shell is becoming a monolith.

**Non-breaking recommendation:** split by responsibility:

- `base.html` for structure and blocks.
- `partials/nav.html` for navigation.
- `partials/chat.html` for chatbot markup.
- `static/css/app.css` for shell/components.
- `static/js/chat.js` for chatbot behavior.

This can be done safely because rendered HTML, IDs, endpoints, and function behavior can remain unchanged.

## PASS 4 — Mobile UX Audit

### What is working

- Several pages include mobile media queries.
- Bootstrap’s responsive table wrappers are used in many places.
- The fixed nav and chatbot include mobile handling.
- Race navigation pills provide useful anchor-based movement.

### Main mobile issues

#### 1. Mobile density is too high on expert pages

Race cards, analytics tables, backtest outputs, AFL/UFC dashboards, and results entry workflows likely require excessive scrolling on mobile.

**Recommendation:** mobile should not just shrink desktop. Use progressive disclosure:

- Top summary card first.
- Collapsed evidence sections.
- Sticky race/event selector.
- Horizontal chips for race navigation.
- Expandable runner/fighter/player detail cards.

#### 2. Tables need mobile alternatives

Responsive table wrappers preserve data but often create horizontal scrolling fatigue. This is acceptable for admin tables, but betting decisions need faster scanning.

**Recommendation:** define table-to-card alternatives:

- **Ranking table desktop → runner cards mobile.**
- **Results entry table desktop → one race accordion with tap targets.**
- **Analytics table desktop → top movers / top outliers cards.**
- **Admin table desktop → compact list rows with action menu.**

#### 3. Touch targets are inconsistent

Dense nav pills, table buttons, chart toggles, finish-position buttons, and admin actions need consistent minimum hit areas.

**Recommendation:** define mobile touch rules:

- Minimum target: 44px high/wide.
- Minimum gap: 8px between independent actions.
- Destructive actions require spacing from routine actions.
- Sticky bottom action bars only for primary workflows.

#### 4. Chart usability needs a mobile mode

Charts and heatmaps are valuable, but chart controls and canvases become difficult on small screens.

**Recommendation:** add mobile chart modes:

- Default to insight summary above chart.
- Provide segmented control for one metric at a time.
- Use horizontal scroll only inside the chart, not the full page.
- Offer “top 3 only” as the default mobile view.
- Make legends tappable and large.

#### 5. Chatbot can conflict with mobile workflows

The floating chat button and container are useful, but on data-entry or race-analysis screens they may cover actions or table content.

**Recommendation:** hide, minimize, or reposition chat on specific high-density workflows, especially results entry and meeting analysis on mobile. Keep the endpoint and behavior unchanged.

## PASS 5 — Product Strategy Audit

### What is working

- The product has meaningful differentiation: racing form, sectionals, speed maps, model intelligence, best bets, results tracking, and backtesting in one environment.
- The addition of AFL and UFC suggests a broader sports-intelligence platform opportunity.
- The chatbot can become a retention and explanation layer if integrated into workflows.
- Backtesting and results pages can generate trust if surfaced more prominently.

### Strategic UX gaps

#### 1. Perceived value should be visible within five seconds

A new or returning user should immediately see:

- Today’s strongest bet.
- Current model edge.
- Confidence level.
- Why it is recommended.
- Recent proof of performance.

**Recommendation:** create a dashboard hero called **Today’s Edge**:

- Best bet / top opportunity.
- Market price vs model price.
- Confidence and risk.
- One-line reason.
- CTA to full evidence.

#### 2. Trust needs a first-class interface

The product contains trust-building assets: results, backtests, ML shadow data, and analytics. They should not feel like separate tools only power users visit.

**Recommendation:** add trust modules throughout the product:

- Last 7 / 30 day strike rate.
- ROI or profit trend.
- Closing line value where available.
- Calibration: confidence bucket vs actual result.
- “Why this pick?” evidence stack.

#### 3. Differentiation should be expressed as product language

Current labels are functional. The product needs named patterns that users remember.

**Recommendation:** create branded intelligence modules:

- **Edge Signal:** value difference between model and market.
- **Confidence Stack:** model, form, sectionals, map, market alignment.
- **Risk Flags:** volatility, maiden, tempo uncertainty, data gaps.
- **Race Shape:** speed map and tempo interpretation.
- **Proof Panel:** results, CLV, ROI, calibration.

#### 4. Onboarding is underdeveloped

The UI assumes users know how to interpret the product. Expert users may be fine, but onboarding helps conversion and retention.

**Recommendation:** add low-risk onboarding:

- Empty-state examples.
- Tooltip glossary for edge, confidence, PFAI, sectionals, and speed map.
- First-run dashboard guide.
- “How to read this race” panel on meeting pages.
- Chat prompts tied to current screen.

#### 5. Monetization UX should be confidence-based, not obstructive

If this product monetizes, the interface should not simply lock random pages. It should show enough value to make the upgrade obvious.

**Recommendation:** use premium framing around:

- Advanced evidence stack.
- Historical performance filters.
- Export tools.
- Full model explanation.
- Alerts and notifications.
- Multi-sport expansion.

## Recurring UX Problems

- Decision hierarchy is not standardized across racing, AFL, UFC, analytics, and admin pages.
- Data appears before interpretation too often.
- Primary actions are not always visually distinct from maintenance actions.
- Navigation groups are not aligned with user intent.
- Page headers and summary sections vary too much.
- Expert density is high without enough progressive disclosure.
- Results and backtesting are not leveraged enough as trust builders.

## Recurring Visual Issues

- Color tokens exist but are not consistently used across all templates.
- Hard-coded colors, gradients, spacing, and shadows appear throughout page-local styles.
- Typography roles are implicit rather than codified.
- Emojis, icons, badges, gradients, and glows sometimes compete for attention.
- Tables, cards, and metric tiles have similar concepts but inconsistent treatments.
- Standalone pages do not fully match the premium app shell.
- JS-generated UI can bypass dark-mode and component rules.

## Recurring Architecture Issues

- Most templates contain large local CSS blocks.
- Many templates contain inline styles.
- Many interactions are bound through inline `onclick` handlers.
- `base.html` mixes shell, design tokens, nav, layout, chat markup, chat CSS, and chat JS.
- Data-heavy page scripts generate styled HTML directly.
- Component naming is page-specific rather than product-system based.
- There is no central design-system documentation or component inventory.

## Recommended Product Design System

### 1. Semantic tokens

Create a token file for:

- Brand.
- Surfaces.
- Text.
- Borders.
- Intent/status.
- Betting-specific meaning: edge, confidence, risk, market, model, result.
- Spacing.
- Radius.
- Shadows/elevation.
- Motion.
- Z-index.

### 2. Layout primitives

Define reusable primitives:

- App shell.
- Page header.
- Context bar.
- Metric grid.
- Insight strip.
- Card stack.
- Split panel.
- Sticky subnav.
- Evidence accordion.
- Mobile card list.

### 3. Component primitives

Define components:

- Button.
- Icon button.
- Action group.
- Badge/chip.
- Status pill.
- Metric card.
- Insight card.
- Data table.
- Ranking table.
- Runner card.
- Race card.
- Chart panel.
- Empty state.
- Alert.
- Modal.
- Tabs.
- Form field.
- Chat entry point.

### 4. Betting intelligence presentation rules

Every betting recommendation should show:

- Selection.
- Event/race context.
- Confidence.
- Model price or score.
- Market price.
- Edge.
- Risk flags.
- Evidence summary.
- Recommended action.

Use a consistent order and color language everywhere.

### 5. Dashboard philosophy

Dashboard should answer:

- What should I look at now?
- What is the best opportunity?
- What needs action?
- Can I trust today’s model?
- What changed since last time?

Avoid making dashboard a generic menu. It should be an intelligence command center.

## Non-Breaking Implementation Plan

### Phase 1 — Design system foundation, no behavior changes

- Add central token CSS file.
- Add central component CSS file.
- Keep all existing IDs, endpoints, Jinja variables, forms, and JavaScript functions.
- Update `base.html` to load new CSS while preserving existing page CSS.
- Add documentation for page header, metric cards, buttons, badges, and tables.

### Phase 2 — Shell and navigation polish

- Refactor global nav into grouped visual sections without changing route names.
- Standardize page header component.
- Make standalone error pages and Betfair mapping visually consistent with the app.
- Improve flash messages and chatbot positioning rules.

### Phase 3 — High-value product surfaces

- Redesign Dashboard around Today’s Edge, needs action, recent proof, and shortcuts.
- Redesign Best Bets around the betting-decision card model.
- Redesign View Meeting around context, action, evidence, and audit bands.
- Add mobile-first runner cards as an alternative to dense tables.

### Phase 4 — Data and admin consistency

- Normalize analytics cards, chart panels, tabs, and tables.
- Convert admin and results flows to consistent form/table/action patterns.
- Make destructive and operational actions visually predictable.

### Phase 5 — Frontend extraction

- Move repeated CSS from templates into `static/css/components.css`.
- Move chatbot JS to `static/js/chat.js` after preserving behavior.
- Move page-specific JS into dedicated files only after test coverage or manual verification exists.
- Replace inline styles and inline click handlers incrementally.

## Highest-Impact Page Recommendations

### `templates/base.html`

- Keep the dark premium direction.
- Split tokens/components/nav/chat into separate maintainable files over time.
- Group navigation by user intent.
- Fix inconsistent nav markup around AFL/UFC grouping during the nav redesign.
- Add a `page_header` macro or include to standardize page intros.

### `templates/dashboard.html`

- Reframe as an intelligence command center instead of only a landing menu.
- Add Today’s Edge, Needs Attention, Recent Proof, and Quick Actions.
- Show a small performance/trust strip above secondary cards.

### `templates/view_meeting.html`

- This is the core product experience and should receive the most design attention.
- Add a top-level race decision summary before dense evidence.
- Convert mobile rankings into runner cards.
- Make confidence, edge, risk, and evidence visually consistent.
- Default mobile chart views to top insights rather than full complexity.

### `templates/best_bets.html`

- Treat each bet as a decision card.
- Show selection, race/event, recommended price, confidence, edge, risk flags, and rationale in the same order every time.
- Add trust context: recent similar bets, historical hit rate, or model calibration.

### `templates/data.html`

- Add an executive insight layer before charts/tables.
- Standardize chart panel controls.
- Avoid forcing users to interpret raw analytics before summary conclusions.

### `templates/results_entry.html`

- Optimize for speed and low error rate.
- Increase mobile touch targets for finish positions.
- Add stronger completion progress and unsaved-change confidence.

### `templates/afl.html` and `templates/mma.html`

- Bring these into the same intelligence system rather than allowing them to feel like separate mini-products.
- Reuse the same decision-card pattern with sport-specific labels.

### `templates/admin.html`

- Reduce visual emphasis on routine admin actions.
- Standardize table action menus and destructive states.
- Separate user management from component management more clearly.

### `templates/404.html`, `templates/500.html`, and `admin/templates/admin/betfair_mapping.html`

- Bring these into the shared dark shell or at least visually align them with the product.
- Add clear recovery actions and support/admin context.

## Design Principles For The Overhaul

1. **Do not break behavior.** Keep IDs, names, form actions, endpoints, Jinja variables, and existing JavaScript function contracts unless a targeted refactor is planned.
2. **Answer first, evidence second.** Users should see the recommendation before the supporting data.
3. **One primary action per view.** Reduce decision paralysis.
4. **Use color semantically.** Purple is brand/model intelligence, green is positive edge/success, amber is caution/risk, red is danger/negative, blue is neutral info.
5. **Density is earned.** Dense expert data should be available, but summary and progressive disclosure should lead.
6. **Mobile is a separate presentation mode.** Do not rely only on shrinking desktop tables.
7. **Trust is part of the product.** Results, ROI, calibration, and backtests should be surfaced in context.
8. **Every sport should feel like the same product.** Racing, AFL, and UFC can differ in content but should share layout, hierarchy, and interaction models.

## Immediate Next Step

Create the first version of the design system without changing page behavior:

1. `static/css/tokens.css`
2. `static/css/components.css`
3. `docs/design-system.md`
4. A standardized page header component/macro
5. A reusable betting decision card pattern

After that, redesign the Dashboard, Best Bets, and View Meeting pages first because they have the greatest impact on perceived value, trust, and day-to-day retention.
