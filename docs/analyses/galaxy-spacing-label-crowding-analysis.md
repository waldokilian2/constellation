# Galaxy Layout — Spacing, Squash, and Label Crowding: Findings + Implementation Plan

> Date: 2026-08-14 · Area: `web/src/galaxyLayout.js` + `web/src/app.jsx` GalaxyView/FlowView
> Triggered by: design review feedback — "planets look too squashed / too clustered",
> "no labels should overlap".
> Status: analysis complete, measurements taken against the real seeded graphs; fix NOT yet implemented.

## 0. Verdict summary

The feedback is **justified, with evidence** (see §2): the constellation is not literally
overlapping, but it is packed to its legal minimum everywhere, several edge curves pass
**under unrelated connected planets** (up to 50.7px deep), one edge-label pill rests
**4.6px** from an unrelated orb, and the 26-repo isolated field has **5 genuine
label-vs-label overlaps** plus an 11.8px label-to-orb margin. On laptop viewports the
pill-bearing map overflows the viewport with no initial fit, so the first impression is a
zoomed-in, cropped middle of the map.

**Leveraging the flow view: yes for its edge/label avoidance and fit mechanics — no for
its placement core.** The reasoning is in §4; the three specific mechanisms worth
porting are (1) skip-edge **arc-over routing** for edges that would cross unrelated
nodes, (2) the **pill lift-above fallback** (a perpendicular escape when sliding along
the curve fails), and (3) the **fit-to-content initial viewport**. The flow view's
fixed depth-column grid does NOT transfer — the galaxy is a general graph with cycles
(order↔inventory, user↔recommendation), which a grid cannot express; force relaxation
stays as the placement core, but its objective must change from "minimum legal packing"
to "comfortable even spacing" (§5-P2).

## 1. What the current implementation does (relevant excerpts)

`layoutGalaxy(repos, epCount, edges, W, H, pillWFor)` in `web/src/galaxyLayout.js`:

- **Placement**: golden-angle spiral seed → 160 force-relaxation iterations (pairwise
  repulsion below a hard floor, springs along edges, weak centering) → 60 settle
  iterations (repulsion only) → viewport fit (scale ≤ 1) → post-fit collision resolver
  (≤200 passes) → re-center+round → resolve → re-center.
- **Floors**: `minDist(i,j) = r_i + r_j + clearance(i,j)` (`galaxyLayout.js:353-358`).
  `clearance` = `max(NODE_GAP=90, pillW) + EDGE_BREATHING=64` for pairs inside a
  constellation, `NODE_GAP * 1.55 = 139.5` for isolated pairs (`:330-336`).
- **Springs**: rest length = `r_a + r_b + clearance(a,b)` — **exactly the collision
  floor** (`:413`). Springs only pull when longer than rest (`:415-419`).
- **Pills**: `placeEdgePills` (`:159-221`) places each pill by sliding along its curve
  through 11 fixed t-candidates (`:184`); no perpendicular candidates exist. Pills are
  placed widest-first and clear each other by construction.
- **Curve protection**: the resolver's curve-corridor pass (`:737-855`) clears edge
  curves only from **isolated** repos — connected third-party repos are never checked
  against edges they do not belong to.
- **Fit**: when any edge pill exists, `scale = 1` unconditionally (`:468-472`) — the
  world overflows the viewport instead of shrinking. `usePanZoom` starts at
  `{x:0, y:0, zoom:1}` with no fit-to-content (`app.jsx:303`); the GalaxyView never
  computes a fitted initial viewport (the FlowView does — `app.jsx:3951-3960`).
- **Labels**: fixed geometry `LABEL_HALF_W=90` (180px box), `LABEL_GAP=26`, `LABEL_H=16`
  (`galaxyLayout.js:86-88`); CSS `.repo-label` caps at 180px, ellipsis (`styles.css:399-409`).
  The resolver constrains label-vs-orb, label-vs-pill, and (islands only) label-vs-curve,
  but **not label-vs-label**.

## 2. Measured evidence (real graphs, real module)

A Node harness loaded `web/src/galaxyLayout.js` and replicated `app.jsx`'s exact edge
bundling, `edgeLabelText`, `edgePillWidth`, bend-side rule, and pill placement, then ran
the real seeded project graphs (`output/projects/spring-boot-cc568`,
`java-ee-fedb9`, `spring-petclinic-02020`). Results:

| Graph | Key violations | Notes |
|---|---|---|
| Spring Boot, 11 repos, 17 directed edges @ 1920×1080 | pill↔orb clearance **4.6px** (recommendation→user pill vs notification-service) | 4 edge curves pass under connected third-party orbs |
| | curve-under-orb: recommendation-service 6.9px under analytics→notification; user-service 18.6px under shipping→notification; **notification-service 50.7px under user↔recommendation (both directions)** | the flagship demo has a planet sitting on two edges |
| Spring Boot @ 1366×768 | content bbox 1442×866 — overflows the viewport by ~76px wide × ~98px tall (119% of viewport area), rendered at forced scale 1, initial zoom 1 | laptop first impression: cropped, zoomed-in middle |
| Java EE, 3 repos @ 1920×1080 | none — gaps ≥161px, pills ≥41px clear, triangle sides 336/339/333 | small graphs are fine; keep them that way (regression guard) |
| PetClinic, 26 repos (all isolated) @ 1920×1080 | **5 label-vs-label overlaps**: angularjs×kotlin (10×10), framework-petclinic×angular (7×2), kotlin×langchain4j (27×10), data-jdbc×htmx (32×5), reactive×flutter (10×13) | direct "labels overlap" evidence |
| | min orb-to-label margin **11.8px** | labels nearly touch neighboring orbs |

Spring Boot spacing is not tight in raw numbers (min inter-orb gap 165px, median 468px,
avg edge 510px, 0/55 pairs at the floor) — the "squashed" look there is caused by the
**clutter density**: pills resting 4.6px off orbs, curves threading under planets, and
the cropped initial view. PetClinic is the opposite failure: literal label collisions.

## 3. Findings (root causes)

### F1 — Labels overlap; there is no label-vs-label constraint (PetClinic)

The resolver separates circle-circle, label-vs-circle, pill-vs-circle, pill-vs-label,
pill-vs-pill, and island-vs-curve — but two repos whose **labels** collide are invisible
to it when neither label touches the other's orb. Same-row and shallow-diagonal pairs
(the 5 measured cases) sit in exactly that blind spot: the circle floor allows center
distances below what two 180×16 label rects require, and the label-vs-orb check does not
fire because labels hang `r+26px` below centers, far from the neighboring orb's circle.
The fit pass makes it worse: it scales orbs down (PetClinic fit = 0.75) while labels
stay fixed 180×16px, so the pre-fit clearances no longer guarantee label separation.

### F2 — Connected third-party repos can sit under edge curves (Spring Boot)

The curve-corridor pass is guarded by `if (islands.length)` (`galaxyLayout.js:737`) and
only pushes isolated repos clear of curves. A connected repo that is not an endpoint of
a given edge is never checked — measured: notification-service sits **50.7px under** the
user↔recommendation curve pair (its orb obscures two edges in the flagship demo).
The flow view has the direct counterpart solution: skip edges **arc above** intermediate
nodes (`app.jsx:3885-3909`) instead of moving them.

### F3 — Pills have no perpendicular escape; corridors get tight

`placeEdgePills` can only slide a pill along its curve (11 t-candidates). When every
candidate collides with an orb, the pill takes the least-bad spot and the resolver tries
to move orbs — measured result: 4.6px clearance. The flow view handles this with a
**lift-above** fallback: if the pill midpoint sits on a node box, lift it vertically to
the first clear spot (`app.jsx:4119-4125`). The galaxy needs the same idea in curve
space (perpendicular offsets from the curve, preferring outward from the cluster).

### F4 — First impression is cropped: forced scale=1 + no initial fit

Pill-bearing graphs render at scale 1 by design ("scroll is fine"), but the viewport
starts at zoom 1 centered on the middle — at 1366×768 the Spring Boot map is 119% of the
viewport, so edges/pills at the extremes are cut off and the eye lands on the densest
part of the map. The FlowView already computes a fit-to-content viewport capped at 100%
(`fitViewport`, `app.jsx:3951-3960`, `minZoom = fitViewport.zoom`) and refits when the
flow changes. The galaxy has no equivalent.

### F5 — The force objective is "minimum legal packing", not "comfortable spacing"

Spring rest length equals the collision floor (`galaxyLayout.js:413`), and repulsion is
a hard threshold at the same floor. The system's only equilibrium is the tightest legal
packing: every pair ends up at (or near) its floor, and `EDGE_BREATHING=64` is the
entire breathing budget — a constant, not a hierarchy. There is no force that *wants*
space. The flow view inverts this: spacing is fixed and generous (colStep 440 → ~270px
between 170px nodes; NODE_GAP_Y 160 — `app.jsx:3832,3844`), never a floor. For the
galaxy: give springs an **ideal length above the floor** (they should pull pairs *to*
the ideal, both from below and above), and let repulsion start softly *before* the hard
floor so neighbors spread instead of parking at the minimum.

### F6 — Uniform 180px label boxes waste the space short names don't need

Every repo reserves a 180×16 label box even though `.repo-label` renders at the name's
natural width (e.g. "user-service" ≈ 90px). In dense fields this adds ~90px of phantom
clearance per neighbor pair, which both wastes canvas and (counterintuitively) pushes
repos into fewer viable spots, increasing crowding. Per-repo label widths (measured or
estimated from `name.length`, clamped to 180) would free real space.

### F7 — Bend is capped at 130px; long edges have shallow arcs

`edgeCurve` computes `bend = Math.min(130, d * 0.26)` (`galaxyLayout.js:131`). On the
longest Spring Boot edges (788px) the bow is ~17% of the chord — nearly a straight line
through the interior, which is why F2's crossings happen at 50.7px depth. Arc-over
routing (F2 fix) needs a bend parameter that can grow per-edge; today's `edgeCurve(a, b,
side)` signature has no per-edge bend control.

## 4. Leveraging the flow view's design — what transfers and what doesn't

**Transfers (recommended, with reasoning):**

| Flow-view mechanism | Why it transfers | Where it lives |
|---|---|---|
| Skip-edge arc-over: edges spanning intermediate nodes arc above them (arcY solved to clear the tallest node) | The galaxy's exact F2 failure (third-party planet under an edge). Routing around is strictly better than moving nodes: it keeps the packing stable, doesn't fight other constraints, and is cheap (per-edge solve). The general-graph equivalent is: raise the per-edge bend until the curve clears any third-party orb/label corridor, bounded, with a push fallback. | `app.jsx:3885-3909` |
| Pill lift-above fallback: pill blocked by a node box → lift to the first clear spot instead of overlapping | Directly fixes F3 (4.6px clearance). Ported to curve space: candidate positions offset perpendicular to the curve (outward from the cluster centroid first), then along-curve candidates, then the current least-bad fallback. Both the resolver and the renderer already share `placeEdgePills`, so the rule stays single-sourced. | `app.jsx:4119-4125` |
| Fit-to-content initial viewport (zoom ≤ 100%, minZoom = fit, refit on project change) | Fixes F4 with a proven, small pattern already in this codebase. Shows the whole constellation on first paint on any window size; "scroll is fine" remains true for zooming in. | `app.jsx:3951-3975` |
| Fixed generous spacing (constant, not floor-derived) | Fixes F5 at the objective level. Galaxy adaptation: springs target an ideal edge length (≈ 1.3–1.6× the current floor, or a constant ~400–450px center distance), repulsion ramps up softly toward the hard floor. | `app.jsx:3832,3844` |

**Does NOT transfer (with reasoning):**

- **The depth-column grid** — the flow is a DAG from one origin; the galaxy is a general
  graph with cycles and hubs. A grid cannot express order↔inventory or a 6-consumer hub
  without heavy edge crossings. Force relaxation stays; only the objective changes (F5).
- **Node-box geometry / edge attachment at box faces** — orbs are circles with hanging
  labels; `edgeCurve` already attaches at orb rims with a 3px gap. Box face attachment
  is an artifact of rectangles, not a general improvement.
- **The exact constants** (colStep 440, NODE_GAP_Y 160) — flow nodes are fixed 170×110
  boxes; galaxy orbs are radius-encoded (40–82px). Port the *philosophy* (generous,
  constant, floor-independent), not the numbers.
- **FlowIndexView's scroll-grid** — the galaxy's pan/zoom + fit-zoom model should stay;
  the earlier docs already rejected converting the galaxy to a scroll layout.

## 5. Implementation plan (ordered; each item is independently verifiable)

### P1 — Label-vs-label resolver constraint (fixes F1)

In the post-fit resolver (after the label-vs-circle block, `galaxyLayout.js:592-629`),
add a label-rect vs label-rect pass: for each pair with overlapping 180×16 boxes, push
along the axis of least overlap (mirror the pill-pill axis choice at `:695-726`),
damped (0.6×, ≥0.5px threshold), with isolated repos taking the whole push (existing
`isolated[]` convention). Also make the isolated-pair floor label-aware:
`clearance = max(139.5, 204 - (r_i + r_j))` so small-orb same-row pairs keep ≥204px
center distance (180px labels + 24px margin). Re-verify: 0 label overlaps, min
orb-to-label margin ≥ 24px on PetClinic.

### P2 — Ideal-length springs + soft repulsion (fixes F5, the "squash" at its root)

- Change the spring rest to `rest = max(floor, floor + IDEAL_EXTRA)` with
  `IDEAL_EXTRA ≈ 90` (tunable; start here) and make springs two-sided (pull when
  `d > rest`, push when `d < rest`) — pairs converge to a comfortable length instead of
  parking at the floor. Keep the weight multiplier capped at 2×.
- Replace the hard-threshold repulsion with a soft ramp: force 0 at `1.15 × minDist`,
  linear to full at `minDist`. This spreads neighbors *before* they hit the floor and
  reduces the resolver's workload.
- Bump `EDGE_BREATHING` 64 → 96 for pairs inside a constellation (visual air; the
  overflow cost is handled by P4's fit).
- Acceptance: inter-orb visual gaps ≥ 200px in the Spring Boot constellation; still 0
  overlaps everywhere; Java EE triangle must keep its ~equal side lengths and ≥150px
  gaps (regression guard — it is clean today).

### P3 — Per-edge bend control + arc-over for third-party crossings (fixes F2, F7)

- Extend `edgeCurve(a, b, side, bendScale = 1)` with `bend = min(200, d * 0.26) *
  bendScale` and expose the bend through the existing shared geometry (layout +
  renderer must agree — same pattern as `edgeSides`/`placeEdgePills` today).
- In the resolver, generalize the island-vs-curve pass (`:737-855`) to *all* third-party
  repos: for each directed edge, for each non-endpoint repo whose inflated box touches
  the curve's hull, try increasing `bendScale` (up to ~2.5) until the sampled curve
  clears the orb (r + 10px) and label (corners ≥ 5px); if that fails, push the repo
  (damped, islands take the whole push as today).
- Acceptance: zero third-party orbs/labels under any curve in Spring Boot (today:
  notification-service at 50.7px, user-service at 18.6px, recommendation-service at
  6.9px).

### P4 — Fit-to-content initial viewport for the galaxy (fixes F4)

Port FlowView's `fitViewport` to the galaxy: compute world bounds from the layout
output (the same bbox the fit pass uses, plus pills), set initial
`{x, y, zoom}` with `zoom = min(1, (W-90×2)/cw, (H-90×2)/ch)`, `minZoom = fit`, refit on
project/viewport change (not on every resize — match FlowView's behavior), keep ⤢ as
reset-to-fit. Note: this allows zoom < 1 (usePanZoom already clamps 0.2–3). Do NOT
change the scale=1-with-pills rule inside `layoutGalaxy` — overflow + fit-zoom is the
correct combination; shrinking the world with fixed-px pills re-creates resolver jams
(documented in `galaxy-small-graph-regression.md` §Shipped fix #5).

### P5 — Perpendicular pill escape (fixes F3)

In `placeEdgePills`, extend the candidate list: for each t in the existing candidates
(plus a finer sweep 0.5/0.4/0.6/0.3/0.7), add perpendicular offsets from the curve
normal at ±{18, 36, 54}px, ordering the outward-from-cluster side first (reuse the
centroid rule from `edgeBendSide`). Keep the deterministic widest-first order and the
existing pill-pill clear-by-construction guarantee. The renderer picks the pill up via
the shared function — no renderer changes beyond what it already does.
Acceptance: min pill↔orb clearance ≥ 24px on Spring Boot (today 4.6px).

### P6 — Per-repo label widths (fixes F6, supports P1)

Replace the uniform `LABEL_HALF_W` in the resolver, `placeEdgePills`, and the fit bounds
with a per-repo width function: `min(180, max(60, name.length * 7.3 + 16))` (13px bold,
~7.3px/char, 8px padding each side; calibrate against real rendering). Keep
`LABEL_HALF_W` exported as the cap for back-compat. This frees ~90px of phantom
clearance around short names and directly reduces crowding in dense fields.
Acceptance: PetClinic content bbox shrinks or stays equal with 0 label overlaps.

## 6. Verification

Re-run a harness equivalent to the one used for this analysis (replicates `app.jsx`'s
edge bundling, `edgeLabelText`, `edgePillWidth`, bend rules, and pill placement against
the real module and the three seeded graphs; also run synthetic cases: 60/120 repos,
12-repo chain, 8-cycle, dense hub):

| Metric | Today (measured) | Target |
|---|---|---|
| PetClinic label-vs-label overlaps | 5 | 0 |
| PetClinic min orb-to-label margin | 11.8px | ≥ 24px |
| Spring Boot min pill↔orb clearance | 4.6px | ≥ 24px |
| Spring Boot third-party orb under curve | 50.7px deep | 0 crossings |
| Spring Boot inter-orb visual gap (min) | 165px | ≥ 200px |
| Spring Boot initial view coverage @ 1366×768 | cropped (119% overflow, zoom 1) | 100% of bbox visible |
| Java EE triangle | clean (sides 336/339/333, gaps ≥161px) | unchanged or better |
| Determinism (same input twice) | byte-identical | unchanged |
| Layout time (26 repos) | ~4ms | < 100ms |

Also: `npm run build` passes; `python tests/run_tests.py` stays green (the 4 pre-existing
environmental failures documented on main — mermaid validator + conversation-title —
are unrelated; see the fix commit `369684a`/`8b345e6` for the jsdom + UTF-8 repairs that
made them pass locally).

## 7. Non-goals (do not change)

- **Z-order** (`.edges` z-30 under `.repo-wrap` z-40) — deliberate, documented, correct.
- **Orb radius encoding** (`36 + count*7`, clamped 40–82) — entry-point count encoding
  stays; smaller orbs do not buy pill room (established in `galaxy-small-graph-regression.md`).
- **No new dependencies, no randomness** — the layout must stay a pure deterministic
  module; the resolve → re-center+round → resolve → re-center sequence must survive any
  change (rounding flips bend sides; re-establish the guarantee on the integer state).
- **The scale=1-with-pills rule** — keep it; pair it with P4's fit-zoom instead.
- **FlowIndexView** — its scroll-grid model is unrelated to this work.

## 8. References

- `web/src/galaxyLayout.js` — all layout/resolver code (line numbers in §1/§3).
- `web/src/app.jsx:1166` GalaxyView, `:3744` FlowView, `:301` usePanZoom.
- `web/src/styles.css:399` `.repo-label` geometry.
- `docs/analyses/topology-layout-failure-analysis.md` — the original ring-layout failure.
- `docs/analyses/galaxy-layout-remaining-issues.md` — earlier label/orb round.
- `docs/analyses/galaxy-small-graph-regression.md` — pill-clearance round + harness
  methodology (its "Known limitation" section already predicted this follow-up).
