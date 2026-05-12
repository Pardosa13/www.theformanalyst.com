# The Form Analyst Design System (v1)

## Scope
This first pass introduces non-breaking design primitives only. Existing routes, IDs, forms, endpoints, Jinja variables, and JavaScript contracts remain unchanged.

## Files
- `static/css/tokens.css` — semantic tokens and compatibility aliases.
- `static/css/components.css` — reusable page header and betting decision card styles.
- `templates/macros/design_system.html` — Jinja macros for standardized header and decision card markup.

## Token groups
- Brand: `--brand-primary`, `--brand-secondary`, `--brand-glow`
- Surfaces: `--surface-page`, `--surface-card`, `--surface-card-raised`, `--surface-control`, `--surface-critical`
- Text: `--text-primary`, `--text-secondary`, `--text-tertiary`, `--text-inverse`
- Borders: `--border-subtle`, `--border-strong`
- Intent: `--intent-positive`, `--intent-negative`, `--intent-warning`, `--intent-info`, `--intent-model`, `--intent-market`, `--intent-edge`, `--intent-action`
- Layout foundation: spacing, radius, elevation, motion, and z-index tokens

## Compatibility aliases
To avoid breaking existing page-local styles, token aliases remain available for legacy names (for example `--accent`, `--bg-surface`, and `--border-mid`) while new work should prefer semantic tokens.

## Reusable components
### 1) Standard page header
Use macro: `page_header(...)`

Supports:
- Eyebrow/module label
- Page title
- Utility description
- Optional primary and secondary actions
- Optional status chips

### 2) Betting decision card
Use macro: `betting_decision_card(...)`

Standardizes:
- Selection and context
- Confidence
- Model price
- Market price
- Edge
- Risk flags
- Evidence summary
- Optional call-to-action

## Adoption guidance
1. Prefer semantic tokens over hard-coded values in new styles.
2. Reuse macros before introducing new page-level header/card variants.
3. Move repeated page-local styles into `components.css` gradually.
4. Keep behavior stable while migrating visuals.
