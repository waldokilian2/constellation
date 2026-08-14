# Topology UI Layout Failure — Technical Analysis

> Date: 2026-08-13 · Area: frontend (`web/src/app.jsx` GalaxyView)
> Triggered by: Spring PetClinic project with 26 repositories
> Status: fixed — deterministic constellation layout (`web/src/galaxyLayout.js`)

## 1. Visual Comparison: Small vs Large Projects

**Small projects (3 repos, Java EE):** The layout works as designed. Three repos (`java-ee-order-service`, `java-ee-notification-service`, `java-ee-fulfillment-service`) are evenly spaced on a circular ring at 120° intervals. Each repo is an orb (circle) containing the entry-point count, with producer dots orbiting the ring. Two cross-repo edges are clearly visible: a purple curved line (HTTP + message between order→fulfillment, labeled "1 msg · 1 HTTP") and a cyan curved line (message between fulfillment→notification, labeled "shipment-events"). Labels, orbs, and edges have ample clearance. The visual metaphor of a "galaxy" with connected "stars" reads correctly.

**Large projects (26 repos, Spring PetClinic):** The layout completely fails. All 26 repos are placed on a single fixed-radius circular ring at ~13.8° intervals. The result is a dense annulus of overlapping orbs — a "ring of circles" effect. Key visual artifacts:

- **Orb overlap:** Orb diameters range from 80px (0 entry points) to 164px (8+ entry points). The arc length per repo on a typical 1080p viewport (~370px ring radius) is only ~89px. Most orbs overlap adjacent ones by 30-70%.
- **Label collision:** Repo name labels (`spring-petclinic-ai`, `spring-petclinic-modulith`, `spring-petclinic-jooq`, etc.) overlap each other and the orbs, rendering most unreadable. The green-bordered labels stack in a garbled mass.
- **Entry-point counts unreadable:** The large numeric labels ("18", "17", "29", "40", "14", "11", "1") are partially occluded by neighboring orbs and labels.
- **Orb fill overlap:** The translucent cyan fills of adjacent orbs bleed into each other, creating a continuous glowing band rather than discrete nodes.
- **Producer dots compressed:** The small colored producer dots orbiting each orb's ring collide with neighboring orbs' rings and fills.

## 2. Root Cause: The Layout Algorithm

The old layout was computed in `GalaxyView` (`web/src/app.jsx`):

```javascript
const positions = useMemo(() => {
    const n = repos.length;
    return repos.map((name, i) => {
      const angle = (2 * Math.PI * i) / Math.max(n, 1) - Math.PI / 2;
      const count = epCount[name] || 0;
      const r = Math.max(40, Math.min(82, 36 + count * 7));
      return {
        name, count, r,
        x: cx + radius * Math.cos(angle),
        y: cy + radius * Math.sin(angle),
      };
    });
}, [graph, W, H]);
```

with `radius = Math.max(120, Math.min(W, H) * 0.34)`.

This is a **fixed-radius uniform circular layout**. It distributes `n` repos at equal angular intervals `(2π / n)` around a circle whose radius is `min(W, H) * 0.34` (about 34% of the viewport's smaller dimension). The orb radius `r` is computed from entry-point count (`36 + count * 7`, clamped 40-82px) but **does not feed back into position** — positions are purely angular.

The algorithm has three fundamental deficiencies:

1. **No connected-component awareness.** Repos with no edges between them (disconnected islands) are placed on the same ring as tightly connected clusters. In the PetClinic screenshot, repos like `.github`, `spring-petclinic-hilla`, `spring-petclinic-kotlin`, and `spring-petclinic-graphql` — which likely have no cross-repo links — sit adjacent to repos with 29+ entry points.

2. **No collision avoidance.** The angular spacing `2π / n` produces arc length `2π * R / n`. For R=370, n=26: ~89px per repo. Orb diameters are 80-164px. Overlap is mathematically guaranteed for n > ~12 repos at typical viewport sizes.

3. **No scale adaptation.** The ring radius is a fixed fraction of viewport size, not a function of total orb area. Doubling repos from 13 to 26 halves the angular spacing but keeps the ring radius the same, so overlap worsens linearly.

## 3. Missing Edge Rendering

Edges are computed by iterating `cross_repo_links` and grouping by producer→consumer repo pairs. The geometry is calculated in `edgeGeom` as quadratic Bezier curves with a bend proportional to inter-orb distance, and edge labels sit at the Bezier midpoint — that part is already correct.

In the 26-repo case, edges are **present in the data** but **visually destroyed by the layout**, not by the z-order:

- For repos on opposite sides of the ring, the edge curves are very long (spanning the full ring diameter), so the label pills ("1 msg", channel names) float in empty space at the center of the canvas, far from either endpoint.
- For adjacent repos on the ring, the curves are so short and the orbs so crowded that lines and pills vanish into the overlap mass.

**Z-order is a non-issue.** `.edges` (the SVG layer) sits at `z-index: 30` and `.repo-wrap` (the orb divs) at `z-index: 40`. That layering is *deliberate and correct*: edges terminate at the orb rims and never draw across an orb's face — standard graph-drawing practice. The original draft of this analysis misdiagnosed this ("SVG at `z-index: auto`, rendered first → behind") and proposed moving edges above the orbs, which would regress the visual quality (lines crossing orb faces, labels covering entry-point counts). The fix keeps the existing z-order; edge legibility is recovered through geometry (short, unobstructed edges between clustered nodes) instead.

The net effect before the fix: **no edges were visually legible**, which eliminated the spatial organizing principle of the galaxy view — repos appeared as an undifferentiated ring with no indication of which ones communicate.

## 4. Desired Behavior

The galaxy view should simulate a **celestial/gravitational layout**:

- **Connected repos cluster together.** Repos that share cross-repo links should be positioned near each other, forming visible constellations. A repo with several links to another should sit close to it, not diametrically opposite on a ring.
- **Disconnected repos distribute across the canvas.** Isolated repos (no edges to any other repo) should not stack on the same ring — they scatter across the available space, creating the "scattered stars" metaphor.
- **Edge length correlates with relationship strength.** Strongly connected repos (many shared channels) should be closer; weakly connected repos farther apart.
- **No orb overlap.** The minimum inter-orb distance should be `r_i + r_j + gap` with a clearance that also keeps repo labels and orbit dots from colliding.
- **Edges are always visible.** Edge labels stay at the Bezier midpoint between their source and target orbs — with clustered endpoints, those midpoints are again *between* the endpoints instead of stranded in empty space.
- **Deterministic.** The same graph must produce the same map on every reload and resize — no randomness. This matters for repeatable screenshots, diff comparisons, and debugging.
- **No new dependencies.** The frontend deliberately keeps a tiny dependency footprint (React, marked, mermaid, dompurify). The fix must be a pure module, not a library.
- **Theme preserved.** The layout is pure geometry; the existing "dark sci-fi instrument" visual language (deep-space navy `#0a0e1a`, cyan `#00d4ff` orbs, mint `#00e0a8`/purple `#a78bfa` edges, green `#4ade80` labels, orbiting type dots) is untouched.

## 5. Technical Summary

| Aspect | Before | After |
|---|---|---|
| Layout algorithm | Uniform circular ring, fixed radius | Connected-component clustering + deterministic force relaxation |
| Position formula | `(cx + R*cos(2πi/n), cy + R*sin(2πi/n))` | Golden-angle spiral seed → collision + weighted-spring relaxation → viewport fit |
| Orb radius | `36 + epCount * 7`, clamped 40-82 | Unchanged — still encodes entry-point count; the fit pass scales it only to fit |
| Ring radius | `min(W,H) * 0.34`, min 120 | Gone; spacing emerges from forces + fit-to-viewport scale (≤1, floor 0.55) |
| Edge rendering | Bezier curves, labels at midpoint | Unchanged geometry; legible again because endpoints cluster |
| Z-order | SVG `z-index: 30` under orbs `z-index: 40` | Unchanged — deliberate, correct |
| Connected components | Not computed | Union-find over cross-repo links |
| Collision avoidance | None | Pairwise repulsion + collision-only settle pass; verified 0 overlaps |
| Determinism | Yes (formula-based) | Preserved — zero randomness |
| Repo count threshold | Fails for n > ~12 | Verified 1, 3, and 26 repos; scales to ~200 within budget |
| Dependencies | — | None added |

## 6. Recommended Fix (implemented)

The layout is replaced by a deterministic "constellation packing" in a new pure module `web/src/galaxyLayout.js` (`layoutGalaxy(repos, epCount, edges, W, H)`), used by `GalaxyView` in `web/src/app.jsx`:

1. **Connected-component decomposition (union-find).** Cross-repo link pairs are mapped to undirected, weighted edges (weight = number of distinct channels/HTTP calls between the pair). Union-find partitions repos into components; components are ordered largest-first.

2. **Golden-angle spiral seed.** Every repo is seeded on a spiral at angle `k · π(3−√5)` with radius `95·√k`. The golden angle minimizes symmetry so relaxation never locks into the degenerate overlapping ring. Largest components seed nearest the center; isolated repos trail the spiral outward — the "lone stars".

3. **Force relaxation (160 iterations).** Three forces, all deterministic:
   - **Collision** — every pair closer than `r_i + r_j + 56px` repels proportionally to the deficit. 56px keeps repo labels (which hang `r+42px` below center) clear of the next orb.
   - **Springs** — linked repos attract when farther apart than `r_i + r_j + 80px`, scaled by link count (capped at 2×). Strongly linked pairs pull closer: edge length tracks relationship strength.
   - **Centering** — a weak pull toward the canvas center stops clusters from drifting off-screen.
   - Velocity integration with 0.8 damping and a 18px/step speed cap guarantees convergence without oscillation.

4. **Collision-only settle pass (60 iterations).** Springs and centering are switched off; pure repulsion can never compress a pair back into overlap, so this guarantees clearance without undoing the clustering.

5. **Viewport fit.** The bounding box (orb radii + orbit-dot and label extents) is scaled to fit `W×H` with padding — **scale ≤ 1, floor 0.55** — then re-centered. Positions *and* orb radii scale together, so orb proportions, orbit rings, and edge geometry stay coherent. The existing pan/zoom layer handles the rest; the canvas never grows beyond the viewport.

6. **Label truncation.** `.repo-label` gets `max-width: 180px` + ellipsis (full name on `title` hover), so even dense clusters don't smear names into each other.

7. **What deliberately did *not* change:** the z-order (see §3), the `edgeGeom` curve math, the orbiting producer dots, the hover popups, and the legend. The fix is geometry-only.

Complexity is `O(n² · iterations)` — about 7 ms for 26 repos, ~30 ms for 100, still fine at 200 (useMemo recomputes only when the graph or viewport changes). 3 repos still form a neat triangle; 26 form clusters with isolated repos scattered around them — the "galaxy" metaphor is preserved: connected repos are constellations, isolated repos are lone stars.

## 7. Corrections from review

The original draft of this document contained four claims that did not survive code review. They are corrected above; the reasoning, for the record:

1. **Z-order diagnosis (rejected).** The draft claimed the SVG and orb divs were both `z-index: auto` and the SVG was accidentally behind. In fact `.edges` is `z-index: 30` and `.repo-wrap` is `z-index: 40` — deliberate layering. Its proposed fix (edges above orbs) would draw lines across orb faces and was dropped.
2. **d3-force dependency (rejected).** The draft suggested d3-force. A ~140-line deterministic module achieves the same result, adds zero dependencies (a repo convention), and avoids nondeterministic physics seeds. d3 would be justified only if the map grew to thousands of nodes.
3. **"Scatter semi-randomly" for isolated repos (rejected).** Randomness breaks determinism (reload stability, diff comparisons). The golden-angle spiral is deterministic and visually reads as scattered stars.
4. **Canvas expansion beyond the viewport (rejected).** The draft proposed growing the world and leaning on pan/zoom — the exact shrink-to-fit-then-scroll model that `FlowIndexView` already abandoned ("with many flows the shrink-to-fit + zoom model became unusable"). Fitting the layout into the viewport keeps the initial view complete.
5. **"Component isolation force" (dropped as redundant).** The draft's fix item #5 duplicated #1: components are already disjoint and the all-pairs collision force separates them. No special force needed.

The draft also missed three requirements that are now explicit: determinism, label handling (the failure screenshots show label collisions were as damaging as orb overlap), and the no-new-dependencies convention.

## 8. Verification

A Node harness exercised `layoutGalaxy` across the failure scenarios (results below; harness was a throwaway script, the checks are trivially repeatable):

| Scenario | Overlaps | Min gap | Bounding box | Time |
|---|---|---|---|---|
| 3 repos, all linked (triangle) | 0 | 58px | 314×260 fits 1920×1080 | 1.2ms |
| 26 repos, sparse links (PetClinic-like) | 0 | 53px | 1107×911 fits 1920×1080 | 7.2ms |
| 26 repos, one fully-connected cluster | 0 | 46px | 845×922 fits 1920×1080 | 9.1ms |
| 26 repos sparse @ 1366×768 | 0 | 36px | 754×622 fits | 2.2ms |
| 1 repo | 0 | — | centered | 0.0ms |

- **Determinism:** same input twice → byte-identical JSON output.
- **Build:** `npm run build` passes (Vite production build).
- **Manual check:** open the "Spring Boot" demo project (3 repos) — layout remains a tight triangle; ingest the 26-repo PetClinic project — clusters + spiral, no overlap, edges legible.

**Known remaining limitation:** very long repo names still truncate at 180px (full name on hover), and edge-label pills on long edges remain at the curve midpoint (the hover popup carries the details). Both are acceptable trade-offs; a label-collision post-pass is the natural follow-up if a project with 50+ repos surfaces problems.
