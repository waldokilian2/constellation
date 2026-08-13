# Galaxy Layout — Small-Graph Regression: Root Cause, Fix, Verification

> Status: **implemented and verified** (2026-08-13). This doc records the measured
> root cause, the shipped fix, the alternatives that were prototyped and rejected,
> and the verification harness results. It supersedes the earlier speculative
> draft (which misdiagnosed the cause — see "Corrections to the draft" below).

## The problem

In small connected graphs (3–4 repos: the Java EE and Spring Boot seeds), the
edge-label pills (channel names like `shipment-events`, `1 msg · 1 HTTP`)
overlapped the orbs they sit between, and one pill overlapped a third repo's
label. Labels were illegible, and a follow-up review found the constellation
felt "squashed together" — edges too short, no breathing room.

## Measured root cause (from a numeric harness over the real seeded graphs)

The layout is NOT "too large" or "cramped" as the draft claimed. Measured with
the real module and data:

| Graph | Content bbox | Viewport 1920×1080 | Overlaps before fix |
|---|---|---|---|
| Java EE (3 repos) | 412×416px | ~15% of canvas | `1 msg · 1 HTTP` pill overlapped BOTH endpoint orbs by ~10px; `shipment-events` overlapped the notification repo label |
| Spring Boot (3 repos) | 398×358px | ~12% of canvas | same two failure modes |

The actual defect: **the inter-orb gap is a fixed ~90px** (from `NODE_GAP`/
`SPRING_MARGIN`), but **the label pill is a fixed ~108–128px wide** (9px font,
`label.length * 6.5 + 22` + glow). The pill — centered at the Bezier midpoint
between the orbs — is wider than the gap between them, so it sits on top of the
orbs regardless of orb size or viewport size. `minDist = r_i + r_j + NODE_GAP`
is radius-invariant in its margin, which is why "make the orbs smaller" could
never have fixed it.

The draft's "orbs fill ~60% of the viewport" figure matches a ~700px-tall
window only by coincidence (the cluster is 416px tall and centered); the cause
it inferred from that (oversized orbs) was wrong.

## Shipped fix

All changes are in `web/src/galaxyLayout.js` and `web/src/app.jsx`.

1. **Per-pair pill clearance + breathing room** — `layoutGalaxy(repos, epCount,
   edges, W, H, pillWFor)` takes a map of rendered pill widths per directed
   edge. `clearance(i, j)` = base gap (90px) + `EDGE_BREATHING` (64px) for any
   pair inside a constellation, plus the pair's pill width when linked. Used in
   the pairwise repulsion, the spring rest length, and the post-fit resolver.
   Seeded graphs went from ~264px to ~330px average edge length (+25%).
   Isolated pairs keep `NODE_GAP * 1.55` (PetClinic "lone star" spacing —
   same relative arrangement as before, now properly centered).
2. **Shared edge geometry** — `edgeCurve(a, b, side)` + `curvePoint(curve, t)`
   + `EDGE_PILL = { H: 20, PAD: 4 }` live in `galaxyLayout.js`; the layout, the
   renderer, and the fit bounding box all use the same curve. `app.jsx` gained
   `edgeLabelText(items)` / `edgePillWidth(label)` helpers so the `pillWFor`
   memo and the render can never disagree on widths.
3. **Side-aware bend** — `edgeBendSide(a, b, others, flip)` chooses the bend
   side away from the centroid of the other repos, so pills face the outside
   of a cluster. **Bidirectional pairs** (edges in both directions between the
   same two repos) flip the reverse direction to the opposite physical side —
   without this, both pills land on the identical curve midpoint (a bug the
   expanded Spring Boot graph exposed: `order↔analytics`,
   `order↔inventory`, `user↔recommendation` all overlapped by 28px).
4. **Pill sliding (`edgePillT`)** — the pill slides along its curve from the
   midpoint toward either endpoint (candidates 0.5, 0.35, 0.65, 0.2, 0.8,
   0.08, 0.92) and takes the first spot that clears every orb and repo label.
   Deterministic, shared by the post-fit resolver and the renderer, so the
   pill always renders where the layout placed it. This was the decisive fix
   for dense graphs: a midpoint pill in a crowded pocket has nowhere to go,
   but sliding finds the gap.
5. **Fit pass never shrinks pill-bearing graphs** — pills are fixed pixel
   sizes; any shrink re-creates overlaps the resolver must then un-jam (the
   failure mode at 1366×768 / 800×600). Pill graphs render at scale 1 and
   overflow instead (pan/zoom covers it — the user's "scroll is fine").
   Pill-free graphs (PetClinic) keep the fit-to-viewport behavior.
6. **Pill repulsion during relaxation** (`PILL_REPULSE = 0.1`) — each pill
   midpoint repels every non-endpoint orb, with equal-and-opposite force on
   the endpoint pair.
7. **Post-fit collision resolver** — separates circle-circle, label-rect vs
   circle, pill-rect vs circle, pill-rect vs repo-label, and pill-rect vs
   pill-rect violations with fractional damped pushes (0.6×, 0.5px threshold)
   so the greedy pass converges geometrically instead of oscillating on
   integer rounding; pill-vs-label pushes are direction-aware. The layout is
   re-centered afterwards (pure translation; preserves every clearance).
8. **Isolated repos never cross an edge** (2026-08-13 follow-up) — the pill
   checks cleared pills but not the *bare curve* between pill and orbs, so a
   lone star (reporting-service) sat on the `order→payment` curve and its
   label crossed two others. Two changes: (a) the resolver now builds one
   pill per **directed** edge — a bidirectional pair renders two pills (one
   per bend side) and previously the flipped-side pill was invisible to the
   resolver; same-pair pill overlaps separate by lengthening the chord
   (endpoint pushes would cancel). (b) A curve-corridor pass samples every
   edge curve (12 segments) and pushes each island's orb (≥10px clearance)
   and label (corners ≥5px, no sampled point inside the rect) clear of it.
   Islands take the whole push in every constraint (orb-orb, label-orb,
   pill-orb, pill-label, curve) — they have no edges pulling them back, so
   the constellation never yields to a lone star. This applies to **all**
   graphs: every render path goes through `layoutGalaxy`, so islands can
   never sit on an edge in any project/viewport.

## Prototyped and rejected (with evidence)

- **Draft Fix 1 — cap orb radius at 55px for ≤6-repo components.** Rejected:
  (a) the chord gap is radius-invariant, so smaller orbs buy the pills zero
  room; (b) it flattens the entry-point-count encoding.
- **Draft Fix 2 — reduce SPRING_MARGIN to 60–70 for small components.**
  Rejected: wrong direction — tighter spacing makes overlaps worse.
- **Force-strength tuning alone** (0.1–0.2, margins 16–24, settle-phase
  force): swept on the expanded graph — shuffled the local minimum, never
  cleared it.
- **Integer-damped resolver pushes** (0.35× + 3px): oscillate in a ±9px limit
  cycle (overshoot from rounding); the fractional 0.6× pushes converge.
- **Bend away from the third vertex only (not centroid).** For seeded
  triangles this pointed pills at the third repo's label.

## Verification (numeric harness, real module, real seeded data)

All checks are deterministic-replayable; the harness replicates `app.jsx`'s
edge bundling, pill sizing, bend-side + flip rule, slide rule, and curve math,
and reports rect-vs-circle / rect-vs-rect intersections.

| Case | Result |
|---|---|
| Java EE 3 repos @ 1920×1080 / 1366×768 / 800×600 | all zeros; avg edge 330px (+25%) |
| Spring Boot 3 repos @ 1920×1080 / 1366×768 | all zeros; avg edge 308px |
| PetClinic 26 repos (isolated) | all zeros; identical relative arrangement to pre-fix |
| Synthetic 2-node with 36-char HTTP channel (264px pill) | all zeros |
| Synthetic 6-repo connected @ 1920×1080 / 1366×768 | all zeros |
| Synthetic 4-chain + 3 isolated @ 1920×1080 / 800×600 | all zeros |
| Synthetic 8-repo cycle @ 1920×1080 / 1280×800 | all zeros |
| Synthetic 12-repo chain @ 1920×1080 / 800×600 | all zeros (overflows the small viewport; pan/zoom) |
| **Real expanded Spring Boot graph: 11 repos, 17 edges, 19 directed link pairs** (9-repo connected constellation incl. 3 bidirectional pairs + 2 isolated islands) @ 1920×1080 / 1366×768 / 800×600 / 2560×1440 | **all zeros**; isoGap 156px |
| **Island-vs-curve** (the follow-up): expanded graph @ 3 viewports + triangle+lone-star + hub star with 2 islands + 4+3 mixed — island orbs/labels never touch a curve; before the fix, reporting-service sat 28px onto the `order→payment` curve | **all zeros** |
| Determinism (same input twice, 2–26 repos) | identical output |

`npm run build` passes; the server serves the rebuilt bundle. The Python suite
passes except 4 pre-existing environmental failures (mermaid validator +
conversation-title truncation — unrelated modules).

## Test data: expanded Spring Boot project

To exercise the layout on a larger real graph, the Spring Boot seed family was
extended (fixtures under `tests/repos/`, synced into the project and rescanned
via the ingest API):

- **Connected**: order, fulfillment, notification, analytics, payment,
  inventory, shipping, user, recommendation (9 repos)
- **Isolated islands**: reporting-service (scheduled + REST, no channels),
  legacy-monolith (orphan `legacy-jobs` JMS producer, dead `LegacyReport`)
- **New links**: `payment-events` payment→shipping, `user-events`
  user→recommendation, `recommendation-events` recommendation→user,
  `inventory-updates` inventory→order, HTTP `payment→fulfillment` and
  `inventory→order` (RestTemplate + Feign), plus the existing order-events
  hub (6 consumers), shipment-events, analytics-events, metrics-jobs.

The project graph now has 11 repos, 51 entry points, 27 producers, and 12
cross-repo link objects (19 directed pairs).

## Deep review follow-up (2026-08-13, second pass)

A scalability review against synthetic 60/120-repo projects (up to 127
directed pill edges, 40 islands) found four defects — all fixed and
re-verified:

1. **Final-rounding perturbation.** The resolver converged on fractional
   positions, then the re-center rounded to integers (±0.5px/axis). That
   rounding could flip a bend side or change a pill's slide candidate —
   both move pills by several px — leaving real pill overlaps after a
   "converged" solve. Fixed by the sequence resolve → re-center+round →
   resolve → re-center (pure translation), so the guarantee holds on the
   integer state.
2. **Force-based pill-pill resolution oscillates.** At 120 repos the
   greedy endpoint pushes cycle against orb constraints and every pass
   hits the 200-pass cap (verified by instrumentation). Replaced with a
   **coordinated placement** (`placeEdgePills`, shared by resolver and
   renderer): pills are placed widest-first and each slides along its
   curve (11 candidates) to the first spot clear of every orb, every
   label, and every previously placed pill. Pills can no longer fight
   each other through the orbs. Placement fallbacks (no fully clear
   spot) get a residual endpoint push to open room; endpoint-orb
   overlaps lengthen the chord.
3. **Island-label corridor under-sampling.** Point-sampling missed
   segments that crossed the 180×16 label rect between samples (the
   `payment→shipping` curve cut `reporting-service`'s label at t=0.70
   with 12 samples). Now a proper segment-vs-rect intersection test
   (endpoint-inside or edge-crossing) catches every crossing. Also
   fixed the island bounding-box horizontal inflation (label corners sat
   exactly on the box boundary, letting curves hug the edge undetected).
4. **Direction bug in label-orb pushes.** "Label of a vs circle of b"
   pushed both repos apart unconditionally — when b's orb sat ABOVE a's
   label the push moved them together. Now direction-aware.

Cleanups: label geometry constants (`LABEL_HALF_W`/`LABEL_GAP`/`LABEL_H`)
exported as the single source, named `RESOLVER_PASSES`/`CURVE_SEGS`,
zero-distance guards in corridor pushes (segment-normal fallback),
island bounding-box early-outs keep the corridor O(edges × islands) in
the common case.

**Precision floor.** Pushes fire at ≥0.5px of overlap, so the guarantee
is "no violation exceeds 0.5px" (sub-pixel; invisible). The harnesses
count violations only above that floor. Measured at 60 and 120 repos:
every class (orb-orb, label-orb, pill-orb, pill-label, pill-pill,
island-curve) is at or below the floor; layout+verify runs in ~0.4–0.6s.

## Known limitation

Pill-bearing graphs render at scale 1 and overflow the viewport on small
screens (pan/zoom covers it — "scroll is fine"). Extremely dense single
components could still require the resolver to push orbs when no slide
candidate clears; every measured case (up to 12 connected repos)
converges to zero. If denser graphs become a priority, the clean next
step is a labeling redesign (transit-map style labels at vertices, or
aggregated labels with the hover popup carrying details) rather than
more force tuning.
