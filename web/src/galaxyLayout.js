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
      pill-rect vs circle, pill-rect vs pill-rect, pill-rect
      vs repo label, and isolated-repo orb/label vs the bare
      edge curve between pill and orb.  Islands take the whole
      push on the last one — they have no edges pulling them
      back — so no edge ever renders under a lone star.  Labels
      and pills are fixed pixel sizes that do not scale with the
      fit pass, so they are resolved on the final positions.
      The layout is re-centered afterwards.

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
const EDGE_BREATHING = 64;

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
  if (!others || !others.length) return 1;
  let sx = 0, sy = 0;
  others.forEach((p) => { sx += p.x; sy += p.y; });
  sx /= others.length; sy /= others.length;
  const s = sideSign(sx, sy, a.x, a.y, b.x, b.y);
  return flip ? -s : s;
}

// Bezier curve between two orb centers (same math the galaxy view renders).
// The control point bends perpendicular to the chord (side: +1/-1); the
// label pill sits at the curve midpoint.
export function edgeCurve(a, b, side) {
  const GAP = 3; // uniform clearance at both orb edges
  const dx = b.x - a.x, dy = b.y - a.y;
  const d = Math.hypot(dx, dy) || 1;
  const ux = dx / d, uy = dy / d;
  const s = side || 1;
  const start = { x: a.x + ux * (a.r + GAP), y: a.y + uy * (a.r + GAP) };
  const end = { x: b.x - ux * (b.r + GAP), y: b.y - uy * (b.r + GAP) };
  const bend = Math.min(130, d * 0.26);
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

// Deterministic pill placement: slide along the curve from the midpoint
// toward either endpoint and take the first spot whose pill rect clears
// every orb (endpoints included) and every repo label.  Falls back to the
// midpoint when no spot works — the post-fit resolver then pushes orbs
// instead.  Shared by the layout resolver and the renderer, so the pill
// always renders where the layout placed it.
export function edgePillT(a, b, positions, pw, ph, side) {
  const curve = edgeCurve(a, b, side);
  const candidates = [0.5, 0.35, 0.65, 0.2, 0.8, 0.08, 0.92];
  const rectAt = (t) => {
    const p = curvePoint(curve, t);
    return { x: p.x - pw / 2, y: p.y - ph / 2, w: pw, h: ph };
  };
  const rectClear = (rect) => {
    for (const o of positions) {
      const cxp = Math.max(rect.x, Math.min(o.x, rect.x + rect.w));
      const cyp = Math.max(rect.y, Math.min(o.y, rect.y + rect.h));
      if (Math.hypot(o.x - cxp, o.y - cyp) < o.r + 2) return false;
      const labTop = o.y + o.r + 26;
      const ox = Math.min(rect.x + rect.w, o.x + 90) - Math.max(rect.x, o.x - 90);
      const oy = Math.min(rect.y + rect.h, labTop + 16) - Math.max(rect.y, labTop);
      if (ox > 0 && oy > 0) return false;
    }
    return true;
  };
  for (const t of candidates) {
    if (rectClear(rectAt(t))) return t;
  }
  return 0.5;
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
  // float apart as lone stars, handled separately by minDist.
  const clearance = (i, j) => {
    const key = i < j ? i + "|" + j : j + "|" + i;
    const pw = pairPill.get(key) || 0;
    let base = Math.max(NODE_GAP, pw);
    if (!isolated[i] && !isolated[j]) base += EDGE_BREATHING;
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
  const minDist = (i, j) => {
    if (isolated[i] || isolated[j]) {
      return r[i] + r[j] + NODE_GAP * ISOLATED_GAP_MULT;
    }
    return r[i] + r[j] + clearance(i, j);
  };

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
          const rest = r[a] + r[b] + clearance(a, b);
          if (d > rest) {
            const f = (d - rest) * spring * Math.min(2, 1 + 0.2 * (w - 1)) / d;
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
    const ex = r[i] + 24; // orbit dots extend r+18
    const ey = r[i] + 42; // repo label hangs r+42 below center
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

  const pillExtents = (i, j) => {
    const pw = pairPill.get(i < j ? i + "|" + j : j + "|" + i);
    if (!pw) return null;
    const pa = { x: positions[i].x, y: positions[i].y, r: postR[i] };
    const pb = { x: positions[j].x, y: positions[j].y, r: postR[j] };
    const side = pairSide(i, j);
    // Slide along the curve to the first clear spot (same rule the
    // renderer uses); fall back to the midpoint.
    const t = edgePillT(pa, pb, positions, pw, pillH, side);
    const curve = edgeCurve(pa, pb, side);
    const p = curvePoint(curve, t);
    return { cx: p.x, cy: p.y, hw: pw / 2, hh: pillH / 2 };
  };
  // Depth of the orb circle (px,py,r) inside the pill rect (0 when outside).
  const rectCircleOverlap = (pl, px, py, r) => {
    const cxp = Math.max(pl.cx - pl.hw, Math.min(px, pl.cx + pl.hw));
    const cyp = Math.max(pl.cy - pl.hh, Math.min(py, pl.cy + pl.hh));
    const d = Math.hypot(px - cxp, py - cyp);
    return d < r ? r - d : 0;
  };
  // Nearest point on segment (sx,sy)-(ex,ey) to (px,py).
  const segNearest = (sx, sy, ex, ey, px, py) => {
    const dx = ex - sx, dy = ey - sy;
    const len2 = dx * dx + dy * dy || 1e-6;
    const t = Math.max(0, Math.min(1, ((px - sx) * dx + (py - sy) * dy) / len2));
    const qx = sx + t * dx, qy = sy + t * dy;
    return { d: Math.hypot(px - qx, py - qy), qx, qy };
  };
  for (let pass = 0; pass < 200; pass++) {
    let moved = false;

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

        // Label of a vs circle of b
        const labTopA = a.y + postR[i] + 26, labBotA = labTopA + 16;
        const labHalfW = 90;
        const cxb = Math.max(a.x - labHalfW, Math.min(b.x, a.x + labHalfW));
        const cyb = Math.max(labTopA, Math.min(b.y, labBotA));
        const dAB = Math.hypot(b.x - cxb, b.y - cyb);
        if (dAB < postR[j] - 0.5) {
          const gap = postR[j] - dAB + 6;
          if (isolated[i] && !isolated[j]) {
            a.y -= gap;
          } else if (isolated[j] && !isolated[i]) {
            b.y += gap;
          } else {
            const push = gap * 0.6;
            a.y -= push; b.y += push;
          }
          moved = true;
          continue;
        }

        // Label of b vs circle of a
        const labTopB = b.y + postR[j] + 26, labBotB = labTopB + 16;
        const cxa = Math.max(b.x - labHalfW, Math.min(a.x, b.x + labHalfW));
        const cya = Math.max(labTopB, Math.min(a.y, labBotB));
        const dBA = Math.hypot(a.x - cxa, a.y - cya);
        if (dBA < postR[i] - 0.5) {
          const gap = postR[i] - dBA + 6;
          if (isolated[j] && !isolated[i]) {
            b.y += gap;
          } else if (isolated[i] && !isolated[j]) {
            a.y -= gap;
          } else {
            const push = gap * 0.6;
            a.y -= push; b.y += push;
          }
          moved = true;
        }
      }
    }

    // Pill constraints: recompute pill extents each pass — they move with
    // their endpoint pair.  Built per DIRECTED edge: a bidirectional pair
    // renders two pills (one per bend side) and both must be resolved.
    const pills = [];
    for (const e of dirEdgeIdx) {
      const ext = pillExtents(e.a, e.b);
      if (ext) pills.push({ ext, a: e.a, b: e.b });
    }

    for (const pl of pills) {
      // Pill vs every non-endpoint orb.  An isolated orb takes the whole
      // push; connected orbs split it with the pill's endpoints.
      for (let k = 0; k < n; k++) {
        if (k === pl.a || k === pl.b) continue;
        const ov = rectCircleOverlap(pl.ext, positions[k].x, positions[k].y, postR[k]);
        if (ov < 0.5) continue;
        const dxk = positions[k].x - pl.ext.cx, dyk = positions[k].y - pl.ext.cy;
        const dk = Math.hypot(dxk, dyk) || 1e-3;
        const ux = dxk / dk, uy = dyk / dk;
        if (isolated[k]) {
          const push = Math.max(0.5, ov);
          positions[k].x += ux * push; positions[k].y += uy * push;
        } else {
          const push = Math.max(0.5, ov * 0.6);
          positions[k].x += ux * push; positions[k].y += uy * push;
          positions[pl.a].x -= ux * push; positions[pl.a].y -= uy * push;
          positions[pl.b].x -= ux * push; positions[pl.b].y -= uy * push;
        }
        moved = true;
      }
      // Pill vs every repo label (the 180×16 rect below each orb)
      for (let k = 0; k < n; k++) {
        const labTop = positions[k].y + postR[k] + 26;
        const ox = Math.min(pl.ext.cx + pl.ext.hw, positions[k].x + 90) - Math.max(pl.ext.cx - pl.ext.hw, positions[k].x - 90);
        const oy = Math.min(pl.ext.cy + pl.ext.hh, labTop + 16) - Math.max(pl.ext.cy - pl.ext.hh, labTop);
        if (ox <= 0 || oy < 0.5) continue;
        // Push the label's orb away from the pill vertically and the
        // pill's endpoints the other way, so the pill clears the label.
        // An isolated repo takes the whole push.
        const dir = pl.ext.cy < labTop ? 1 : -1;
        if (isolated[k]) {
          positions[k].y += Math.max(0.5, oy) * dir;
        } else {
          const push = Math.max(0.5, oy * 0.6);
          positions[k].y += push * dir;
          positions[pl.a].y -= push * dir; positions[pl.b].y -= push * dir;
        }
        moved = true;
      }
    }
    // Pill vs pill: separate along the axis of larger overlap
    for (let pi = 0; pi < pills.length; pi++) {
      for (let pj = pi + 1; pj < pills.length; pj++) {
        const A = pills[pi], B = pills[pj];
        const ox = A.ext.hw + B.ext.hw - Math.abs(A.ext.cx - B.ext.cx);
        const oy = A.ext.hh + B.ext.hh - Math.abs(A.ext.cy - B.ext.cy);
        if (ox <= 0 || oy <= 0) continue;
        // Two pills of the SAME pair share endpoints — pushing them apart
        // cancels out (each endpoint moves twice, once per pill).  Instead
        // lengthen the chord: both curves bend proportionally, so the two
        // midpoints (one per side) separate with it.
        if ((A.a === B.a && A.b === B.b) || (A.a === B.b && A.b === B.a)) {
          const pa = positions[A.a], pb = positions[A.b];
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
          ux = A.ext.cx < B.ext.cx ? 1 : -1;
        } else {
          push = Math.max(0.5, oy * 0.6);
          uy = A.ext.cy < B.ext.cy ? 1 : -1;
        }
        positions[A.a].x -= ux * push; positions[A.a].y -= uy * push;
        positions[A.b].x -= ux * push; positions[A.b].y -= uy * push;
        positions[B.a].x += ux * push; positions[B.a].y += uy * push;
        positions[B.b].x += ux * push; positions[B.b].y += uy * push;
        moved = true;
      }
    }
    // ── Islands vs bare edge curves ──
    // The pill checks above clear every pill, but the curve between a
    // pill and its endpoint orbs is unguarded — an isolated repo can sit
    // on it and obscure the edge.  Sample each directed edge's curve and
    // push every island's orb and label clear of it.  Islands have no
    // edges pulling them back, so they take the entire push and the
    // endpoints stay put.
    if (islands.length) {
      const T = 12;
      for (const e of dirEdgeIdx) {
        const side = pairSide(e.a, e.b);
        const curve = edgeCurve(
          { x: positions[e.a].x, y: positions[e.a].y, r: postR[e.a] },
          { x: positions[e.b].x, y: positions[e.b].y, r: postR[e.b] },
          side
        );
        const pts = [];
        for (let s = 0; s <= T; s++) pts.push(curvePoint(curve, s / T));
        for (const k of islands) {
          const p = positions[k];
          // Orb vs corridor: nearest sampled segment point to the orb center.
          let nd = Infinity, nqx = 0, nqy = 0;
          for (let s = 0; s < T; s++) {
            const seg = segNearest(pts[s].x, pts[s].y, pts[s + 1].x, pts[s + 1].y, p.x, p.y);
            if (seg.d < nd) { nd = seg.d; nqx = seg.qx; nqy = seg.qy; }
          }
          const mdCurve = postR[k] + CURVE_CLEAR;
          if (nd < mdCurve - 0.5) {
            const push = Math.max(0.5, (mdCurve - nd) * 0.8);
            const ux = (p.x - nqx) / nd, uy = (p.y - nqy) / nd;
            p.x += ux * push; p.y += uy * push;
            moved = true;
          }
          // Label: corners must clear every segment, and no sampled point
          // may sit inside the label rect.
          const labTop = p.y + postR[k] + 26, labBot = labTop + 16;
          const corners = [
            [p.x - 90, labTop], [p.x + 90, labTop],
            [p.x - 90, labBot], [p.x + 90, labBot],
          ];
          for (const [cx0, cy0] of corners) {
            let nd2 = Infinity, qx2 = 0, qy2 = 0;
            for (let s = 0; s < T; s++) {
              const seg = segNearest(pts[s].x, pts[s].y, pts[s + 1].x, pts[s + 1].y, cx0, cy0);
              if (seg.d < nd2) { nd2 = seg.d; qx2 = seg.qx; qy2 = seg.qy; }
            }
            if (nd2 < LABEL_CURVE_M) {
              const push = Math.max(0.5, (LABEL_CURVE_M + 2 - nd2) * 0.8);
              const dx0 = cx0 - qx2, dy0 = cy0 - qy2;
              const dd = Math.hypot(dx0, dy0) || 1e-3;
              p.x += (dx0 / dd) * push; p.y += (dy0 / dd) * push;
              moved = true;
            }
          }
          const labCx = p.x, labCy = p.y + postR[k] + 34;
          for (const pt of pts) {
            if (pt.x <= p.x - 90 || pt.x >= p.x + 90 || pt.y <= labTop || pt.y >= labBot) continue;
            let dx0 = labCx - pt.x, dy0 = labCy - pt.y;
            const dd = Math.hypot(dx0, dy0);
            if (dd < 1e-3) { dx0 = 0; dy0 = -1; } else { dx0 /= dd; dy0 /= dd; }
            // Distance along the direction until the point exits the rect.
            let ex = Infinity;
            if (Math.abs(dx0) > 1e-6) {
              const tx = dx0 > 0 ? (p.x + 90 - pt.x) / dx0 : (p.x - 90 - pt.x) / dx0;
              if (tx >= 0) ex = Math.min(ex, tx);
            }
            if (Math.abs(dy0) > 1e-6) {
              const ty = dy0 > 0 ? (labBot - pt.y) / dy0 : (labTop - pt.y) / dy0;
              if (ty >= 0) ex = Math.min(ex, ty);
            }
            const push = Math.max(0.5, (ex === Infinity ? 8 : ex + 4) * 0.8);
            p.x += dx0 * push; p.y += dy0 * push;
            moved = true;
          }
        }
      }
    }
    if (!moved) break;
  }

  // ── Re-center after the resolver ──
  // The pushes above can drift the cluster away from the viewport center.
  // Translate it back — a pure translation preserves every clearance.
  let sumX = 0, sumY = 0;
  positions.forEach((p) => { sumX += p.x; sumY += p.y; });
  const meanX = sumX / n, meanY = sumY / n;
  const tx = cx - meanX, ty = cy + 12 - meanY;
  positions.forEach((p) => { p.x = Math.round(p.x + tx); p.y = Math.round(p.y + ty); });

  return positions;
}
