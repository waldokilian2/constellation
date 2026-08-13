# Task 04 — Compare Mode UX Overhaul (Topology & Orbs)

> Branch: `feature/compare-mode-ux` · Status: implemented (awaiting review)
> Area: frontend (`web/src/app.jsx`, `web/src/styles.css`, `tests/e2e/diff-compare.spec.js`)
> No engine changes. Deterministic graph pipeline is untouched.

## Problem (design analysis)

The graph-diff feature (#13) shipped three overlapping ways to say the same
thing, and they fight each other:

1. **Redundancy.** The compare banner (`.compare-bar`, `app.jsx:574-633`)
   repeats the project-card chips and the canvas badges. "~1 changed entry
   point" appears on the project card, in the banner, *and* on the repo badge.
2. **Obstructed data.** The verbose badge text ("+1 new entry point",
   "~1 changed entry point", `app.jsx:1427-1429`) is wider than the data it
   annotates. In the orbs view it sits on top of the repo ring that shows the
   entry-point **count** — the label obscures the number it is modifying.
3. **Undefined symbols.** The previous iteration tried bare glyphs ("~1").
   That was correctly called out as ambiguous: `~` has no meaning without a
   key. The current fix (full sentences everywhere) traded ambiguity for
   clutter. The correct synthesis is the one this task specifies:
   **concise mathematical notation on the canvas + a legend that defines the
   symbols exactly once.**
4. **Two statuses, one corner.** The header already has a green "Up to date"
   pill (`Header`, `app.jsx:382-422`, styles `.meta-pill`, `styles.css:180-230`)
   that is pure decoration. The banner is a second, lower-priority status strip.
   One should own both jobs.
5. **Dead space.** The banner permanently reserves 40px
   (`DIFF_BAR_H`, `app.jsx:574`; stage shrink at `app.jsx:4284-4285,4394`).

> Note: this intentionally **reverses the wording** introduced by commit
> `4476e32` ("make diff change labels explicit"). That commit's goal — clarity —
> is preserved, but achieved via a legend instead of repeated prose. Do not
> re-litigate this; implement the legend.

## Goals

1. Canvas indicators become concise math notation: `+1`, `~1`, `−1`
   (U+2212 minus sign — the codebase already uses it, keep it).
2. One **legend** defines `+ / ~ / −` for the orbs view, visible **only in
   compare mode**; the solar view gets a compact inline key.
3. The compare **banner is deleted**. The green "Up to date" pill becomes the
   single status + compare-mode toggle/indicator.
4. Diff patterns (colors: green added / amber changed / red removed; symbols
   `+ ~ −`; chip spacing) are identical on every screen.

## Current diff-surface inventory (verified line numbers)

| Surface | Location | Current label |
|---|---|---|
| Compare banner (idle) | `app.jsx:583-597` | "Since last scan:" + verbose chips + Compare btn |
| Compare banner (active) | `app.jsx:607-630` | "COMPARING" + snapshot select + summary text |
| Project card chips | `app.jsx:4719-4724` | "+1 new entry point" etc. |
| Orbs repo badge | `app.jsx:1425-1430` | "+1 new" / "~1 changed" / "−1 removed" |
| Flow repo badge | `app.jsx:3343-3348` | same |
| Solar star badge | `app.jsx:1922-1926` | glyph `▲` / `▼` / `~` only |
| Galaxy edge diff mark | `app.jsx:3306-3310` | glyph `▲` / `▼` / `~` |
| Flow card chip | `app.jsx:2991-2993` | "▲ new channels" etc. |
| Path node chip | `app.jsx:2611-2612` | "▲ new" / "~ changed" |
| Exit-point status | `app.jsx:2655` | "▲ new" / "▼ removed" / "~ changed" |
| Edge popup status | `app.jsx:2536` | "▲ new since last scan" |
| Detail panel lines | `app.jsx:2802` | "▲ entry point is new" |
| Removed-strip toggle | `app.jsx:2683` | "▼ N call nodes removed…" |
| Solar removed filter | `app.jsx:1890-1895` | "▼ N removed (hidden)" |
| Galaxy legend (cmp mode) | `app.jsx:660-673` | colored lines, no symbols |
| Header pill | `app.jsx:382-422` | "Up to date" / "Stale" |

## Visual Logic — top-right indicator state machine

The pill replaces `.meta-pill` and owns three pieces of truth:
**git staleness** (`stale` prop), **graph diff** (`diffInfo`), and
**compare mode** (`compareMode`). It is a `<button>`.

| State | Condition | Render |
|---|---|---|
| **S0 · no history** | `diffInfo` null OR `(diffInfo.snapshots||[]).length === 0` | green dot + "Up to date" (or amber "Stale"). No diff segment. Not clickable. |
| **S1 · clean** | snapshots exist, `!diffHasChanges(diffInfo)` | green "Up to date" + dim segment `· no changes since last scan`. Clickable → S3. |
| **S2 · dirty** | snapshots exist, `diffHasChanges(diffInfo)` | green "Up to date" + **amber** segment `| View changes →` (soft pulse, nudge arrow). Clickable → S3. Hover tooltip = `diffSummaryText(diffInfo)`. |
| **S3 · comparing** | `compareMode === true` | segment 1: "COMPARING" (green, `.compare-title` styling); segment 2: `✕ exit` (click → S1/S2). Pill border glows green. |
| **S4 · stale + dirty** | `stale && diffHasChanges(diffInfo)` | segment 1: amber "Stale" (existing `.status-stale` colors); diff segment unchanged from S2. |
| **S5 · stale + comparing** | `stale && compareMode` | "Stale" + "COMPARING" + `✕ exit`. |

Transitions: `click pill (S1|S2) → S3` · `click ✕ exit (S3) → S1|S2` ·
`rescan completes → refetch diff → S0|S1|S2` · `project switch → S0`
(existing reset effect, `app.jsx:4177`).

```mermaid
stateDiagram-v2
    [*] --> S0_noHistory: project opened, no snapshots
    S0_noHistory --> S1_clean: first rescan creates snapshot
    S1_clean --> S2_dirty: source change + rescan
    S2_dirty --> S3_comparing: click pill
    S1_clean --> S3_comparing: click pill
    S3_comparing --> S1_clean: click exit
    S3_comparing --> S2_dirty: click exit
    S2_dirty --> S1_clean: rescan (no net change)
    S1_clean --> S0_noHistory: never (snapshots are append-only)
    state S4_staleDirty <<join>>: git-stale overlay of S2
    state S5_staleComparing <<join>>: git-stale overlay of S3
```

"Stale" is an **overlay** on the status segment, orthogonal to diff state.

## Component Specifications

### 1. `ComparePill` (new component, replaces the pill in `Header`)

Location: define next to `Header` (`app.jsx:382`). `Header` keeps its current
API surface; `App` passes through new props.

```jsx
function ComparePill({ stale, generatedAt, diffLatest, comparing, snapshots, onToggleCompare }) {
  const hasHistory = !!(snapshots && snapshots.length > 0);
  const hasChanges = !!diffLatest && diffHasChanges(diffLatest);
  const canCompare = hasHistory;
  const statusCls = stale ? "stale" : "ok";
  const statusLabel = stale ? "Stale" : "Up to date";
  const diffTooltip = hasChanges && diffLatest ? diffSummaryText(diffLatest) : "";
  return (
    <div className="meta-right">
      <button
        type="button"
        className={"compare-pill status-" + statusCls + (comparing ? " comparing" : "") + (canCompare && !comparing ? " can-toggle" : "")}
        onClick={canCompare || comparing ? onToggleCompare : undefined}
        disabled={!canCompare && !comparing}
        aria-pressed={comparing}
        title={(comparing ? "" : generatedAt ? "Last scanned: " + generatedAt + (diffTooltip ? "\n" + diffTooltip : "") : diffTooltip)}
      >
        <span className="seg seg-status">
          <span className="status-dot" />
          <span className="status-label">{comparing ? "COMPARING" : statusLabel}</span>
        </span>
        {canCompare && !comparing && (
          <span className={"seg seg-diff" + (hasChanges ? " st-diff" : "")}>
            <span className="seg-sep">|</span>
            {hasChanges ? (
              <span className="seg-action">
                View changes <span className="seg-arrow" aria-hidden="true">→</span>
              </span>
            ) : (
              "no changes"
            )}
          </span>
        )}
        {comparing && <span className="seg seg-exit">✕ exit</span>}
      </button>
    </div>
  );
}
```

> **Snapshot selector removed.** Compare mode always compares against the
> latest previous snapshot; the `compare-select` dropdown (and its
> `compareTs`/`compareInfo` state plumbing) was deleted in a follow-up. The
> pill is the only compare control. `snapshots` stays as a prop purely to
> gate `canCompare` (history exists).

Wiring (`App`, around `app.jsx:4375-4380`): pass
`diffLatest={diffInfo}` `comparing={compareMode}`
`snapshots={diffInfo ? (diffInfo.snapshots || []) : []}`
`onToggleCompare={() => (compareMode ? exitCompare() : enterCompare())}`.

`Header` renders `<ComparePill … />` inside `<div className="meta">`.

**Accessibility**: real `<button>`, `aria-pressed`, `disabled` in S0,
visible focus ring (`:focus-visible` outline in the cyan accent, matching
`.mode-btn`).

**Hover behavior change**: the old pill swapped its label for the scan date
on hover (`.pill-date` spacer + absolute overlay, `styles.css:222-230`). That
machinery is deleted; the date moves into the `title` tooltip. No layout jump.

### 2. Legend — diff section defines the symbols

`Legend` (`app.jsx:636-677`) keeps its current `diff={!!cmp}` gate
(already context-aware: only in compare mode). Replace the three colored-line
rows with **symbol chips**:

```jsx
{diff && (
  <>
    <div className="legend-sep">Since last scan</div>
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
  </>
)}
```

The legend lives in the orbs (galaxy) view only (`<Legend … diff={!!cmp} />`,
`app.jsx:1485`) — satisfies the "Orbs view legend" requirement with zero new
mount points.

### 3. Concise canvas notation (symbol + count)

The legend carries the definition; badges carry the data. Every badge keeps
its `title` tooltip (spell-out, hover-only — clarity without clutter):

| Surface | New label | Tooltip (keep) |
|---|---|---|
| Orbs repo badge (`app.jsx:1427-1429`) | `+{n}` `~{n}` `−{n}` | "N entry point(s) added/changed/removed since last scan" |
| Flow repo badge (`app.jsx:3345-3347`) | `+{n}` `~{n}` `−{n}` | same |
| Star badge (`app.jsx:1924`) | `+` `−` `~` | `DIFF_LABELS[status] + " since last scan"` |
| Edge diff mark (`app.jsx:3308`) | `+` `−` `~` | keep `<title>` |

**Glyph unification** (objective 4): the codebase currently mixes `▲/▼/~`
(spatial glyphs) with `+/−/~` (chips). Unify on **`+ / − / ~`** everywhere —
color already encodes meaning, and the legend documents the symbols:

- Flow card chip (`app.jsx:2991-2993`): `▲ new channels` → `+ new channels`,
  `▼ removed channels` → `− removed channels`, `~ changed channels` unchanged.
- Path node chip (`app.jsx:2612`): `▲ new` → `+ new`; `~ changed` unchanged.
- Exit-point status (`app.jsx:2655`): `▲ new`/`▼ removed` → `+ new`/`− removed`.
- Edge popup (`app.jsx:2536`): `▲ new since last scan` → `+ new since last scan`.
- Detail panel (`app.jsx:2802`): `▲ entry point is new` → `+ entry point is new`;
  `▲ this call node is new` → `+ this call node is new`.
- Removed-strip toggle (`app.jsx:2683`): `▼` → `−`.
- Solar removed filter (`app.jsx:1893`): `▼ N removed` → `− N removed`.

**Project-card chips are deliberately unchanged** (`+1 new entry point`,
`app.jsx:4719-4724`): the landing page has no legend and plenty of room;
full sentences are the right choice there. Document this exception in a code
comment so it isn't "fixed" later.

### 4. Solar view inline key

The solar system has stars, not a legend panel. Add a compact key to
`.view-top` (right-aligned, only when `cmp`):

```jsx
{cmp && (
  <span className="diff-key">
    <span className="diff-chip added">+ new</span>
    <span className="diff-chip changed">~ changed</span>
    <span className="diff-chip removed">− removed</span>
  </span>
)}
```

Keeps the existing `· was N entry points before` hint (`app.jsx:1869`).

### 5. Deletions

- `CompareBar` component (`app.jsx:574-633`) — entire function.
- `DIFF_BAR_H` const (`app.jsx:574`).
- `barVisible` / `stageH` (`app.jsx:4284-4285`); `<main className="stage">`
  reverts to the default class with **no inline style** (`app.jsx:4394`); all
  `dims={{ w: dims.w, h: stageH }}` become `dims={dims}` (5 sites, `app.jsx:4400-4527`).
- `bannerDismissed` state + `dismissBanner` + `onDismiss` prop + the
  `setBannerDismissed(false)` call inside `enterCompare` (`app.jsx:4173,4190,4193`).
- CSS block `.compare-bar` … `.compare-x` (`styles.css:2509-2547`), keeping
  `.compare-inline` (still used in solar hint) and repurposing `.compare-title`
  colors for the pill's COMPARING segment.
- `.pill-date` spacer machinery (`styles.css:222-230`), superseded by tooltip.
- `.legend-line.diff*` styles (`styles.css:2599-2601`), replaced by legend chips.

`diffSummaryText` (`app.jsx:65-76`) survives — it feeds the pill tooltip.

## Layout Impact

- **Stage height**: +40px recovered everywhere inside a project (banner gone).
  `GalaxyView`/`SolarSystemView`/`FlowView`/etc. re-center automatically via
  their existing `dims.h` math — no layout code changes beyond the prop swap.
- **Header**: right side gains the pill's diff segment (max ~200px).
  `.meta` is `flex:1` with `min-width:0`; the centered mode-toggle stays
  dead-center because `.hdr-left` and `.meta` remain equal flex tracks.
  Breadcrumbs truncate first on narrow screens (existing ellipsis). No
  structural change. (The snapshot select was later removed — compare mode
  has no extra controls.)
- **Orbs badges**: text shrinks ~60% ("+1 new" → "+1"), eliminating overlap
  with repo rings/labels; badges keep `top:-34px; right:-26px` anchor.
- **Legend**: grows by ~6px (chips are 18px rows vs 3px lines) — it already
  reserves bottom-left space and is pan-canvas exempt (`app.jsx:1143`).
- **Solar view-top**: the `diff-key` wraps with `flex-wrap` (existing) — no
  overlap with star field (`.view-top` is z-index 20, stars live below).

## Styling Guidelines (match the existing aesthetic)

Tokens only: `--panel`, `--border`, `--mono`, `--text-dim`, `--cyan`,
`--topbar-h`; pill radius `999px`; the diff palette from `DIFF_COLORS`
(`app.jsx:20`). No new colors.

```css
/* ── Compare pill (replaces .meta-pill) ── */
.meta-right { display: flex; align-items: center; gap: 8px; }
.compare-pill {
  font-size: 11px; color: var(--text-dim); padding: 4px 6px 4px 10px;
  border: 1px solid var(--border); border-radius: 999px;
  background: rgba(255,255,255,.03);
  display: inline-flex; align-items: center; gap: 8px;
  white-space: nowrap; cursor: default;
}
.compare-pill.can-toggle { cursor: pointer; }
.compare-pill.can-toggle:hover { border-color: var(--border-strong); background: rgba(255,255,255,.06); }
.compare-pill:focus-visible { outline: 1px solid var(--cyan); outline-offset: 2px; }
.compare-pill:disabled { opacity: .9; cursor: default; }
.seg { display: inline-flex; align-items: center; gap: 6px; }
.seg-sep { color: var(--text-faint); opacity: .6; }
/* status segment colors — reuse .meta-pill.status-ok / .status-stale rules */
.compare-pill.status-ok .seg-status { color: #4ade80; }
.compare-pill.status-ok .status-dot { background: #4ade80; box-shadow: 0 0 8px rgba(74,222,128,.8); }
.compare-pill.status-stale .seg-status { color: #fbbf24; }
.compare-pill.status-stale .status-dot { background: #fbbf24; box-shadow: 0 0 8px rgba(251,191,36,.8); }
/* diff segment: amber = attention (changed palette) */
.compare-pill .seg-diff { color: var(--text-dim); font-size: 10.5px; }
.compare-pill .seg-diff.st-diff {
  color: #fbbf24; font-family: var(--mono);
  animation: diffSegPulse 3s ease-in-out infinite;
}
@keyframes diffSegPulse { 50% { text-shadow: 0 0 8px rgba(251,191,36,.5); } }
/* comparing state — reuse .compare-title styling */
.compare-pill.comparing { border-color: rgba(74,222,128,.45); background: rgba(74,222,128,.08); box-shadow: 0 0 14px rgba(74,222,128,.25); }
.compare-pill.comparing .seg-status { color: #4ade80; letter-spacing: .14em; font-weight: 700; font-size: 10px; }
.compare-pill .seg-exit { color: var(--text-dim); font-size: 10.5px; padding: 1px 6px; border-radius: 999px; }
.compare-pill .seg-exit:hover { color: #fca5a5; background: rgba(248,113,113,.12); }

/* ── Legend diff chips ── */
.legend .diff-chip { padding: 0 7px; font-size: 10px; line-height: 18px; min-width: 26px; justify-content: center; box-shadow: none; }
.legend .diff-chip + span { color: var(--text-dim); }

/* ── Solar diff key ── */
.diff-key { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-left: auto; }
```

Keep `.diff-chip` base (`.diff-chip.added/.changed/.removed` and
`.repo-diff-badge .diff-chip` shadow) untouched — every surface reuses them.

## Implementation Steps (ordered)

1. **Add `ComparePill`** + `Header` prop pass-through + `App` wiring. Delete
   `.meta-pill`/`.pill-date` CSS, add pill CSS. *(Banner still present.)*
2. **Delete the banner**: `CompareBar`, `DIFF_BAR_H`, `barVisible`, `stageH`,
   `bannerDismissed`/`dismissBanner`, `.compare-bar` CSS, `main.stage` inline
   style, `dims` prop swap (5 sites).
3. **Legend symbol chips** in `Legend` + `.legend .diff-chip` CSS; delete
   `.legend-line.diff*` rules.
4. **Concise badges** (orbs + flow repos) and **glyph unification** across all
   surfaces listed in the table above, with the project-card exception comment.
5. **Solar `diff-key`** in `SolarSystemView` `.view-top`.
6. `npm run build`, then update `tests/e2e/diff-compare.spec.js` (below), run
   the e2e suite against `python -m uvicorn server:app --port 8765` and
   `.venv\Scripts\python.exe tests/run_tests.py` (expect the same 4
   pre-existing failures: `test_conversation_title`, 3× `test_mermaid_validator`).

## Test Plan — e2e updates (`tests/e2e/diff-compare.spec.js`)

| Line | Old assertion | New assertion |
|---|---|---|
| ~112 | `.compare-bar` count 0 | pill visible with text "Up to date", no `.seg-diff`, no `.compare-select` |
| ~121 | `bar.locator(".compare-text")` "Since last scan" | `.compare-pill .seg-diff` → "View changes →" |
| ~123 | `bar.locator(".diff-chip.added")` "+1" | pill `.seg-diff` text (summary lives in pill/tooltip now) |
| ~124 | `.compare-action` "Compare" | `.compare-pill` has class `can-toggle` |
| `enterProjectCompare` (~66) | click `.compare-action`, expect `.compare-bar.active .compare-title` "COMPARING" | click `.compare-pill`, expect `.compare-pill.comparing` + `.seg-status` "COMPARING" |
| ~138 | repo badge "+1 new" | repo badge "+1" **and** title attr contains "added since last scan" |
| ~148 | star badge "▲" | star badge "+" |
| ~164 | pv-diff-chip "▲ new" | pv-diff-chip "+ new" |
| ~181 | `.compare-versions select` | *(removed — no snapshot selector exists; assert `.compare-select` count 0)* |
| ~185 | `.compare-bar .compare-text` "+1" | `.repo-diff-badge .diff-chip.added` "+1" |
| — | *(new)* | legend in compare mode shows `.legend .diff-chip.changed` with text "~" |
| — | *(new)* | clicking `✕ exit` leaves compare mode; no `.compare-select` remains |
| ~130 | project card "+1 new entry point" | **unchanged** (deliberate exception) |

## Acceptance Criteria

- [x] No `.compare-bar` element exists; stage height is `calc(100% - var(--topbar-h))` in all project views.
- [x] Orbs badges read `+1` / `~1` / `−1` only; tooltips spell them out.
- [x] Galaxy legend shows `+ added` / `~ changed` / `− removed` chips **only in compare mode**.
- [x] Solar view shows the `diff-key` only in compare mode.
- [x] Pill states S0–S5 render per the table; pill is the only compare toggle.
- [x] `+ / − / ~` symbols are consistent on every surface (project-card prose excepted).
- [x] e2e suite passes; Python suite has no new failures.
- [x] Rebuild via `npm run build`; verify against the running server at :8765.

## Risks & Mitigations

- **Symbol-only badges rely on the legend** — mitigate with preserved
  `title` tooltips on every badge.
- **Header width on small screens** — breadcrumb ellipsis already handles
  overflow; the pill is compact; verify at 1280px.
- **e2e coupling** — the spec file is updated in the same commit as the UI
  change (never split).
