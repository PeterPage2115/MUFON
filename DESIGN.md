# Travian Dashboard Design System

## 0. Research Log

- Embedded refs: shortlisted `clickhouse.md` (technical yellow-on-black data console), `raycast.md` (dark command surface), and `sentry.md` (dark operational dashboard) from the curated Layer B index → picked `taste-skill.md` + `clickhouse.md` because their restrained data hierarchy and singular warm accent fit an admin dashboard; ClickHouse's neon volt was softened into Travian field-gold.
- Lazyweb: skipped — this is a local operational surface, not a marketing page; no external product screens are needed and the implementation must remain self-contained.
- Imagen drafts: skipped — the brief requires a live DOM dashboard with no external assets, not an image-first concept.

## 1. Atmosphere & Identity

The dashboard is a quiet field-operations console: dense enough for a developer to scan between jobs, but calm enough to make failures legible. Its signature is a muted gold signal moving through layered charcoal-green surfaces, like torchlight on a stone map table. It borrows ClickHouse's black technical discipline, then trades neon urgency for Travian's weathered amber and forest undertone.

## 2. Color

### Palette

| Role | Token | Value | Usage |
|------|-------|-------|-------|
| Surface / canvas | `--surface-canvas` | `#090b09` | Page background |
| Surface / primary | `--surface-primary` | `#0f130f` | Header and main panel layer |
| Surface / secondary | `--surface-secondary` | `#151b15` | Cards and form groups |
| Surface / elevated | `--surface-elevated` | `#1b231a` | Inputs, focused rows, toast |
| Text / primary | `--text-primary` | `#f1eee1` | Headings and important values |
| Text / secondary | `--text-secondary` | `#c5c2b5` | Body copy and labels |
| Text / muted | `--text-muted` | `#96988c` | Metadata and helper text |
| Text / faint | `--text-faint` | `#8a9080` | Quiet timestamps and separators |
| Border / default | `--border-default` | `#2a3328` | Card and input outlines |
| Border / strong | `--border-strong` | `#3c4b38` | Focus and active containment |
| Accent / gold | `--accent-gold` | `#d1a84a` | Primary action and live signal |
| Accent / gold-soft | `--accent-gold-soft` | `#e1c675` | Hover and emphasized copy |
| Accent / gold-deep | `--accent-gold-deep` | `#8b672a` | Gold-tinted borders and decoration |
| On accent | `--on-accent` | `#141005` | Text and marks on gold fills (buttons, spinners) |
| Status / success | `--status-success` | `#91bd78` | Completed jobs and healthy state |
| Status / warning | `--status-warning` | `#d6a552` | Warning jobs and caution |
| Status / error | `--status-error` | `#d47769` | Failed jobs and validation errors |
| Status / info | `--status-info` | `#9ba99a` | Informational jobs |

### Rules

- Use one warm accent family: gold carries interaction, focus, and wayfinding; status colors are semantic exceptions only.
- Keep the theme dark throughout. No light section or unrelated cool accent.
- Surface depth comes from tonal shifts plus restrained borders; shadows are soft and background-tinted, never black blobs.

## 3. Typography

### Scale

| Level | Size | Weight | Line height | Usage |
|-------|------|--------|-------------|-------|
| Display | `clamp(2rem, 4vw, 3.5rem)` | 760 | 1.05 | Dashboard title |
| H1 | `clamp(1.75rem, 3.4vw, 2.5rem)` | 720 | 1.1 | Page-level title |
| H2 | `1.125rem` | 700 | 1.3 | Card titles |
| Body | `1rem` | 400 | 1.55 | Default copy |
| Body / small | `0.875rem` | 400 | 1.45 | Form labels and values |
| Label | `0.8125rem` | 650 | 1.4 | Input labels, card intro |
| Caption | `0.75rem` | 650 | 1.35 | Overlines and metadata |
| Micro | `0.6875rem` | 650 | 1.3 | Uppercase labels, log level words |
| Metric | `clamp(1.4rem, 2.6vw, 2rem)` | 760 | 1.05 | Status figures |

### Font Stack

- Primary: `ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`.
- Mono: `ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace`.
- No external font or CDN is loaded; the interface must render consistently offline.

### Rules

- Body text stays at or above 14px. Uppercase labels use wide tracking sparingly.
- Numbers, timestamps, and timezone strings use the mono stack for reliable scanning.
- Labels sit above controls; helper and error text sits below the relevant control.

## 4. Spacing & Layout

### Base Unit

All spacing derives from a 4px base unit: `--space-1` 4px, `--space-2` 8px, `--space-3` 12px, `--space-4` 16px, `--space-5` 20px, `--space-6` 24px, `--space-8` 32px, `--space-10` 40px, `--space-12` 48px.

### Grid

- Content limiter: `min(100% - 32px, 1240px)` with 16px mobile margins and 32px desktop margins.
- **Three-view shell**: top-level tabs switch between `Intelligence` (analysis tabs), `Overview` (Status / Job log) and `Operations` (Actions / Settings, admin-only). One view is visible at a time; each view owns its tablist.
- Desktop composition: a 12-column CSS grid on Overview; Status spans 12, Settings spans 8, Actions spans 4, Job log spans 12. At 1100px and below Settings and Actions become two equal columns (span 6); below 680px every card spans 1 column.
- Breakpoints: two-column switch at 1100px, single column at 680px (no breakpoint tokens in CSS; media queries are raw by design).
- The document owns page scroll. Nested scroll owners are bounded and described: Job log (`max-height: 360px`), standings options list, event lists, data tables, and the mobile tab bar. A table may own a horizontal scroll on narrow viewports instead of dropping columns.

## 5. Components

### Dashboard Card

- **Structure**: `article.card` with a header row (`overline`, title, optional status mark) and content body.
- **Variants**: status, settings, actions, job log; `.card--status` gets the gold signal rail.
- **Spacing**: 24px desktop / 20px mobile card padding; 24px grid gap.
- **States**: default, loading content, empty content, error content (message + visible `Retry` action), focused descendants.
- **Accessibility**: semantic heading hierarchy; no card is the sole hit target for an action.
- **Motion**: entry fade/translate only; no decorative looping motion.
- **Layout**: CSS grid item; Job log list is a bounded vertical scroll owner, not the only one (see §4).

### Global Connection State

- One persistent banner (`#global-status-banner`) reflects `online | degraded | offline`, derived from the last `/api/status` result: success sets `online` and records `last_good_load`; failure keeps the last good payload on screen, shows `Connection issue` with the time of the last good read and a Retry action. The dashboard never claims "connected" without a successful `/api/status`.
- A single `Refresh dashboard` button (label `Retry dashboard` after a failure) re-reads status, the active analysis panel, and logs for admins; it does not reload the page and never overwrites a dirty settings form.
- Data lifecycle is `initial load → load on view activation → refresh after action → manual Refresh`. There is no background polling after initial load; freshness never fakes itself up to date.

### Chart Data Table

- Every Chart.js canvas (`renderRegionChart`, `renderStandingsChart`, `renderVillageChart`) has a companion `<details>` `Show data table` containing a semantic table built from the exact chart payload (same dates, values, labels). The canvas gets `aria-describedby` pointing at the table; the table is keyboard/screen-reader reachable by default, may be visually collapsed, and never replaces the data. Tooltips are never the only way to read a chart.

### Metric Tile

- **Structure**: label, numeric value, optional unit/description.
- **Variants**: population (gold accent), neutral counts, empty (`—`).
- **Spacing**: 12px internal gap; 2-column grid on narrow cards.
- **States**: default, refreshed (brief opacity/translate feedback), empty.
- **Accessibility**: values remain text, never color-only; labels are explicit.
- **Motion**: refresh feedback uses opacity and transform only.

### Field Group

- **Structure**: `label`, input/select, helper text, inline error slot.
- **Variants**: text, number, timezone select, color compound field, tag list textarea, optional identifier.
- **Spacing**: 8px label/control gap; 16px between groups.
- **States**: default, hover, focus, valid, invalid, disabled while saving.
- **Accessibility**: every control has a visible label and `aria-describedby`; errors use `role="alert"` and `aria-live` feedback.
- **Motion**: error and success feedback fades in; no layout animation.

### Action Button

- **Structure**: semantic `button` with label and loader slot.
- **Variants**: primary gold, secondary forest, quiet outline.
- **Spacing**: minimum 44px block touch target; 8px × 16px padding.
- **States**: default, hover, active, focus-visible, disabled, loading, success/error via toast.
- **Accessibility**: focus-visible ring, disabled during request, `aria-busy` while loading.
- **Motion**: 120ms press transform; loader is an opacity-safe inline indicator.

### Log Entry

- **Structure**: grid row of timestamp, job, level word, message.
- **Variants**: error, warning, info; level word is always shown (`ERR`/`WRN`/`INF`) so level is never communicated by color alone.
- **Spacing**: 2px row gap; 8px × 12px cell padding; 4 columns (128px ts, 84px job, 40px level, fluid message) at desktop, compact 72/56/32 with 6px gaps from 1100px down (two-column cards are ~half width — the full grid would squeeze the message column to 0 below ~780px).
- **States**: entering, stale-removed; hover is intentionally inert (rows are not interactive).
- **Accessibility**: `aria-live="polite"` on the list announces new rows; messages use `overflow-wrap: anywhere`.
- **Motion**: entry fade/translate only; removal is instant.

### Toast

- **Structure**: one fixed `#toast-container`; toasts stack upward, newest on top.
- **Variants**: success, error, info.
- **Spacing**: 16px inset from viewport edge; 12px × 16px internal padding.
- **States**: entering, visible, dismissing; auto-dismiss after 4 seconds.
- **Accessibility**: `aria-live="polite"` for success/info and `aria-live="assertive"` while an error toast is present (the container attribute is toggled around error insertion).
- **Motion**: transform + opacity only; reduced-motion path is an instant state change.

## 6. Motion & Interaction

| Token | Duration | Easing | Usage |
|-------|----------|--------|-------|
| `--motion-micro` | 120ms | `ease-out` | Press, focus, hover |
| `--motion-standard` | 220ms | `ease-in-out` | Toast and field feedback |
| `--motion-emphasis` | 480ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Initial card entry |

- The mechanism is adapted from the beui `button`, `input`, `animated-toast-stack`, and `loader` patterns: async actions visibly swap to loading, then resolve through a single contextual toast.
- Only `transform`, `opacity`, and color/filter transitions are used; layout dimensions never animate.
- Respect `prefers-reduced-motion: reduce` by removing entry/press transforms and keeping state changes immediate.
- Data refresh is manual and explicit: initial load, refresh on view activation, refresh after an action, and the `Refresh dashboard` button. Job log caption reads `Manual refresh · UTC`; there is no periodic refresh timer after initial load.

## 7. Depth & Surface

**Strategy: mixed tonal-shift + borders.** The canvas is the deepest layer, cards lift one tonal step, and inputs lift another. A 1px border reinforces containment; a small warm inset highlight gives the header and focused controls physical edge without a glossy glass effect. Cards use a soft `0 14px 40px` shadow only where it separates the dashboard from the canvas.

## 8. Accessibility Constraints & Accepted Debt

### Constraints

- WCAG 2.2 AA target: body contrast floor 4.5:1, large text 3:1, visible focus on all controls, full keyboard reachability, semantic landmarks, explicit labels, and reduced-motion support.
- Never communicate job level or form validity by color alone; use text labels, icons made from CSS shapes, and error copy.
- At 375px the primary content must reflow to one readable column with no horizontal page scroll. Long tags, timestamps, and API error strings wrap safely.

### Accepted Debt

| Item | Location | Why accepted | Owner / Exit |
|------|----------|--------------|--------------|
| System font metrics vary by OS | `static/style.css` | User explicitly forbids external fonts/CDNs; system stack is the most reliable offline choice | Revisit if dashboard gains a packaged font asset |
| No component showcase route | `static/` | Plan pins a single-page, no-build dashboard; each primitive is exercised in the live page and interaction QA | Add a dev-only showcase only if the surface grows |
