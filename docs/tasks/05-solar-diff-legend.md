# Task 05 — Solar View: Bottom-Left Diff Legend (replace the colliding diff-key)

> Branch: `feature/compare-mode-ux` (continue on the same branch) · Status: done
> Area: frontend — `web/src/app.jsx`, `web/src/styles.css`, `tests/e2e/diff-compare.spec.js`
> No engine changes.

## Problem (verified against the current code)

Task 04 added a compact `diff-key` — three chips (`+ new`, `~ changed`,
`− removed`) — to the **solar system ("orbs") view** inside `.view-top`
(`app.jsx`, `SolarSystemView`, the block right after the `view-hint`):

```jsx
{cmp && (
  <span className="diff-key">
    <span className="diff-chip added">+ new</span>
    <span className="diff-chip changed">~ changed</span>
    <span className="diff-chip removed">− removed</span>
  </span>
)}
```

It collides with the **channels panel header**. Root cause, verified in the
CSS stacking:

1. `.view-top` is `position: absolute; left: 0; right: 0; z-index: 20`
   (`styles.css:227-232`) — it spans the **full width**, including the
   right-docked channels panel.
2. `.channels-panel` is `position: absolute; top: 0; right: 0; bottom: 0;
   z-index: 15` (`styles.css`, "Channels panel" section). Its header
   (`.cp-head`) renders the repo name — `.cp-repo` shows
   "java-ee-order-service" at the panel's top-right corner (`app.jsx:2204-2206`).
3. `.diff-key` has `margin-left: auto` (`styles.css:2552`), so inside the
   full-width `.view-top` flex row it is pushed to the **far right** — exactly
   on top of the panel header. Because `.view-top`'s z-index (20) beats the
   panel's (15), the chips paint **over** the repo name.
4. Secondary issue: `.view-top` has `flex-wrap: wrap`; on narrower viewports
   the chips wrap onto a second line and can sit over the star field.

The galaxy (topology) view already solves this correctly: its diff legend
lives in the bottom-left `.legend` glass panel, stacked above the zoom
controls. The solar view should mirror that pattern.

## Requirement

Replace the solar `.diff-key` with a **bottom-left glass legend panel**,
rendered **only in compare mode** (`cmp` truthy), visually identical to the
galaxy view's "Since last scan" legend section:

- Title: `SINCE LAST SCAN` (same uppercase micro-label as `.legend-title`).
- Three rows, each a symbol chip + word: `+` added · `~` changed · `−` removed.
- Position: bottom-left corner, stacked above the zoom controls, **outside**
  the pan/zoom canvas (fixed overlay — it must not pan or scale with the
  star field).

## Component Specification

### 1. JSX — remove the diff-key

In `SolarSystemView` (`app.jsx`), delete the entire `{cmp && <span
className="diff-key">…</span>}` block from `.view-top`.

**Keep** the `compare-inline` span in `.view-hint`:

```jsx
{cmp && (
  <span className="compare-inline">
    · was {cmp.oldCount} {cmp.oldCount === 1 ? "entry point" : "entry points"} before
  </span>
)}
```

It lives at the top-LEFT (no collision) and carries useful delta context.

### 2. JSX — add the legend

Add a `SolarDiffLegend` component **immediately above** `SolarSystemView`
(same file, right after the `GalaxyView`/`diffStatus` helpers) so it is easy
to reuse or extract later:

```jsx
/* ---------------- Solar diff legend (compare mode only) ---------------- */
// Bottom-left glass panel mirroring the galaxy legend's "Since last scan"
// section. Rendered outside the pan canvas so it stays fixed while the star
// field pans/zooms. Gate: compare mode only (cmp truthy).
function SolarDiffLegend({ cmp }) {
  if (!cmp) return null;
  return (
    <div className="legend solar-legend glass" role="note" aria-label="Diff legend">
      <div className="legend-title">Since last scan</div>
      <div className="legend-item">
        <span className="diff-chip added">+</span>
        <span>added</span>
      </div>
      <div className="legend-item">
        <span className="diff-chip changed">~</span>
        <span>changed</span>
      </div>
      <div className="legend-item">
        <span className="diff-chip removed">−</span>
        <span>removed</span>
      </div>
    </div>
  );
}
```

Mount it inside `SolarSystemView`'s returned `.solar` div, **after** the
`.filters` block and **before** the `.canvas.solar-canvas` element:

```jsx
<div className="solar">
  <div className="view-top">…</div>
  <div className="filters">…</div>
  <SolarDiffLegend cmp={cmp} />
  <div className="canvas solar-canvas pan-canvas" …>…</div>
  <ChannelsPanel … />
  {pz.zoomControls}
</div>
```

Notes for the implementer:

- **Gate** is `cmp` (compare mode active), not "has changes" — same semantics
  as the galaxy legend, which shows its diff section whenever compare mode is
  on. If the diff is empty the rows are still meaningful ("nothing changed" is
  implied by empty badges elsewhere); do not add extra logic.
- The legend is a **direct child of `.solar`**, NOT inside `.canvas-world`:
  unlike the galaxy legend (which lives inside the canvas and pans with it),
  this one must be a fixed overlay. This also means **no change** to the
  `usePanZoom(".star, .star-label, .channels-panel")` selector (`app.jsx`,
  `SolarSystemView` top) — the legend is outside the canvas so it never
  intercepts pan gestures.
- `role="note"` + `aria-label="Diff legend"` for screen readers; rows are
  plain readable text.

### 3. CSS

Reuse the existing `.legend` base class (glass panel, absolute bottom-left,
z-index 15, `styles.css:449-463`) plus the `.legend .diff-chip` chip sizing
already shipped in task 04. Add one small override rule next to the legend
rules:

```css
/* Solar view: diff legend stacks above the zoom controls (same slot as the
   galaxy legend) but is a fixed overlay — outside the pan canvas. */
.legend.solar-legend {
  bottom: 84px;   /* same as .legend default, but keep explicit: above zoom-controls (bottom:24px) */
  left: 24px;
  z-index: 15;    /* above the canvas, below .view-top (20) and .zoom-controls (50) */
  pointer-events: none; /* purely informational; never blocks star clicks/pan */
}
```

Delete the now-unused rule:

```css
.diff-key { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-left: auto; }
```

(`styles.css:2552`)

If `.legend .diff-chip` sizing rules from task 04 are scoped as
`.legend .diff-chip { … }` they apply to `.solar-legend` automatically —
verify they are not scoped to a galaxy-only class before relying on them.

## Layout Impact

- **`.view-top` row**: shrinks to the `view-hint` alone. No vertical shift of
  any canvas element — `.view-top`, `.filters`, and the legend are all
  absolute overlays; the star field geometry (`W`, `H`, `cx`, `cy`) is
  untouched.
- **Channels panel**: fully unobstructed again — its header (repo name +
  channel counts) gets its space back.
- **Bottom-left corner**: zoom controls remain at `bottom: 24px` (z 50); the
  legend sits above them at `bottom: 84px`, the exact slot the galaxy legend
  uses. On short viewports the legend is ~150px tall; acceptable (the galaxy
  legend has the same footprint plus type rows).
- **Star occlusion**: the legend may cover stars panned to the bottom-left.
  This is the accepted tradeoff of the galaxy legend too; z-index 15 keeps it
  above stars (star z-index 5, hover 12). Do not try to make it shrink or
  fade on pan — keep it simple and consistent.
- **Narrow viewports**: no more flex-wrap collision — the legend is out of the
  flow entirely.

## Styling Guidelines (consistency with the design language)

- Reuse tokens only: `.glass` (`--panel`, `--border`, blur), `--text-faint`
  for the title, `--mono` via `.diff-chip`, the diff palette already in
  `DIFF_COLORS` (`added` green `#4ade80`, `changed` amber `#fbbf24`, `removed`
  red `#f87171`). No new colors, no new shadows beyond what `.glass` and
  `.diff-chip` already provide.
- Row layout must be identical to the galaxy legend rows: chip first, label
  second, `gap: 8px`, `font-size: 11px`, `color: var(--text-dim)`
  (`.legend-item`).
- Title must match `.legend-title`: `10px`, `letter-spacing: .14em`,
  `text-transform: uppercase`, `color: var(--text-faint)`.
- Chips in the legend are **symbol-only** (`+`, `~`, `−` — the `−` is U+2212,
  the same minus the codebase uses everywhere). The words "added / changed /
  removed" sit beside them as plain legend text, NOT inside the chips.
- Panel: `padding: 12px 14px; border-radius: 12px;` (inherited from `.legend`).

## Test Plan — e2e updates (`tests/e2e/diff-compare.spec.js`)

The spec already drills into the solar view in compare mode
(`enterProjectCompare` → `drillToOrderService`, ~lines 66-91) and asserts the
galaxy legend chips (~169-173). Add/update:

1. **New assertion** in the solar system test (~184) or a new test right after
   the galaxy legend test:
   ```js
   test("solar system: bottom-left diff legend in compare mode", async ({ page }) => {
     await enterProjectCompare(page);
     await drillToOrderService(page);
     const legend = page.locator(".legend.solar-legend");
     await expect(legend).toBeVisible();
     await expect(legend.locator(".legend-title")).toHaveText("Since last scan");
     await expect(legend.locator(".diff-chip.added")).toHaveText("+");
     await expect(legend.locator(".diff-chip.changed")).toHaveText("~");
     await expect(legend.locator(".diff-chip.removed")).toHaveText("−");
   });
   ```
2. **Negative assertion** (compare mode OFF): in the "fresh project" test
   (~107) or a dedicated one, `await expect(page.locator(".legend.solar-legend")).toHaveCount(0)`.
3. **Regression guard** for the fix: `await expect(page.locator(".diff-key")).toHaveCount(0)`
   anywhere the solar view is visited; and assert the channels panel repo name
   is not overlapped indirectly by checking `.cp-repo` remains visible after
   entering compare mode:
   ```js
   await expect(page.locator(".channels-panel .cp-repo")).toBeVisible();
   ```
4. Existing galaxy legend assertions (~166-173) must stay green — the galaxy
   legend is unchanged by this task.

Run: server up (`python -m uvicorn server:app --port 8765`), then
`cd tests/e2e && npm test -- diff-compare.spec.js`. Backend sanity:
`.venv\Scripts\python.exe tests/run_tests.py` (expect the same 4 pre-existing
failures: `test_conversation_title` + 3× `test_mermaid_validator` — unrelated).

## Implementation Steps (ordered)

1. Delete the `.diff-key` JSX block in `SolarSystemView`.
2. Add `SolarDiffLegend` component above `SolarSystemView`; mount it between
   `.filters` and `.canvas`.
3. Add `.legend.solar-legend` CSS; delete `.diff-key` CSS.
4. Verify the `.legend .diff-chip` rules apply (not galaxy-scoped).
5. Update the e2e spec per the test plan.
6. `npm run build`; reload `http://localhost:8765`, enter compare mode on
   "Java EE", drill into `java-ee-order-service`, confirm: legend bottom-left,
   channels panel title unobstructed, star field pans without moving the
   legend.
7. Commit on `feature/compare-mode-ux`.

## Acceptance Criteria

- [x] No `.diff-key` element remains anywhere in `web/src`.
- [x] In compare mode, the solar view shows a bottom-left `.legend.solar-legend`
      glass panel titled "Since last scan" with rows `+ added`, `~ changed`,
      `− removed`.
- [x] The legend is not visible outside compare mode (any solar view visit
      without compare mode).
- [x] The channels panel repo name ("java-ee-order-service") is never covered
      by diff chips in compare mode.
- [x] The legend does not pan or zoom with the canvas; stars remain clickable
      underneath the rest of the canvas.
- [x] Galaxy legend and all task-04 surfaces are visually unchanged.
- [x] e2e `diff-compare.spec.js` passes; no new Python test failures.

## Risks & Mitigations

- **Legend covering stars** — accepted, consistent with galaxy view; mitigated
  by `pointer-events: none` so no interaction is blocked.
- **Rule reuse assumptions** — verify `.legend .diff-chip` is generic before
  shipping; if galaxy-scoped, widen the selector rather than duplicating chip
  styles.
- **z-index drift** — `.view-top` (20) must stay above the legend (15) so the
  top-left hint never underlaps it; document both values with comments in CSS.
