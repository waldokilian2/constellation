/* ============================================================
   Galaxy layout — deterministic constellation packing

   Replaces the fixed-radius uniform ring (GalaxyView) with a
   deterministic layout that scales past ~12 repos:

   1. Connected components (union-find over cross-repo links)
      become clusters — "constellations".
   2. Repos are seeded on a golden-angle spiral, then relaxed
      with pairwise collision forces, weighted springs along
      links, a weak centering force, and a pill-repulsion force
      that keeps each edge-label pill clear of third-party orbs.
      No randomness anywhere, so the map is identical across
      reloads and resizes.
   3. A final fit pass scales and re-centers the whole layout
      into the viewport (scale ≤ 1 — never enlarges orbs).
   4. A post-fit collision resolver greedily separates any
      remaining overlaps: circle-circle, label-rect vs circle,
      pill-rect vs circle, pill-rect vs repo label, and
      isolated-repo orb/label vs the bare edge curve between
      pill and orb.  Pill rects come from a coordinated placement
      (placeEdgePills) shared with the renderer: pills are placed
      widest-first and slide to the first spot clear of every orb,
      label, and previously placed pill, so pills never fight each
      other through endpoint pushes.  Islands take the whole push
      on the curve constraint — they have no edges pulling them
      back — so no edge ever renders under a lone star.  Labels
      and pills are fixed pixel sizes that do not scale with the
      fit pass, so they are resolved on the final positions.  The
      layout resolves, re-centers + rounds, re-resolves on the
      integer state, and re-centers once more by pure translation.

   Linked pairs are spaced with per-pair clearance (label pill
   width + EDGE_BREATHING) so each edge has room for its pill
   AND reads as a real connection.  The bend side of each edge
   is chosen away from the cluster centroid so pills face
   outward (see edgeBendSide) — the renderer uses the same rule.

   Connected repos cluster; disconnected repos float apart as
   lone stars with generous spacing.

   Pure function of (repos, epCount, edges, W, H, pillWFor)
   → positions[].  No dependencies.  Runs in a few ms.
   ============================================================ */

const GOLDEN = Math.PI * (3 - Math.sqrt(5));

// Minimum center-to-center clearance beyond the two orb radii.
// Sized for the repo label (which hangs r+42px below center) to
// clear the next orb with comfortable margin.  Linked pairs may
// need more room for the edge-label pill (see clearance()).
const NODE_GAP = 90;

// Disconnected repos (no edges to any other repo) float further
// apart as "lone stars".  1.55× the base gap gives generous
// spacing without wasting canvas.
const ISOLATED_GAP_MULT = 1.55;

const RELAX_ITERS = 160;
const SETTLE_ITERS = 60;
const MAX_SPEED = 18;
const MIN_SCALE = 0.55;

// Extra breathing room between repos inside a constellation, on top of the
// base gap (and the pill width for linked pairs), so edges read as real
// connections instead of orbs squashed against labels.
const EDGE_BREATHING = 120;

// Springs pull linked pairs to (collision floor + IDEAL_EXTRA) instead of
// parking them at the floor — the layout aims at comfortable, even spacing
// (the flow view's fixed-column philosophy) rather than minimum legal packing.
const IDEAL_EXTRA = 90;

// Soft repulsion ramp: force starts at 0 when a pair is SOFT_BAND × its
// minimum distance and ramps linearly to full at the floor, so neighbors
// spread out BEFORE they touch instead of only reacting at the boundary.
const SOFT_BAND = 1.15;

// Per-edge bend multiplier cap for arc-over routing (see resolveEdgeBends):
// an edge that would pass under a third-party orb/label bows out until it
// clears it, up to this multiplier of its natural bend.
const MAX_BEND_SCALE = 2.5;

// Strength of the pill-vs-third-party-orb repulsion during relaxation.
const PILL_REPULSE = 0.1;

// Edge-label pill geometry — single source of truth, shared with app.jsx.
// The pill rect is EDGE_PILL.H tall plus EDGE_PILL.PAD of glow on each side.
export const EDGE_PILL = { H: 20, PAD: 4 };

// Island clearances from edge curves (post-fit resolver): an isolated
// orb stays this far from any curve stroke, and an isolated label's
// corners this far from any curve segment.
const CURVE_CLEAR = 10;
const LABEL_CURVE_M = 5;

// Repo label geometry — fixed px, does not scale with the fit pass.
// LABEL_HALF_W is the CAP (matches .repo-label's max-width:180px); the
// actual reserved box per repo is labelHalfWidth(name) so short names
// don't waste ~90px of phantom clearance on every side.
export const LABEL_HALF_W = 90;
export const LABEL_GAP = 26;
export const LABEL_H = 16;

// Full label box width + side margin for two same-row labels: the minimum
// center distance that keeps two 180px-wide labels clear of each other.
const LABEL_FULL_W = 2 * LABEL_HALF_W;
const LABEL_ROW_MARGIN = 24;

// Estimated rendered half-width of a repo label: 13px bold ≈ 7.5px/char,
// + 8px padding each side, clamped to the 180px CSS cap (min 64px).
// Used for label-aware clearance, the resolver, pill placement, and fit
// bounds — everywhere the layout needs the label box.  Calibrated to be
// slightly conservative so real rendering never exceeds the reserved box.
export function labelHalfWidth(name) {
  return Math.min(LABEL_HALF_W, Math.max(32, name.length * 3.75 + 8));
}

// Post-fit resolver pass cap and edge-curve sampling density.
const RESOLVER_PASSES = 200;
const CURVE_SEGS = 12;

// ── Edge curve geometry (shared with app.jsx) ──

// Sign of (point - chord midpoint) projected onto the +bend perpendicular.
const sideSign = (px, py, ax, ay, bx, by) => {
  const mx = (ax + bx) / 2, my = (ay + by) / 2;
  const ux = -(by - ay), uy = bx - ax;
  return (px - mx) * ux + (py - my) * uy > 0 ? -1 : 1;
};

// Bend side for the a→b curve: away from the centroid of `others` (every
// repo except the two endpoints), so pills face the outside of a cluster
// instead of crowding its interior (a ring would otherwise fold its pills
// inward).  Defaults to +1 when there is no third repo.  `flip` negates the
// result — used for the reverse direction of a bidirectional pair, whose two
// pills must land on opposite sides of the chord or they share a midpoint.
// app.jsx applies the same rule on the final positions, so render and
// layout always agree.
export function edgeBendSide(a, b, others, flip) {
  if (!others || !others.length) return flip ? -1 : 1;
  let sx = 0, sy = 0;
  others.forEach((p) => { sx += p.x; sy += p.y; });
  sx /= others.length; sy /= others.length;
  const s = sideSign(sx, sy, a.x, a.y, b.x, b.y);
  return flip ? -s : s;
}

// Bezier curve between two orb centers (same math the galaxy view renders).
// The control point bends perpendicular to the chord (side: +1/-1); the
// label pill sits at the curve midpoint.  `bendScale` multiplies the natural
// bend — arc-over routing (resolveEdgeBends) raises it per edge so a curve
// clears third-party orbs/labels instead of running under them.  The TOTAL
// bend is capped at 220px so an arc (and its label pill) never swings so far
// from the chord that it visually detaches from its endpoints — beyond the
// cap, the resolver pushes repos instead of bending further.  The layout
// resolver and the renderer must pass the same bendScale per directed edge.
export function edgeCurve(a, b, side, bendScale) {
  const GAP = 3; // uniform clearance at both orb edges
  const dx = b.x - a.x, dy = b.y - a.y;
  const d = Math.hypot(dx, dy) || 1;
  const ux = dx / d, uy = dy / d;
  const s = side || 1;
  const start = { x: a.x + ux * (a.r + GAP), y: a.y + uy * (a.r + GAP) };
  const end = { x: b.x - ux * (b.r + GAP), y: b.y - uy * (b.r + GAP) };
  const bend = Math.min(220, d * 0.26 * (bendScale || 1));
  const c = { x: (start.x + end.x) / 2 - s * uy * bend, y: (start.y + end.y) / 2 + s * ux * bend };
  const mid = {
    x: 0.25 * start.x + 0.5 * c.x + 0.25 * end.x,
    y: 0.25 * start.y + 0.5 * c.y + 0.25 * end.y,
  };
  const path = "M " + start.x + " " + start.y + " Q " + c.x + " " + c.y + " " + end.x + " " + end.y;
  return { mid, path, bend, d, start, end, c };
}

// Point on the curve at parameter t (0 = start orb edge, 1 = end orb edge).
export function curvePoint(curve, t) {
  const mt = 1 - t;
  return {
    x: mt * mt * curve.start.x + 2 * mt * t * curve.c.x + t * t * curve.end.x,
    y: mt * mt * curve.start.y + 2 * mt * t * curve.c.y + t * t * curve.end.y,
  };
}

// Nearest point on segment (sx,sy)-(ex,ey) to (px,py).
const segNearest = (sx, sy, ex, ey, px, py) => {
  const dx = ex - sx, dy = ey - sy;
  const len2 = dx * dx + dy * dy || 1e-6;
  const t = Math.max(0, Math.min(1, ((px - sx) * dx + (py - sy) * dy) / len2));
  const qx = sx + t * dx, qy = sy + t * dy;
  return { d: Math.hypot(px - qx, py - qy), qx, qy };
};

// True when the segment crosses the rect: an endpoint lies inside, or
// the segment intersects one of the rect's four edges.  Point-sampling
// alone misses crossings between samples (the rect is 180px wide — a
// segment can pass straight through the middle between two samples).
const segHitsRect = (sx, sy, ex, ey, x0, y0, x1, y1) => {
  if ((sx > x0 && sx < x1 && sy > y0 && sy < y1) || (ex > x0 && ex < x1 && ey > y0 && ey < y1)) return true;
  const cross = (ax, ay, bx, by, cx, cy, dx, dy) => {
    const d1 = (dx - cx) * (ay - cy) - (dy - cy) * (ax - cx);
    const d2 = (dx - cx) * (by - cy) - (dy - cy) * (bx - cx);
    const d3 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
    const d4 = (bx - ax) * (dy - ay) - (by - ay) * (dx - ax);
    return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0));
  };
  return cross(sx, sy, ex, ey, x0, y0, x1, y0)
    || cross(sx, sy, ex, ey, x1, y0, x1, y1)
    || cross(sx, sy, ex, ey, x1, y1, x0, y1)
    || cross(sx, sy, ex, ey, x0, y1, x0, y0);
};

// Undirected pairs with edges in BOTH directions — their two pills bend to
// opposite physical sides or they would share the same curve midpoint.
// Name-keyed ("a|b", sorted), shared by placeEdgePills and resolveEdgeBends.
const pairBothNameSet = (edges) => {
  const pairBoth = new Set();
  const seen = new Set();
  edges.forEach((e) => {
    if (!e.from || !e.to || e.from === e.to) return;
    const key = e.from < e.to ? e.from + "|" + e.to : e.to + "|" + e.from;
    if (seen.has(key)) pairBoth.add(key);
    seen.add(key);
  });
  return pairBoth;
};

// Coordinated pill placement — the single source of truth for WHERE a
// pill renders.  Pills are placed in a fixed order (widest first, then
// lexicographic by repo name) and each one slides ALONG its curve to the
// first spot whose rect clears every orb, every repo label, and every
// previously placed pill — labels always sit on their edge line (the
// arc-over corridor keeps the curve clear of third-party orbs, so a
// clear on-curve spot exists in practice).  `bends` is the per-directed-
// edge bend map from resolveEdgeBends; pills must be placed on the SAME
// curve the renderer draws.  A pill with no fully clear spot takes the
// least-bad candidate (deterministic tie-break); the resolver's endpoint
// pushes then open a spot on the next pass.  Both the resolver and the
// renderer call this, so a pill always renders exactly where the layout
// placed it.  Returns [{ from, to, i, j, side, t, cx, cy, hw, hh }].
export function placeEdgePills(edges, positions, pillWFor, bends) {
  const n = positions.length;
  const repos = positions.map((p) => p.name);
  const pairBoth = pairBothNameSet(edges);
  const pillH = EDGE_PILL.H + 2 * EDGE_PILL.PAD;
  const order = [];
  edges.forEach((e) => {
    const i = repos.indexOf(e.from), j = repos.indexOf(e.to);
    const pw = (pillWFor && pillWFor[e.from + ">>" + e.to]) || 0;
    if (i < 0 || j < 0 || i === j || !pw) return;
    order.push({ from: e.from, to: e.to, i, j, pw });
  });
  order.sort((A, B) => (B.pw - A.pw) ||
    (A.from < B.from ? -1 : A.from > B.from ? 1 : A.to < B.to ? -1 : 1));

  const ts = [0.5, 0.35, 0.65, 0.2, 0.8, 0.08, 0.92, 0.28, 0.72, 0.15, 0.85, 0.42, 0.58, 0.05, 0.95];
  const placed = [];
  for (const o of order) {
    const a = positions[o.i], b = positions[o.j];
    const others = positions.filter((_, k) => k !== o.i && k !== o.j);
    const pairKey = a.name < b.name ? a.name + "|" + b.name : b.name + "|" + a.name;
    const side = edgeBendSide(a, b, others, pairBoth.has(pairKey) && a.name > b.name);
    const curve = edgeCurve(a, b, side, (bends && bends[o.from + ">>" + o.to]) || 1);
    let best = null;
    // Pills stay ON their curve (no perpendicular offsets) so every label
    // sits exactly on its edge line; the slide along t resolves crowding.
    // The arc-over corridor keeps the curve clear of third-party orbs so an
    // on-curve spot exists in practice.
    for (const t of ts) {
      const p = curvePoint(curve, t);
      const rect = { x: p.x - o.pw / 2, y: p.y - pillH / 2, w: o.pw, h: pillH };
      let v = 0;
      for (const q of positions) {
        const cxp = Math.max(rect.x, Math.min(q.x, rect.x + rect.w));
        const cyp = Math.max(rect.y, Math.min(q.y, rect.y + rect.h));
        const d = Math.hypot(q.x - cxp, q.y - cyp);
        if (d < q.r + 2) v += q.r + 2 - d;
        const labTop = q.y + q.r + LABEL_GAP;
        const hw = labelHalfWidth(q.name);
        const ix = Math.min(rect.x + rect.w, q.x + hw) - Math.max(rect.x, q.x - hw);
        const iy = Math.min(rect.y + rect.h, labTop + LABEL_H) - Math.max(rect.y, labTop);
        if (ix > 0 && iy > 0) v += Math.min(ix, iy);
      }
      for (const pl of placed) {
        const ix = Math.min(rect.x + rect.w, pl.cx + pl.hw) - Math.max(rect.x, pl.cx - pl.hw);
        const iy = Math.min(rect.y + rect.h, pl.cy + pl.hh) - Math.max(rect.y, pl.cy - pl.hh);
        if (ix > 0 && iy > 0) v += Math.min(ix, iy);
      }
      if (v === 0) { best = { t, p, v }; break; }
      if (!best || v < best.v) best = { t, p, v };
    }
    placed.push({
      from: o.from, to: o.to, i: o.i, j: o.j,
      side, t: best.t, cx: best.p.x, cy: best.p.y, hw: o.pw / 2, hh: pillH / 2,
    });
  }
  return placed;
}

// Per-edge arc-over bend resolution — pure function of the final positions,
// shared by the post-fit resolver and the renderer so the curve that is
// drawn is exactly the curve that was checked.
//
// For each directed edge the natural bend may still pass under a third-party
// orb or label (only ISLANDS were protected from curves historically — a
// connected repo could sit on an unrelated edge).  This raises the edge's
// bendScale — the flow view's skip-edge arc-above idea — until the sampled
// curve clears every non-endpoint orb (r + CURVE_CLEAR + the edge's own pill
// half-width, since the pill rides this curve) and label (corners ≥
// LABEL_CURVE_M, no segment crossing the label rect), capped at
// MAX_BEND_SCALE.  Returns a map "from>>to" → bendScale (missing = 1).
export function resolveEdgeBends(edges, positions, pillWFor) {
  const byName = {};
  positions.forEach((p) => { byName[p.name] = p; });
  const pairBoth = pairBothNameSet(edges);
  const out = {};
  const SEGS = 24;
  for (const e of edges) {
    const a = byName[e.from], b = byName[e.to];
    if (!a || !b || a === b) continue;
    const key = e.from + ">>" + e.to;
    const others = positions.filter((p) => p.name !== e.from && p.name !== e.to);
    const pairKey = a.name < b.name ? a.name + "|" + b.name : b.name + "|" + a.name;
    const side = edgeBendSide(a, b, others, pairBoth.has(pairKey) && a.name > b.name);
    // The edge-label pill sits ON this curve, so the corridor must fit the
    // orb, the clearance, AND the pill rect — otherwise the pill placement
    // has nowhere clear to land and rests a few px off an orb.
    const pillHalf = ((pillWFor && pillWFor[key]) || 0) / 2;
    let scale = 1;
    for (let it = 0; it < 10; it++) {
      const curve = edgeCurve(a, b, side, scale);
      const pts = [];
      for (let s = 0; s <= SEGS; s++) pts.push(curvePoint(curve, s / SEGS));
      let pen = 0;
      for (const p of positions) {
        if (p === a || p === b) continue;
        let nd = Infinity;
        for (let s = 0; s < SEGS; s++) {
          const q = segNearest(pts[s].x, pts[s].y, pts[s + 1].x, pts[s + 1].y, p.x, p.y);
          if (q.d < nd) nd = q.d;
        }
        pen = Math.max(pen, p.r + CURVE_CLEAR + pillHalf - nd);
        const hw = labelHalfWidth(p.name);
        const labTop = p.y + p.r + LABEL_GAP, labBot = labTop + LABEL_H;
        const corners = [
          [p.x - hw, labTop], [p.x + hw, labTop], [p.x - hw, labBot], [p.x + hw, labBot],
        ];
        for (const [cx0, cy0] of corners) {
          let nd2 = Infinity;
          for (let s = 0; s < SEGS; s++) {
            const q = segNearest(pts[s].x, pts[s].y, pts[s + 1].x, pts[s + 1].y, cx0, cy0);
            if (q.d < nd2) nd2 = q.d;
          }
          pen = Math.max(pen, LABEL_CURVE_M - nd2);
        }
        for (let s = 0; s < SEGS; s++) {
          if (segHitsRect(pts[s].x, pts[s].y, pts[s + 1].x, pts[s + 1].y,
            p.x - hw, labTop, p.x + hw, labBot)) {
            pen = Math.max(pen, LABEL_CURVE_M + 4);
          }
        }
      }
      if (pen <= 0) break;
      // The total bend is capped (220px): raising the multiplier can't help
      // anymore — the resolver's arc-capped push handles the residual.
      if (curve.bend >= 220 - 1e-6) break;
      scale = Math.min(MAX_BEND_SCALE, scale * ((CURVE_CLEAR + pen) / CURVE_CLEAR + 0.05));
    }
    if (scale !== 1) out[key] = scale;
  }
  return out;
}

export function orbitRadius(count) {
  return Math.max(40, Math.min(82, 36 + count * 7));
}

// pillWFor: optional map of "from>>to" → rendered edge-label pill width (px).
// Linked pairs are spaced so the label pill clears both orbs — the pill is a
// fixed pixel size that does not scale with the fit pass, so the required
// clearance is the pill width itself.
export function layoutGalaxy(repos, epCount, edges, W, H, pillWFor) {
  const n = repos.length;
  if (n === 0) return [];

  const cx = W / 2, cy = H / 2;
  const r = repos.map((name) => orbitRadius(epCount[name] || 0));
  const index = new Map(repos.map((name, i) => [name, i]));

  // ── Undirected weighted adjacency from cross-repo links ──
  const wgt = new Map();
  edges.forEach((e) => {
    const i = index.get(e.from), j = index.get(e.to);
    if (i == null || j == null || i === j) return;
    const key = i < j ? i + "|" + j : j + "|" + i;
    wgt.set(key, (wgt.get(key) || 0) + Math.max(1, (e.items || []).length));
  });

  // ── Per-pair label-pill clearance ──
  const pairPill = new Map();
  edges.forEach((e) => {
    const i = index.get(e.from), j = index.get(e.to);
    if (i == null || j == null || i === j) return;
    const key = i < j ? i + "|" + j : j + "|" + i;
    const w = (pillWFor && pillWFor[e.from + ">>" + e.to]) || 0;
    pairPill.set(key, Math.max(pairPill.get(key) || 0, w));
  });

  // Undirected pairs with edges in BOTH directions — their two pills bend
  // to opposite physical sides or they would share the same curve midpoint.
  const pairBoth = new Set();
  {
    const seen = new Set();
    edges.forEach((e) => {
      const i = index.get(e.from), j = index.get(e.to);
      if (i == null || j == null || i === j) return;
      const key = i < j ? i + "|" + j : j + "|" + i;
      if (seen.has(key)) pairBoth.add(key);
      seen.add(key);
    });
  }

  // Fixed pixel height of the edge-label pill incl. glow — does not scale.
  const pillH = EDGE_PILL.H + 2 * EDGE_PILL.PAD;

  // Bend side for the a→b curve from position arrays: away from the
  // centroid of the other repos (mirrors the exported edgeBendSide).
  // The reverse direction of a bidirectional pair flips to the opposite
  // physical side, so the pair's two pills never share a midpoint.
  const bendSide = (px, py, a, b) => {
    if (n <= 2) return 1;
    let sx = 0, sy = 0;
    for (let k = 0; k < n; k++) {
      if (k === a || k === b) continue;
      sx += px[k]; sy += py[k];
    }
    sx /= n - 2; sy /= n - 2;
    let s = sideSign(sx, sy, px[a], py[a], px[b], py[b]);
    const key = a < b ? a + "|" + b : b + "|" + a;
    if (pairBoth.has(key) && repos[a] > repos[b]) s = -s;
    return s;
  };

  // ── Union-find → connected components ──
  const parent = Array.from({ length: n }, (_, i) => i);
  const find = (x) => {
    while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; }
    return x;
  };
  const union = (a, b) => {
    const ra = find(a), rb = find(b);
    if (ra !== rb) parent[ra] = rb;
  };
  wgt.forEach((w, key) => {
    const [a, b] = key.split("|").map(Number);
    union(a, b);
  });

  const groups = new Map();
  for (let i = 0; i < n; i++) {
    const g = find(i);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(i);
  }
  const order = Array.from(groups.values())
    .sort((a, b) => b.length - a.length)
    .flat();

  // ── Identify isolated repos (degree 0) ──
  const degree = new Array(n).fill(0);
  wgt.forEach((w, key) => {
    const [a, b] = key.split("|").map(Number);
    degree[a]++; degree[b]++;
  });
  const isolated = degree.map((d) => d === 0);

  // Pairs inside a constellation (neither repo isolated) get breathing
  // room on top of the base gap; linked pairs additionally reserve space
  // for their edge-label pill.  Isolated repos keep the plain gap — they
  // float apart as lone stars, handled separately by minDist.  Labels are
  // fixed pixel boxes that don't scale with the fit pass, so every pair
  // also keeps at least LABEL_FULL_W + LABEL_ROW_MARGIN center distance
  // (that term only binds when the radii are small).
  const clearance = (i, j) => {
    const key = i < j ? i + "|" + j : j + "|" + i;
    const pw = pairPill.get(key) || 0;
    let base = Math.max(NODE_GAP, pw);
    if (!isolated[i] && !isolated[j]) {
      base += EDGE_BREATHING;
    } else {
      base = Math.max(base, NODE_GAP * ISOLATED_GAP_MULT);
    }
    base = Math.max(base, LABEL_FULL_W + LABEL_ROW_MARGIN - (r[i] + r[j]));
    return base;
  };

  // ── Seed: golden-angle spiral ──
  const x = new Float64Array(n), y = new Float64Array(n);
  order.forEach((node, k) => {
    const rad = 95 * Math.sqrt(k + 1);
    x[node] = cx + rad * Math.cos(k * GOLDEN);
    y[node] = cy + rad * Math.sin(k * GOLDEN);
  });

  // ── Minimum distance for a pair (label-aware) ──
  // Labels hang r+42px below center.  Two vertically-stacked orbs
  // need their centers at least (r_i + r_j + 42 + 48) apart so
  // the upper label clears the lower orb by 48px.  For horizontal
  // adjacency the label extends ~90px to each side, so the same
  // threshold prevents horizontal label-orb collisions too.
  // Linked pairs additionally need room for the edge-label pill.
  const minDist = (i, j) => r[i] + r[j] + clearance(i, j);

  // ── Relaxation ──
  const vx = new Float64Array(n), vy = new Float64Array(n);
  const fx = new Float64Array(n), fy = new Float64Array(n);

  const relax = (iters, { repulse, spring, center, pillRepulse }) => {
    for (let it = 0; it < iters; it++) {
      fx.fill(0); fy.fill(0);

      for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
          const dx = x[j] - x[i], dy = y[j] - y[i];
          const d = Math.hypot(dx, dy) || 1e-3;
          const md = minDist(i, j);
          if (d < md) {
            const f = (md - d) * repulse / d;
            const px = dx * f, py = dy * f;
            fx[i] -= px; fy[i] -= py; fx[j] += px; fy[j] += py;
          } else if (d < md * SOFT_BAND) {
            // Soft pre-floor ramp: force 0 at SOFT_BAND×minDist, ramping
            // linearly to the full repulsion at the floor — neighbors
            // spread out BEFORE touching instead of parking at the minimum.
            const t = (md * SOFT_BAND - d) / ((SOFT_BAND - 1) * md);
            const f = t * (md - d) * repulse / d;
            const px = dx * f, py = dy * f;
            fx[i] -= px; fy[i] -= py; fx[j] += px; fy[j] += py;
          }
        }
      }

      // Edge-label pills repel every non-endpoint orb: the pill is a
      // fixed-size rect at the curve midpoint, so keep orb centers at
      // least (r_k + pill corner radius + margin) from it.  Equal and
      // opposite force on the endpoints translates the pill away too.
      if (pillRepulse > 0) {
        wgt.forEach((w, key) => {
          const [a, b] = key.split("|").map(Number);
          const pw = pairPill.get(key);
          if (!pw) return;
          const curve = edgeCurve({ x: x[a], y: y[a], r: r[a] }, { x: x[b], y: y[b], r: r[b] }, bendSide(x, y, a, b));
          const clear = Math.hypot(pw / 2, pillH / 2) + 16;
          for (let k = 0; k < n; k++) {
            if (k === a || k === b) continue;
            const dxk = x[k] - curve.mid.x, dyk = y[k] - curve.mid.y;
            const dk = Math.hypot(dxk, dyk) || 1e-3;
            const md = r[k] + clear;
            if (dk < md) {
              const f = (md - dk) * pillRepulse / dk;
              const pxk = dxk * f, pyk = dyk * f;
              fx[k] += pxk; fy[k] += pyk;
              fx[a] -= pxk * 0.5; fy[a] -= pyk * 0.5;
              fx[b] -= pxk * 0.5; fy[b] -= pyk * 0.5;
            }
          }
        });
      }

      if (spring > 0) {
        wgt.forEach((w, key) => {
          const [a, b] = key.split("|").map(Number);
          const dx = x[b] - x[a], dy = y[b] - y[a];
          const d = Math.hypot(dx, dy) || 1e-3;
          // Ideal rest length sits IDEAL_EXTRA above the collision floor, and
          // the spring is TWO-SIDED: linked pairs converge to a comfortable
          // spacing instead of being pulled down onto the floor (the old
          // pull-only spring made the floor the only equilibrium — the
          // "squashed" packing).
          const rest = r[a] + r[b] + clearance(a, b) + IDEAL_EXTRA;
          const diff = d - rest;
          if (Math.abs(diff) > 1) {
            const f = diff * spring * Math.min(2, 1 + 0.2 * (w - 1)) / d;
            const px = dx * f, py = dy * f;
            fx[a] += px; fy[a] += py; fx[b] -= px; fy[b] -= py;
          }
        });
      }

      if (center > 0) {
        for (let i = 0; i < n; i++) {
          fx[i] += (cx - x[i]) * center;
          fy[i] += (cy - y[i]) * center;
        }
      }

      for (let i = 0; i < n; i++) {
        vx[i] = (vx[i] + fx[i]) * 0.8;
        vy[i] = (vy[i] + fy[i]) * 0.8;
        const sp = Math.hypot(vx[i], vy[i]);
        if (sp > MAX_SPEED) { vx[i] *= MAX_SPEED / sp; vy[i] *= MAX_SPEED / sp; }
        x[i] += vx[i]; y[i] += vy[i];
      }
    }
  };

  relax(RELAX_ITERS, { repulse: 0.05, spring: 0.012, center: 0.0012, pillRepulse: PILL_REPULSE });
  relax(SETTLE_ITERS, { repulse: 0.2, spring: 0, center: 0, pillRepulse: 0 });

  // ── Fit into the viewport (scale ≤ 1, never enlarges) ──
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (let i = 0; i < n; i++) {
    const ex = Math.max(r[i] + 24, labelHalfWidth(repos[i])); // orbit dots extend r+18; labels are fixed px
    const ey = r[i] + LABEL_GAP + LABEL_H; // repo label hangs below center
    minX = Math.min(minX, x[i] - ex); maxX = Math.max(maxX, x[i] + ex);
    minY = Math.min(minY, y[i] - ex); maxY = Math.max(maxY, y[i] + ey);
  }
  // Edge-label pills also bound the content: they sit at the curve
  // midpoints and are a fixed pixel size that the fit scale does not
  // shrink, so include them in the pre-fit bounds.
  wgt.forEach((w, key) => {
    const [a, b] = key.split("|").map(Number);
    const pw = pairPill.get(key) || 0;
    if (!pw) return;
    const curve = edgeCurve({ x: x[a], y: y[a], r: r[a] }, { x: x[b], y: y[b], r: r[b] }, bendSide(x, y, a, b));
    minX = Math.min(minX, curve.mid.x - pw / 2);
    maxX = Math.max(maxX, curve.mid.x + pw / 2);
    minY = Math.min(minY, curve.mid.y - pillH / 2);
    maxY = Math.max(maxY, curve.mid.y + pillH / 2);
  });
  const PAD = 36;
  const availW = Math.max(200, W - PAD * 2);
  const availH = Math.max(200, H - PAD * 2 - 28);
  const bw = maxX - minX, bh = maxY - minY;
  let scale = Math.min(1, availW / bw, availH / bh);
  if (pairPill.size) {
    // Pills are fixed pixel sizes: any shrink would re-create pill overlaps
    // that the post-fit resolver then has to un-jam.  Pill-bearing graphs
    // overflow instead (pan/zoom covers it — "scroll is fine").
    scale = 1;
  } else if (scale < MIN_SCALE) {
    scale = MIN_SCALE;
  }
  const bbx = (minX + maxX) / 2, bby = (minY + maxY) / 2;

  const positions = repos.map((name, i) => ({
    name,
    count: epCount[name] || 0,
    r: Math.round(r[i] * scale),
    x: Math.round((x[i] - bbx) * scale + cx),
    y: Math.round((y[i] - bby) * scale + cy + 12),
  }));

  // ── Post-fit collision resolver ──
  // The fit pass scales everything uniformly, but the label height (42px)
  // and edge-label pills are fixed pixel sizes that don't scale — so two
  // repos that were non-overlapping before scaling can have their labels
  // collide after.  This pass runs on the FINAL scaled positions and
  // greedily separates every violation (circle-circle, label-rect vs
  // circle, pill-rect vs circle, pill-rect vs pill-rect, pill-rect vs
  // repo label).  A pill belongs to its endpoint pair, so pill pushes move
  // the blocking orb/repo one way and the endpoints the other.  Linked
  // pairs keep the un-scaled pill clearance.  Pushes are damped so the
  // greedy pass converges instead of oscillating.
  const postR = positions.map((p) => p.r);

  // Directed edge indices and isolated repos for the curve-corridor checks.
  const dirEdgeIdx = [];
  edges.forEach((e) => {
    const a = index.get(e.from), b = index.get(e.to);
    if (a == null || b == null || a === b) return;
    dirEdgeIdx.push({ a, b });
  });
  const islands = [];
  for (let i = 0; i < n; i++) if (isolated[i]) islands.push(i);

  // Bend side for the a→b curve on the current positions — centroid-
  // outward, with the reverse direction of a bidirectional pair flipped
  // to the opposite physical side (same rule the renderer applies).
  const pairSide = (i, j) => {
    let sx = 0, sy = 0;
    for (let k = 0; k < n; k++) {
      if (k === i || k === j) continue;
      sx += positions[k].x; sy += positions[k].y;
    }
    let side = n <= 2 ? 1 : sideSign(sx / (n - 2), sy / (n - 2),
      positions[i].x, positions[i].y, positions[j].x, positions[j].y);
    const key = i < j ? i + "|" + j : j + "|" + i;
    if (pairBoth.has(key) && repos[i] > repos[j]) side = -side;
    return side;
  };

  // Depth of the orb circle (px,py,r) inside the pill rect (0 when outside).
  const rectCircleOverlap = (pl, px, py, r) => {
    const cxp = Math.max(pl.cx - pl.hw, Math.min(px, pl.cx + pl.hw));
    const cyp = Math.max(pl.cy - pl.hh, Math.min(py, pl.cy + pl.hh));
    const d = Math.hypot(px - cxp, py - cyp);
    return d < r ? r - d : 0;
  };
  let moved = false;
  const resolvePasses = () => {
    for (let pass = 0; pass < RESOLVER_PASSES; pass++) {
      moved = false;

      // Per-edge arc-over bends on the CURRENT positions: the rendered curve
      // must clear every third-party orb/label (see resolveEdgeBends), and
      // every curve-based check below (pills, island corridors) uses the
      // same bent curves the renderer will draw.
      const bends = resolveEdgeBends(edges, positions, pillWFor);

    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const a = positions[i], b = positions[j];
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.hypot(dx, dy) || 1e-3;

        // Circle-circle (orb vs orb).  An isolated repo has no edges
        // pulling it back, so it takes the whole push and the connected
        // repo stays put — lone stars yield instead of the constellation.
        const mdOrb = postR[i] + postR[j] + clearance(i, j);
        if (d < mdOrb - 0.5) {
          const push = (mdOrb - d) / 2 + 0.5;
          const ux = dx / d, uy = dy / d;
          if (isolated[i] && !isolated[j]) {
            a.x -= ux * push * 2; a.y -= uy * push * 2;
          } else if (isolated[j] && !isolated[i]) {
            b.x += ux * push * 2; b.y += uy * push * 2;
          } else {
            a.x -= ux * push; a.y -= uy * push;
            b.x += ux * push; b.y += uy * push;
          }
          moved = true;
          continue;
        }

        // Label of a vs circle of b.  Move the pair apart vertically in the
        // direction that actually separates them — b's orb can sit ABOVE a's
        // label (a large orb close above), where the old unconditional
        // "a up, b down" pushed the two together.
        const labTopA = a.y + postR[i] + LABEL_GAP, labBotA = labTopA + LABEL_H;
        const hwA = labelHalfWidth(a.name);
        const cxb = Math.max(a.x - hwA, Math.min(b.x, a.x + hwA));
        const cyb = Math.max(labTopA, Math.min(b.y, labBotA));
        const dAB = Math.hypot(b.x - cxb, b.y - cyb);
        if (dAB < postR[j] - 0.5) {
          const gap = postR[j] - dAB + 6;
          const sgn = a.y < b.y ? 1 : -1;
          if (isolated[i] && !isolated[j]) {
            a.y -= sgn * gap;
          } else if (isolated[j] && !isolated[i]) {
            b.y += sgn * gap;
          } else {
            const push = gap * 0.6;
            a.y -= sgn * push; b.y += sgn * push;
          }
          moved = true;
          continue;
        }

        // Label of b vs circle of a (mirror of the block above)
        const labTopB = b.y + postR[j] + LABEL_GAP, labBotB = labTopB + LABEL_H;
        const hwB = labelHalfWidth(b.name);
        const cxa = Math.max(b.x - hwB, Math.min(a.x, b.x + hwB));
        const cya = Math.max(labTopB, Math.min(a.y, labBotB));
        const dBA = Math.hypot(a.x - cxa, a.y - cya);
        if (dBA < postR[i] - 0.5) {
          const gap = postR[i] - dBA + 6;
          const sgn = b.y < a.y ? 1 : -1;
          if (isolated[j] && !isolated[i]) {
            b.y -= sgn * gap;
          } else if (isolated[i] && !isolated[j]) {
            a.y += sgn * gap;
          } else {
            const push = gap * 0.6;
            b.y -= sgn * push; a.y += sgn * push;
          }
          moved = true;
        }

        // Label of a vs label of b — the constraint that was missing:
        // two same-row/same-column labels can overlap without either
        // touching the other's orb, so the circle and label-orb checks
        // never fire.  Push along the axis of least overlap (mirror of
        // the pill-pill rule) so labels never sit on top of each other.
        const labTA = a.y + postR[i] + LABEL_GAP, labBA = labTA + LABEL_H;
        const labTB = b.y + postR[j] + LABEL_GAP, labBB = labTB + LABEL_H;
        const lx = Math.min(a.x + hwA, b.x + hwB) - Math.max(a.x - hwA, b.x - hwB);
        const ly = Math.min(labBA, labBB) - Math.max(labTA, labTB);
        if (lx > 0.5 && ly > 0.5) {
          let push, ux = 0, uy = 0;
          if (lx >= ly) {
            push = Math.max(0.5, lx * 0.6);
            ux = a.x < b.x ? -1 : 1;
          } else {
            push = Math.max(0.5, ly * 0.6);
            uy = a.y < b.y ? -1 : 1;
          }
          if (isolated[i] && !isolated[j]) {
            a.x += ux * push; a.y += uy * push;
          } else if (isolated[j] && !isolated[i]) {
            b.x -= ux * push; b.y -= uy * push;
          } else {
            a.x += ux * push; a.y += uy * push;
            b.x -= ux * push; b.y -= uy * push;
          }
          moved = true;
        }
      }
    }

    // Pill constraints: recompute the coordinated placement each pass —
    // pills move with their endpoint pair and clear each other by
    // construction (see placeEdgePills).  The remaining pushes resolve
    // pill-vs-orb and pill-vs-label overlaps the slide could not avoid.
    const pills = placeEdgePills(edges, positions, pillWFor, bends);

    for (const pl of pills) {
      // Pill vs every orb.  An isolated orb takes the whole push; a
      // connected orb splits it with the pill's endpoints.  An ENDPOINT
      // orb (placement fallback) lengthens the chord so the pill clears
      // both ends.
      for (let k = 0; k < n; k++) {
        const ov = rectCircleOverlap(pl, positions[k].x, positions[k].y, postR[k]);
        if (ov < 0.5) continue;
        if (k === pl.i || k === pl.j) {
          const pa = positions[pl.i], pb = positions[pl.j];
          const dxc = pb.x - pa.x, dyc = pb.y - pa.y;
          const dc = Math.hypot(dxc, dyc) || 1e-3;
          const ux = dxc / dc, uy = dyc / dc;
          const push = Math.max(0.5, ov * 0.6);
          pa.x -= ux * push; pa.y -= uy * push;
          pb.x += ux * push; pb.y += uy * push;
          moved = true;
          continue;
        }
        const dxk = positions[k].x - pl.cx, dyk = positions[k].y - pl.cy;
        const dk = Math.hypot(dxk, dyk) || 1e-3;
        const ux = dxk / dk, uy = dyk / dk;
        if (isolated[k]) {
          const push = Math.max(0.5, ov);
          positions[k].x += ux * push; positions[k].y += uy * push;
        } else {
          const push = Math.max(0.5, ov * 0.6);
          positions[k].x += ux * push; positions[k].y += uy * push;
          positions[pl.i].x -= ux * push; positions[pl.i].y -= uy * push;
          positions[pl.j].x -= ux * push; positions[pl.j].y -= uy * push;
        }
        moved = true;
      }
      // Pill vs every repo label (the label rect below each orb)
      for (let k = 0; k < n; k++) {
        const labTop = positions[k].y + postR[k] + LABEL_GAP;
        const hwk = labelHalfWidth(positions[k].name);
        const ox = Math.min(pl.cx + pl.hw, positions[k].x + hwk) - Math.max(pl.cx - pl.hw, positions[k].x - hwk);
        const oy = Math.min(pl.cy + pl.hh, labTop + LABEL_H) - Math.max(pl.cy - pl.hh, labTop);
        if (ox <= 0 || oy < 0.5) continue;
        // Push the label's orb away from the pill vertically and the
        // pill's endpoints the other way, so the pill clears the label.
        // An isolated repo takes the whole push.
        const dir = pl.cy < labTop ? 1 : -1;
        if (isolated[k]) {
          positions[k].y += Math.max(0.5, oy) * dir;
        } else {
          const push = Math.max(0.5, oy * 0.6);
          positions[k].y += push * dir;
          positions[pl.i].y -= push * dir; positions[pl.j].y -= push * dir;
        }
        moved = true;
      }
    }
    // Residual pill-pill overlaps from placement fallbacks (no fully clear
    // spot existed): push the endpoint pairs apart so the next placement
    // pass finds room.  Same-pair pills lengthen their chord instead —
    // pushing shared endpoints would cancel out.
    for (let pi = 0; pi < pills.length; pi++) {
      for (let pj = pi + 1; pj < pills.length; pj++) {
        const A = pills[pi], B = pills[pj];
        const ox = A.hw + B.hw - Math.abs(A.cx - B.cx);
        const oy = A.hh + B.hh - Math.abs(A.cy - B.cy);
        if (ox <= 0 || oy <= 0) continue;
        if ((A.i === B.i && A.j === B.j) || (A.i === B.j && A.j === B.i)) {
          const pa = positions[A.i], pb = positions[A.j];
          const dxc = pb.x - pa.x, dyc = pb.y - pa.y;
          const dc = Math.hypot(dxc, dyc) || 1e-3;
          const ux = dxc / dc, uy = dyc / dc;
          const push = Math.max(0.5, Math.max(ox, oy) * 0.6);
          pa.x -= ux * push; pa.y -= uy * push;
          pb.x += ux * push; pb.y += uy * push;
          moved = true;
          continue;
        }
        let push, ux = 0, uy = 0;
        if (ox >= oy) {
          push = Math.max(0.5, ox * 0.6);
          ux = A.cx < B.cx ? 1 : -1;
        } else {
          push = Math.max(0.5, oy * 0.6);
          uy = A.cy < B.cy ? 1 : -1;
        }
        positions[A.i].x -= ux * push; positions[A.i].y -= uy * push;
        positions[A.j].x -= ux * push; positions[A.j].y -= uy * push;
        positions[B.i].x += ux * push; positions[B.i].y += uy * push;
        positions[B.j].x += ux * push; positions[B.j].y += uy * push;
        moved = true;
      }
    }
    // ── Islands vs bare edge curves ──
    // The pill checks above clear every pill, but the curve between a
    // pill and its endpoint orbs is unguarded — an isolated repo can sit
    // on it and obscure the edge.  Sample each directed edge's curve and
    // push every island's orb and label clear of it.  Islands have no
    // edges pulling them back, so they take the entire push and the
    // endpoints stay put.  Bounding-box early-outs keep this O(edges ×
    // islands) in the common case instead of O(edges × islands × segments):
    // an island whose inflated box misses the curve's control-point hull
    // (a valid bound on the whole Bezier) cannot touch any segment.
    if (islands.length) {
      const islandBoxes = new Map();
      const boxFor = (k) => {
        const m = postR[k] + CURVE_CLEAR;
        const p = positions[k];
        // Horizontal margin must cover BOTH the orb (m) and the label corners
        // (label half-width + LABEL_CURVE_M) — a corner sits on the boundary
        // of the label, so without the extra margin a curve hugging the box
        // edge would be skipped by the early-out and never pushed clear.
        const hm = Math.max(labelHalfWidth(p.name) + LABEL_CURVE_M, m);
        const box = {
          x0: p.x - hm,
          x1: p.x + hm,
          y0: p.y - m - LABEL_CURVE_M,
          y1: p.y + postR[k] + LABEL_GAP + LABEL_H + LABEL_CURVE_M,
        };
        islandBoxes.set(k, box);
        return box;
      };
      islands.forEach(boxFor);

      for (const e of dirEdgeIdx) {
        const side = pairSide(e.a, e.b);
        const bendKey = positions[e.a].name + ">>" + positions[e.b].name;
        const curve = edgeCurve(
          { x: positions[e.a].x, y: positions[e.a].y, r: postR[e.a] },
          { x: positions[e.b].x, y: positions[e.b].y, r: postR[e.b] },
          side,
          bends[bendKey] || 1
        );
        const cbx0 = Math.min(curve.start.x, curve.c.x, curve.end.x);
        const cbx1 = Math.max(curve.start.x, curve.c.x, curve.end.x);
        const cby0 = Math.min(curve.start.y, curve.c.y, curve.end.y);
        const cby1 = Math.max(curve.start.y, curve.c.y, curve.end.y);
        let pts = null;
        for (const k of islands) {
          const ib = islandBoxes.get(k);
          if (ib.x1 < cbx0 || ib.x0 > cbx1 || ib.y1 < cby0 || ib.y0 > cby1) continue;
          if (!pts) {
            pts = [];
            for (let s = 0; s <= CURVE_SEGS; s++) pts.push(curvePoint(curve, s / CURVE_SEGS));
          }
          const p = positions[k];
          // Orb vs corridor: nearest sampled segment point to the orb center.
          let nd = Infinity, nqx = 0, nqy = 0, nsx = 0, nsy = 0, nex = 1, ney = 0;
          for (let s = 0; s < CURVE_SEGS; s++) {
            const seg = segNearest(pts[s].x, pts[s].y, pts[s + 1].x, pts[s + 1].y, p.x, p.y);
            if (seg.d < nd) {
              nd = seg.d; nqx = seg.qx; nqy = seg.qy;
              nsx = pts[s].x; nsy = pts[s].y; nex = pts[s + 1].x; ney = pts[s + 1].y;
            }
          }
          const mdCurve = postR[k] + CURVE_CLEAR;
          if (nd < mdCurve - 0.5) {
            if (nd < 1e-3) {
              // Center exactly on a segment — push along the segment normal
              // (a zero-length radial would never move the island).
              const lx = nex - nsx, ly = ney - nsy;
              const len = Math.hypot(lx, ly) || 1e-3;
              p.x += (-ly / len) * mdCurve;
              p.y += (lx / len) * mdCurve;
            } else {
              const push = Math.max(0.5, (mdCurve - nd) * 0.8);
              const ux = (p.x - nqx) / nd, uy = (p.y - nqy) / nd;
              p.x += ux * push; p.y += uy * push;
            }
            moved = true;
          }
          // Label: corners must clear every segment, and no segment may
          // cross the label rect (endpoint-inside or edge intersection —
          // point-sampling alone misses crossings between samples).
          const hwk = labelHalfWidth(p.name);
          const labTop = p.y + postR[k] + LABEL_GAP, labBot = labTop + LABEL_H;
          const corners = [
            [p.x - hwk, labTop], [p.x + hwk, labTop],
            [p.x - hwk, labBot], [p.x + hwk, labBot],
          ];
          for (const [cx0, cy0] of corners) {
            let nd2 = Infinity, qx2 = 0, qy2 = 0;
            for (let s = 0; s < CURVE_SEGS; s++) {
              const seg = segNearest(pts[s].x, pts[s].y, pts[s + 1].x, pts[s + 1].y, cx0, cy0);
              if (seg.d < nd2) { nd2 = seg.d; qx2 = seg.qx; qy2 = seg.qy; }
            }
            if (nd2 < LABEL_CURVE_M) {
              const push = Math.max(0.5, (LABEL_CURVE_M + 2 - nd2) * 0.8);
              const dx0 = cx0 - qx2, dy0 = cy0 - qy2;
              const dd = Math.hypot(dx0, dy0);
              if (dd < 1e-3) {
                p.y -= push;
              } else {
                p.x += (dx0 / dd) * push; p.y += (dy0 / dd) * push;
              }
              moved = true;
            }
          }
          const labCx = p.x, labCy = p.y + postR[k] + LABEL_GAP + LABEL_H / 2;
          for (let s = 0; s < CURVE_SEGS; s++) {
            const sx0 = pts[s].x, sy0 = pts[s].y, ex0 = pts[s + 1].x, ey0 = pts[s + 1].y;
            if (!segHitsRect(sx0, sy0, ex0, ey0, p.x - hwk, labTop, p.x + hwk, labBot)) continue;
            const q = segNearest(sx0, sy0, ex0, ey0, labCx, labCy);
            let dx0 = labCx - q.qx, dy0 = labCy - q.qy;
            const dd = Math.hypot(dx0, dy0);
            if (dd < 1e-3) { dx0 = 0; dy0 = -1; } else { dx0 /= dd; dy0 /= dd; }
            // Distance along the direction until the rect clears the
            // segment's nearest point.
            let ex = Infinity;
            if (Math.abs(dx0) > 1e-6) {
              const tx = dx0 > 0 ? (p.x + hwk - q.qx) / dx0 : (p.x - hwk - q.qx) / dx0;
              if (tx >= 0) ex = Math.min(ex, tx);
            }
            if (Math.abs(dy0) > 1e-6) {
              const ty = dy0 > 0 ? (labBot - q.qy) / dy0 : (labTop - q.qy) / dy0;
              if (ty >= 0) ex = Math.min(ex, ty);
            }
            const push = Math.max(0.5, (ex === Infinity ? 8 : ex + 4) * 0.8);
            p.x += dx0 * push; p.y += dy0 * push;
            moved = true;
          }
          boxFor(k);
        }
      }
    }
    // ── Connected repos vs bare edge curves (arc capped) ──
    // resolveEdgeBends bows every edge clear of third-party orbs/labels,
    // but a crowded interior can hit the MAX_BEND_SCALE cap with a residual
    // overlap.  Last resort: push the third-party repo away from the curve —
    // to the same corridor target the bend solver used (orb + clearance +
    // the edge's pill half-width, since the pill rides this curve) — damped;
    // an island takes the whole push, a connected repo splits it with the
    // edge endpoints (mirror of the pill-vs-orb rule).
    for (const e of dirEdgeIdx) {
      const bendKey = positions[e.a].name + ">>" + positions[e.b].name;
      const side = pairSide(e.a, e.b);
      const curve = edgeCurve(
        { x: positions[e.a].x, y: positions[e.a].y, r: postR[e.a] },
        { x: positions[e.b].x, y: positions[e.b].y, r: postR[e.b] },
        side, bends[bendKey] || 1
      );
      // Only when the RENDERED curve is at the total-bend cap (220px) can
      // bending no longer help — the arc-over solver stopped early at the
      // cap for these edges and the pushes below finish the job.
      if (curve.bend < 220 - 0.01) continue;
      const pillHalf = (pairPill.get(e.a < e.b ? e.a + "|" + e.b : e.b + "|" + e.a) || 0) / 2;
      const pts = [];
      for (let s = 0; s <= CURVE_SEGS; s++) pts.push(curvePoint(curve, s / CURVE_SEGS));
      for (let k = 0; k < n; k++) {
        if (k === e.a || k === e.b) continue;
        const p = positions[k];
        let nd = Infinity, nqx = 0, nqy = 0;
        for (let s = 0; s < CURVE_SEGS; s++) {
          const seg = segNearest(pts[s].x, pts[s].y, pts[s + 1].x, pts[s + 1].y, p.x, p.y);
          if (seg.d < nd) { nd = seg.d; nqx = seg.qx; nqy = seg.qy; }
        }
        const mdCurve = postR[k] + CURVE_CLEAR + pillHalf;
        if (nd < mdCurve - 0.5) {
          const push = Math.max(0.5, (mdCurve - nd) * 0.8);
          const ux = nd < 1e-3 ? 0 : (p.x - nqx) / nd;
          const uy = nd < 1e-3 ? -1 : (p.y - nqy) / nd;
          if (isolated[k]) {
            p.x += ux * push; p.y += uy * push;
          } else {
            p.x += ux * push; p.y += uy * push;
            positions[e.a].x -= ux * push * 0.5; positions[e.a].y -= uy * push * 0.5;
            positions[e.b].x -= ux * push * 0.5; positions[e.b].y -= uy * push * 0.5;
          }
          moved = true;
        }
      }
    }
      if (!moved) break;
    }
  };
  resolvePasses();
  // ── Re-center + round, then re-converge on the integer state ──
  // The resolver converges on fractional positions, but the final
  // rounding (±0.5px per axis) can flip a bend side or change a pill's
  // slide candidate — both move pills by several px — so the
  // guarantee must be re-established ON the rounded positions.  A pure
  // translation afterwards preserves every clearance exactly, so the
  // sequence is: resolve → re-center+round → resolve → re-center.
  const recenter = (round) => {
    let sumX = 0, sumY = 0;
    positions.forEach((p) => { sumX += p.x; sumY += p.y; });
    const tx = cx - sumX / n, ty = cy + 12 - sumY / n;
    positions.forEach((p) => {
      const nx = p.x + tx, ny = p.y + ty;
      p.x = round ? Math.round(nx) : nx;
      p.y = round ? Math.round(ny) : ny;
    });
  };
  resolvePasses();
  recenter(true);
  resolvePasses();
  recenter(false);

  return positions;
}
