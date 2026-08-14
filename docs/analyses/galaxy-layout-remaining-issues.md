# Galaxy Layout — Remaining Issues (2026-08-13)

> Superseded for the small-graph edge-label problem by
> [galaxy-small-graph-regression.md](./galaxy-small-graph-regression.md) —
> that doc has the measured root cause, the shipped per-pair pill-clearance
> fix, and the verification results. This file records the earlier round
> (label-orb collisions, bounding box, isolated-repo spacing) which remains
> implemented in the code.

## What the two screenshots show

### Screenshot 1: 26 repos (Spring PetClinic)

The constellation layout is working in principle — repos are no longer on a ring, clusters form, isolated repos scatter outward. But several specific problems remain:

- **Labels overlap neighboring orbs.** The label for `spring-petclinic-hilla` sits directly on the face of the large orb (29 entry points) above it. `spring-petclinic-ai` (40 entry points) has its label colliding with `spring-petclinic-react...` below. `spring-petclinic-github...` label overlaps the orb to its left. This is systematic — labels hang below each orb (`top: p.r + 26` in `app.jsx:1430`), but the collision detection only checks the orb circles, not the label space.
- **Repos are packed into the upper-left quadrant.** The bounding box should force a uniform scale, but the fit pass is over-shrinking because the bounding box calculation uses `r + 48` for vertical extent (line 157 of `galaxyLayout.js`) while the actual label extends to `r + 42`. This 6px discrepancy per orb accumulates across 26 repos, causing the fit pass to shrink the layout more than necessary.
- **Producer dots collide with neighboring orbs.** The orbiting type dots (at `orbitR = p.r + 18`) are not part of the collision detection either. Adjacent orbs' dots overlap into each other's space.

### Screenshot 2: 3 repos (Java EE triangle) — REGRESSION

This view was perfect before the changes. The original circular layout placed three repos at 120° intervals with clear separation, readable labels, and visible edge labels ("1 msg · 1 HTTP"). Now:

- **Labels overlap edge labels.** The `java-ee-notification-s...` label sits directly on top of the "1 msg · 1 HTTP" edge label between the fulfillment and order orbs.
- **Repos are too close together.** The spring rest length (`SPRING_MARGIN = 80`) pulls connected repos to `r_i + r_j + 80` center-to-center distance. For equal orbs (r=82): 244px. The original ring had them at ~311px (circumference / 3). The spring is compressing the triangle by ~22%.
- **The triangle is skewed.** The golden-angle spiral seed plus the centering force produces an asymmetric triangle instead of the clean 120° arrangement the circular layout gave for 3 repos.

## Root cause analysis

Three independent bugs compound the problem:

### Bug 1: Collision detection uses circles, labels are rectangles

The collision check (line 107: `minD = r[i] + r[j] + NODE_GAP`) treats each orb as a circle. But labels hang 42px below center as a rectangle. Two orbs can be non-overlapping as circles while their labels collide vertically:

```
Upper orb center:   y = 300,  r = 82
Lower orb center:   y = 522,  r = 82     (distance = 220 = 2×82 + 56 = minD ✓ no circle collision)

Upper label bottom: 300 + 82 + 42 = 424
Lower orb top:      522 − 82 = 440

Gap: 16px — labels visually collide with the orb face.
```

The bounding box for the fit pass uses `r + 48` (line 157), which is 6px larger than the actual label extent (`r + 42`). This doesn't prevent the collision — it just over-shrinks the layout during the fit pass.

### Bug 2: Spring rest is too short for connected repos

`SPRING_MARGIN = 80` means connected repos settle at `r_i + r_j + 80` center-to-center. The label requires at least `r_i + r_j + 90` (42px label + 48px clearance = 90). The spring is pulling connected repos 10px closer than the label space allows.

For the Java EE triangle (3 equal orbs, r=82): the spring pulls them to 244px apart. The original ring had them at ~311px. The 67px compression is what causes the edge labels and repo labels to collide.

### Bug 3: All repos get the same spacing regardless of connectivity

The user's design intent is clear: **connected repos should cluster tightly (constellations), disconnected repos should float apart (lone stars)**. The current algorithm uses the same `NODE_GAP` for all pairs, so connected and disconnected repos get identical minimum spacing. The user wants disconnected repos to have much more room — they should be scattered across the canvas with generous gaps, while connected repos stay close.

## What the fix needs to do

1. **Increase NODE_GAP to 90** — accounts for label height (42px) + clearance (48px). This prevents label-orb collisions for all pairs.

2. **Fix the bounding box** — use `r + 42` (actual label extent) instead of `r + 48`. This stops the fit pass from over-shrinking.

3. **Set spring rest to match the collision distance** — `SPRING_MARGIN = 90` (same as NODE_GAP). Connected repos settle at exactly the collision boundary, giving them the tightest possible clustering without label collisions.

4. **Give disconnected repos more room** — the golden-angle spiral already places them outward, but the collision check uses the same NODE_GAP. For disconnected repos, use a larger effective gap (e.g., 140px) so they scatter as "lone stars" with generous spacing.

5. **Add a post-fit collision resolver** — after the fit pass scales everything, the fixed 42px label height no longer scales proportionally with the radii. A post-fit pass on the final scaled positions resolves any remaining circle-circle and label-rect vs circle overlaps.

## Results after fix

| Metric | Before | After |
|---|---|---|
| Circle violations (26 repos) | 14 | 0 (max sub-pixel: 0.067px) |
| Label-orb violations | 1 | 0 |
| Label-label violations | 3 | 0 |
| 3-repo minGap | 58px | 94px |
| 26-repo minGap | 55px | 90px |
| Build | ✅ | ✅ |
| Deterministic | ✅ | ✅ |
