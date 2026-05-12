# The Form Analyst Design System (v1)

## Scope
This is a non-breaking design-system foundation for production use. Existing routes, IDs, forms, endpoints, Jinja variables, and JavaScript contracts remain unchanged.

## Component architecture

### Layer 1: Tokens (`static/css/tokens.css`)
Single source of truth for color, spacing, elevation, motion, and z-index.

### Layer 2: Reusable components (`static/css/components.css`)
Global component styles for:
- Page headers
- Status chips
- Betting decision cards
- Generic loading/empty/error state panels
- Skeleton placeholders and screen-reader utility classes

### Layer 3: Template macros (`templates/macros/design_system.html`)
Presentation primitives with stable API:
- `page_header(...)`
- `betting_decision_card(...)`
- `state_panel(...)`

### Layer 4: Page templates
Pages consume macros and pass data only. Page-local CSS remains temporarily for legacy compatibility while migration continues.

## Props / API design

### `page_header(...)`
**Purpose:** Standardized page intro with title hierarchy and action grouping.

**Props:**
- `title` (required): main heading text
- `description` (optional): one-line summary
- `eyebrow` (optional): module label
- `primary_action` (optional object): `{ href, label, icon? }`
- `secondary_actions` (optional list): `[{ href, label, icon? }]`
- `status_chips` (optional list): `[{ label, intent?, dot? }]`
- `heading_level` (optional): defaults to `h1`
- `id_prefix` (optional): stable heading ID prefix for accessibility

### `betting_decision_card(...)`
**Purpose:** Consistent decision-first betting presentation.

**Props:**
- `selection` (required)
- `context` (required)
- `confidence`, `model_price`, `market_price`, `edge` (optional)
- `risk_flags` (optional list)
- `evidence` (optional list)
- `action` (optional object): `{ href, label, icon? }`
- `badge` (optional object): `{ label, intent? }`
- `aria_label` (optional): accessibility label for region

### `state_panel(...)`
**Purpose:** Reusable loading/empty/error pattern with optional action.

**Props:**
- `state` (optional): `loading | empty | error`
- `title` (required)
- `description` (optional)
- `action` (optional object): `{ href, label, icon? }`
- `aria_live` (optional): defaults to `polite`

## Production-ready implementation details

### Accessibility
- Semantic landmarks (`header`, `section`) and labeled regions
- Optional `aria-live` on state panels
- Screen-reader utility class (`.sr-only`)
- Motion respects `prefers-reduced-motion`

### Responsive behavior
- Header and decision card layouts collapse to single-column on mobile
- Decision metrics adapt from 4 → 2 → 1 columns through breakpoints
- State panels switch from row to stacked layout on smaller widths

### State handling
- **Loading:** `state_panel(state='loading', ...)` and `.ds-skeleton`
- **Empty:** `state_panel(state='empty', ...)` (used on Best Bets)
- **Error:** `state_panel(state='error', ...)` for recoverable failures
- **Missing values:** macros safely display `—` for unset metrics

### Edge-case handling
- Optional props render conditionally (no empty wrappers)
- Supports pages with no actions, no badges, or no status chips
- Stable defaults for chip intent and metrics output

## Usage examples

### 1) Standard page header
```jinja2
{% import "macros/design_system.html" as ui %}

{{ ui.page_header(
  eyebrow='Racing Intelligence',
  title="Today's Best Bets",
  description='Highest-value opportunities in current meetings',
  primary_action={'href': url_for('import_from_api'), 'label': 'Import Meetings', 'icon': 'bi bi-arrow-down-circle'},
  status_chips=[{'label': 'Live Feed', 'intent': 'live', 'dot': true}]
) }}
```

### 2) Betting decision card
```jinja2
{{ ui.betting_decision_card(
  selection='Horse Name',
  context='Randwick · Race 6',
  confidence='High',
  model_price='$4.10',
  market_price='$5.20',
  edge='+6.3%',
  risk_flags=['Late market drift', 'Small sample profile'],
  evidence=['Top sectional rank', 'Positive map setup'],
  badge={'label': 'TOP PICK', 'intent': 'positive'}
) }}
```

### 3) Empty/loading/error states
```jinja2
{{ ui.state_panel(state='loading', title='Loading meeting data...') }}
{{ ui.state_panel(state='empty', title='No bets found', description='Try increasing the window.') }}
{{ ui.state_panel(state='error', title='Could not load prices', action={'href': url_for('best_bets'), 'label': 'Retry'}) }}
```

## Best practices
1. Prefer semantic tokens over hard-coded values in new styles.
2. Keep component APIs data-driven; avoid embedding business logic in markup.
3. Use one primary action per view and keep secondary actions compact.
4. Always render explicit empty/error/loading states for async data sections.
5. Use `aria-live` only where content updates dynamically.
6. Respect reduced-motion preferences for all animated affordances.
7. Add new components in `components.css` + macro API before page-specific styling.
8. Preserve existing IDs/endpoints/JS contracts during migrations.

## Current adoption
- `templates/base.html` loads `tokens.css` and `components.css`.
- `dashboard.html` uses `page_header(...)`.
- `best_bets.html` uses `page_header(...)`, `betting_decision_card(...)`, and `state_panel(...)`.
