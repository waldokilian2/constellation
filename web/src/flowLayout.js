/* ============================================================
   Flow layout — deterministic layered DAG layout for the FlowView

   Replaces the FlowView's ad-hoc edge geometry with the same
   philosophy the galaxy view uses (galaxyLayout.js): every edge is
   routed deterministically and verified, and every label pill is
   placed ON its curve (never lifted perpendicularly into space).

   Pipeline:
   1. Cards sit on a depth-column grid (left → right).  Column
      spacing adapts to the widest label pill so pills always fit
      the corridor between columns; row spacing adapts to card
      height (cards list their entry methods).  Sibling order comes
      from deterministic barycenter sweeps (left→right→left→left)
      so edges cross as little as possible.
   2. Every card assigns each edge a distinct attachment y on the
      card face (fan sorted by the far end) — edges never share a
      start point, so a hub fans out cleanly.
   3. Edge shapes by class:
      - forward/backward adjacent columns: cubic bezier with
        horizontal-ish tangents at the faces; same-pair edges fan
        into vertically separated lanes (request/response pairs).
      - same-column edges: one smooth cubic per edge bowing out of
        the column; distinct lane widths keep nested bows apart.
      - skip edges (spanning >1 column): a smooth arch over the
        intermediate columns, escalated until clear; when no single
        arch can serve (corner-to-corner across fully stacked
        columns) a rounded rise/run/drop is the fallback.
   4. Collision resolution: each route gets a fixed, deterministic
      candidate list (increasing bow/bend/arc height, both sides).
      The candidate with zero curve↔card crossings is chosen; a
      second sweep minimizes curve↔curve approaches (<14px,
      forgiving the shared attachment zone where edges legitimately
      meet at a card).  Pure functions, no randomness.
   5. Pills (placeFlowPills): placed widest-first, each sliding
      ALONG its own curve to the first spot whose rect clears every
      card and every previously placed pill.  Labels always sit on
      their edge line.
   6. Bounds cover cards, external nodes, sampled curves and pill
      rects, so the fit-to-flow viewport shows everything.

   Pure function of (repos, externals, edges, pillWFor, H).
   No dependencies.  Runs in a few ms.
   ============================================================ */

// ── Card geometry (must match styles.css .flow-repo-node) ──
const CARD_W = 170;
const EXT_W = 160;
const EXT_H = 64;

// Estimated rendered card height: 16px padding top + name (13px × 1.5
// line-height + 8px margin) + n methods (10px × 1.5 + 3px gap) + 16px
// padding bottom  =  56.5 + 18n.
const cardHeight = (methods) => Math.max(74, 57 + 18 * Math.max(1, methods.length));

const PAD_X = 120;
const BASE_COL_STEP = 440;   // grows when a label pill is wider than the corridor
const COL_STEP_CAP = 700;
const BASE_ROW_GAP = 170;    // grows for tall cards
const CY_OFF = -30;          // vertical centering bias (matches the old flow view)

// Pill geometry (matches the FlowView render: glow rect 24 tall, w + 8 wide).
const PILL_H = 24;
const PILL_W_PAD = 8;
const PILL_MARGIN = 8;       // pill clearance from cards

const CARD_MARGIN = 10;      // curve clearance from non-endpoint cards
const CURVE_GAP = 14;        // min approach between unrelated curves
const ATTACH_R = 50;         // forgiveness radius around shared card faces
const SEGS = 16;             // curve sampling density

// Same-column arc geometry.
const BOW_BASE = 150;
const BOW_STEP = 110;
const BOW_CAP_OPEN = 800;    // open space past the last column

// Skip-edge arc geometry.
const ARC_MIN = 90;
const ARC_MAX = 160;
const ARC_STEP = 60;
const ARC_TRIES = 6;

// Adjacent-edge per-pair lane bend.
const BEND_MIN = 36;
const BEND_MAX = 80;
const BEND_ESCALATE = 60;
const BEND_ESCALATE_MAX = 240;
const BEND_TRIES = 4;

// ── Small geometry helpers ──

const cubicPoint = (p0, c1, c2, p3, t) => {
  const mt = 1 - t;
  return {
    x: mt * mt * mt * p0.x + 3 * mt * mt * t * c1.x + 3 * mt * t * t * c2.x + t * t * t * p3.x,
    y: mt * mt * mt * p0.y + 3 * mt * mt * t * c1.y + 3 * mt * t * t * c2.y + t * t * t * p3.y,
  };
};

const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

// Distance from point p to segment (a,b).
const segPointDist = (a, b, p) => {
  const dx = b.x - a.x, dy = b.y - a.y;
  const len2 = dx * dx + dy * dy || 1e-9;
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
};

// Do segments (a,b) and (c,d) cross?
// Closest point on segment (a,b) to segment (c,d).
const segSegClosestPoint = (a, b, c, d) => {
  const d1 = { x: b.x - a.x, y: b.y - a.y };
  const d2 = { x: d.x - c.x, y: d.y - c.y };
  const r = { x: a.x - c.x, y: a.y - c.y };
  const l1 = d1.x * d1.x + d1.y * d1.y || 1e-9;
  const l2 = d2.x * d2.x + d2.y * d2.y || 1e-9;
  const f = d2.x * r.x + d2.y * r.y;
  let s = 0, t = 0;
  if (l1 <= 1e-9 && l2 <= 1e-9) return { x: a.x, y: a.y };
  if (l1 <= 1e-9) {
    t = Math.max(0, Math.min(1, f / l2));
    return { x: c.x + d2.x * t, y: c.y + d2.y * t };
  }
  const c1 = d1.x * r.x + d1.y * r.y;
  if (l2 <= 1e-9) {
    s = Math.max(0, Math.min(1, -c1 / l1));
    return { x: a.x + d1.x * s, y: a.y + d1.y * s };
  }
  const bnum = d1.x * d2.x + d1.y * d2.y;
  const denom = l1 * l2 - bnum * bnum;
  s = denom ? Math.max(0, Math.min(1, (bnum * f - c1 * l2) / denom)) : 0;
  t = (bnum * s + f) / l2;
  if (t < 0) { t = 0; s = Math.max(0, Math.min(1, -c1 / l1)); }
  else if (t > 1) { t = 1; s = Math.max(0, Math.min(1, (bnum - c1) / l1)); }
  return { x: a.x + d1.x * s, y: a.y + d1.y * s };
};

const segsCross = (a, b, c, d) => {
  const o = (p, q, r) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
  const o1 = o(a, b, c), o2 = o(a, b, d), o3 = o(c, d, a), o4 = o(c, d, b);
  return ((o1 > 0 && o2 < 0) || (o1 < 0 && o2 > 0)) && ((o3 > 0 && o4 < 0) || (o3 < 0 && o4 > 0));
};

const segSegDist = (a, b, c, d) => {
  if (segsCross(a, b, c, d)) return 0;
  return Math.min(segPointDist(a, b, c), segPointDist(a, b, d), segPointDist(c, d, a), segPointDist(c, d, b));
};

// True when segment (sx,sy)-(ex,ey) intersects rect [x0,y0]-[x1,y1].
const segHitsRect = (sx, sy, ex, ey, x0, y0, x1, y1) => {
  if ((sx >= x0 && sx <= x1 && sy >= y0 && sy <= y1) || (ex >= x0 && ex <= x1 && ey >= y0 && ey <= y1)) return true;
  return segsCross({ x: sx, y: sy }, { x: ex, y: ey }, { x: x0, y: y0 }, { x: x1, y: y0 })
    || segsCross({ x: sx, y: sy }, { x: ex, y: ey }, { x: x1, y: y0 }, { x: x1, y: y1 })
    || segsCross({ x: sx, y: sy }, { x: ex, y: ey }, { x: x1, y: y1 }, { x: x0, y: y1 })
    || segsCross({ x: sx, y: sy }, { x: ex, y: ey }, { x: x0, y: y1 }, { x: x0, y: y0 });
};

const inflate = (r, m) => ({ x: r.x - m, y: r.y - m, w: r.w + 2 * m, h: r.h + 2 * m });

// ── Main layout ──

/**
 * repos:     [{ repo, depth, methods: string[] }]
 * externals: [{ key, targetRepo }]        (flow origin nodes)
 * edges:     [{ key, from, to }]          (key = "from>>to|channel|kind")
 * pillWFor:  { key → rendered pill width in px }
 * H:         visible stage height (drives vertical centering)
 *
 * Returns {
 *   positions:    [{ repo, x, y, w, h }],
 *   externalPos:  [{ key, targetRepo, x, y, w, h }],
 *   routes:       [{ key, from, to, skip, path, start, cp1, cp2, end }],
 *   pills:        { key → { t, x, y } },
 *   bounds:       { l, r, t, b },
 * }
 */
export function layoutFlow({ repos, externals, edges, pillWFor, H }) {
  const hasExternal = externals.length > 0;
  const cy = H / 2 + CY_OFF;

  // ── Cards ──
  const cards = repos.map((r, i) => ({
    name: r.repo,
    depth: r.depth + (hasExternal ? 1 : 0),
    methods: r.methods || [],
    idx: i,
    w: CARD_W,
    h: cardHeight(r.methods || []),
  }));
  const extCards = externals.map((e, i) => ({
    name: e.key,
    external: true,
    targetRepo: e.targetRepo,
    depth: 0,
    idx: i,
    w: EXT_W,
    h: EXT_H,
  }));

  const byName = {};
  cards.forEach((c) => { byName[c.name] = c; });
  extCards.forEach((c) => { byName[c.name] = c; });

  // Edges that reference known, distinct cards.
  const E = [];
  edges.forEach((e) => {
    const a = byName[e.from], b = byName[e.to];
    if (!a || !b || a === b) return;
    E.push({ key: e.key, from: e.from, to: e.to, a, b });
  });

  const maxPillW = Math.max(0, ...Object.values(pillWFor || {}));
  const colStep = Math.min(COL_STEP_CAP, Math.max(BASE_COL_STEP, CARD_W + maxPillW + 56));

  // ── Columns & sibling order (barycenter sweeps) ──
  const byDepth = {};
  cards.forEach((c) => { (byDepth[c.depth] = byDepth[c.depth] || []).push(c); });
  const depths = Object.keys(byDepth).map(Number).sort((a, b) => a - b);
  const xOf = (d) => PAD_X + d * colStep;

  const order = {};
  depths.forEach((d) => { order[d] = byDepth[d].slice().sort((a, b) => a.idx - b.idx); });

  const gapOf = (d) => {
    const maxH = order[d].reduce((m, c) => Math.max(m, c.h), 0);
    return Math.max(BASE_ROW_GAP, maxH + 60);
  };

  const assignY = () => {
    depths.forEach((d) => {
      const list = order[d];
      const n = list.length;
      const gap = gapOf(d);
      list.forEach((c, i) => { c.y = cy + (i - (n - 1) / 2) * gap; c.x = xOf(d); });
    });
    const nE = extCards.length;
    extCards.forEach((c, i) => { c.y = cy + (i - (nE - 1) / 2) * 220; c.x = PAD_X; });
  };

  const meanLeft = (c) => {
    const nbr = E.filter((e) => e.b === c && e.a.depth === c.depth - 1);
    if (!nbr.length) return null;
    return nbr.reduce((s, e) => s + e.a.y, 0) / nbr.length;
  };
  const meanRight = (c) => {
    const nbr = E.filter((e) => e.a === c && e.b.depth === c.depth + 1);
    if (!nbr.length) return null;
    return nbr.reduce((s, e) => s + e.b.y, 0) / nbr.length;
  };

  const sortBy = (list, meanFn) => {
    if (list.length < 2) return;
    list.sort((a, b) => {
      const na = meanFn(a), nb = meanFn(b);
      if (na === null && nb === null) return a.idx - b.idx;
      if (na === null) return 1;
      if (nb === null) return -1;
      if (na !== nb) return na - nb;
      return a.idx - b.idx;
    });
    assignY();
  };

  assignY();
  depths.forEach((d) => { if (d > 0) sortBy(order[d], meanLeft); });
  depths.slice().reverse().forEach((d) => sortBy(order[d], meanRight));
  depths.forEach((d) => { if (d > 0) sortBy(order[d], meanLeft); });

  // ── Per-pair lanes (same unordered pair, e.g. request + response) ──
  const pairCount = {};
  E.forEach((e) => {
    const pk = e.a.name < e.b.name ? e.a.name + "|" + e.b.name : e.b.name + "|" + e.a.name;
    e.pairKey = pk;
    e.pairIdx = pairCount[pk] || 0;
    pairCount[pk] = e.pairIdx + 1;
  });

  // ── Same-column bow lanes per column ──
  const sameColLane = {};
  const sameColByDepth = {};
  E.forEach((e) => {
    if (e.a.depth !== e.b.depth) return;
    (sameColByDepth[e.a.depth] = sameColByDepth[e.a.depth] || []).push(e);
  });
  Object.keys(sameColByDepth).forEach((d) => {
    sameColByDepth[d]
      .slice()
      .sort((a, b) => ((a.a.y + a.b.y) - (b.a.y + b.b.y)) || (a.key < b.key ? -1 : 1))
      .forEach((e, i) => { sameColLane[e.key] = i; });
  });

  // ── Skip-edge horizontal lanes per endpoint ──
  // Skip arcs rise/drop steeply in the corridor BESIDE a column; each skip
  // edge at the same card gets its own horizontal inset so parallel
  // rise/drop segments never run on top of each other.
  const skipLaneA = {}, skipLaneB = {};
  const skipCountA = {}, skipCountB = {};
  E.forEach((e) => {
    if (Math.abs(e.a.depth - e.b.depth) <= 1) return;
    skipLaneA[e.key] = skipCountA[e.a.name] || 0;
    skipCountA[e.a.name] = (skipCountA[e.a.name] || 0) + 1;
    skipLaneB[e.key] = skipCountB[e.b.name] || 0;
    skipCountB[e.b.name] = (skipCountB[e.b.name] || 0) + 1;
  });

  // ── Attachment fans: distinct y-offset per edge on each card face ──
  const fanR = {}, fanL = {};
  cards.forEach((c) => { fanR[c.name] = []; fanL[c.name] = []; });
  extCards.forEach((c) => { fanR[c.name] = []; fanL[c.name] = []; });
  E.forEach((e) => {
    if (e.a.depth === e.b.depth) { fanR[e.a.name].push(e); fanR[e.b.name].push(e); }
    else if (e.b.depth > e.a.depth) { fanR[e.a.name].push(e); fanL[e.b.name].push(e); }
    else { fanL[e.a.name].push(e); fanR[e.b.name].push(e); }
  });

  const offsets = {}; // key → { aOff, bOff }
  const assignFan = (card, list) => {
    const sorted = list.slice().sort((x, y) => {
      const far = (e) => (e.a.name === card.name ? e.b.y : e.a.y);
      const fx = far(x), fy = far(y);
      return (fx - fy) || (x.key < y.key ? -1 : 1);
    });
    const n = sorted.length;
    const step = Math.min(26, (card.h - 24) / Math.max(1, n));
    sorted.forEach((e, i) => {
      const off = (i - (n - 1) / 2) * step;
      const o = (offsets[e.key] = offsets[e.key] || {});
      if (e.a.name === card.name) o.aOff = off; else o.bOff = off;
    });
  };
  cards.forEach((c) => { assignFan(c, fanR[c.name]); assignFan(c, fanL[c.name]); });
  extCards.forEach((c) => { assignFan(c, fanR[c.name]); assignFan(c, fanL[c.name]); });

  // ── Route construction + candidate lists ──
  // Max runX inset for a same-column lane beside a column: the corridor
  // between columns (open space past the last column gets the generous cap).
  const bowCapFor = (d) => {
    const isLast = d === depths[depths.length - 1];
    if (isLast) return BOW_CAP_OPEN;
    return Math.max(BOW_BASE, colStep - CARD_W - 10);
  };

  // Geometry for a given candidate; returns { start, cp1, cp2, end }.
  const geomFor = (rt, cand) => {
    const { kind, sign, magnitude } = cand;
    const o = offsets[rt.key] || {};
    const aOff = o.aOff || 0, bOff = o.bOff || 0;
    if (kind === "fwd" || kind === "back") {
      const start = kind === "fwd"
        ? { x: rt.a.x + rt.a.w / 2, y: rt.a.y + aOff }
        : { x: rt.a.x - rt.a.w / 2, y: rt.a.y + aOff };
      const end = kind === "fwd"
        ? { x: rt.b.x - rt.b.w / 2, y: rt.b.y + bOff }
        : { x: rt.b.x + rt.b.w / 2, y: rt.b.y + bOff };
      const dx = end.x - start.x;
      const bend = sign * magnitude;
      const cp1 = { x: start.x + dx * 0.35, y: start.y + bend };
      const cp2 = { x: end.x - dx * 0.35, y: end.y + bend };
      return { start, end, segs: [{ p0: start, c1: cp1, c2: cp2, p3: end }] };
    }
    if (kind === "same") {
      // Same-column edge: one smooth cubic bowing out of the column into
      // the corridor (or open space) beside it — the same curved language
      // as the adjacent-column edges.  Each same-column edge in a column
      // gets its own lane width, so nested same-side bows stay ≥20px apart
      // (two bows of the same size on a shared face line always intersect
      // on the return leg; distinct lane widths never do).
      const faceX = rt.a.x + (sign > 0 ? rt.a.w / 2 : -rt.a.w / 2);
      const start = { x: faceX, y: rt.a.y + aOff };
      const end = { x: faceX, y: rt.b.y + bOff };
      const lane = sameColLane[rt.key] || 0;
      const bow = 80 + lane * 70 + magnitude;
      const pull = bow * 0.4;
      const segs = [{
        p0: start,
        c1: { x: faceX + sign * pull, y: start.y },
        c2: { x: faceX + sign * pull, y: end.y },
        p3: end,
      }];
      return { start, end, segs };
    }
    // skip — smooth arch over the intermediate columns (the original flow
    // view's curved shape).  When no single arch can clear (corner-to-
    // corner skips across fully stacked columns) the rounded rise/run/drop
    // below is the fallback: the vertical parts stay in the corridors
    // beside the endpoint columns and 60px corner radii keep it curved
    // instead of a sharp 90° bend.
    const forward = rt.b.x >= rt.a.x;
    const start = forward
      ? { x: rt.a.x + rt.a.w / 2, y: rt.a.y + aOff }
      : { x: rt.a.x - rt.a.w / 2, y: rt.a.y + aOff };
    const end = forward
      ? { x: rt.b.x - rt.b.w / 2, y: rt.b.y + bOff }
      : { x: rt.b.x + rt.b.w / 2, y: rt.b.y + bOff };
    const dx = end.x - start.x;
    const base = Math.min(ARC_MAX, Math.max(ARC_MIN, Math.abs(dx) * 0.28));
    const arcY = sign > 0
      ? Math.min(start.y, end.y) - (base + magnitude)
      : Math.max(start.y, end.y) + (base + magnitude);
    if (cand.smooth) {
      // Steep controls (like the original flow view's skip arcs): the edge
      // launches upward out of the face and crosses corridor edges at a
      // steep angle instead of running alongside them.
      const segs = [{
        p0: start,
        c1: { x: start.x + dx * 0.15, y: arcY },
        c2: { x: end.x - dx * 0.15, y: arcY },
        p3: end,
      }];
      return { start, end, segs };
    }
    const dir = forward ? 1 : -1;
    const insetA = 24 + (skipLaneA[rt.key] || 0) * 26;
    const insetB = 24 + (skipLaneB[rt.key] || 0) * 26;
    const R = 60;
    const riseX = start.x + dir * insetA;
    const dropX = end.x - dir * insetB;
    const run0 = riseX + dir * R;
    const run1 = dropX - dir * R;
    const segs = [
      { p0: start, c1: { x: riseX, y: start.y }, c2: { x: riseX, y: arcY }, p3: { x: run0, y: arcY } },
      { p0: { x: run0, y: arcY }, c1: { x: (run0 + run1) / 2, y: arcY }, c2: { x: (run0 + run1) / 2, y: arcY }, p3: { x: run1, y: arcY } },
      { p0: { x: run1, y: arcY }, c1: { x: dropX, y: arcY }, c2: { x: dropX, y: end.y }, p3: end },
    ];
    return { start, end, segs };
  };

  const routes = [];
  E.forEach((e) => {
    const n = pairCount[e.pairKey] || 1;
    const sep = n > 1 ? (e.pairIdx - (n - 1) / 2) * 2 : 0;
    const baseBend = sep * Math.min(BEND_MAX, Math.max(BEND_MIN, Math.abs(colStep) * 0.24));
    const rt = {
      key: e.key, from: e.from, to: e.to, a: e.a, b: e.b,
      kind: e.a.depth === e.b.depth ? "same"
        : Math.abs(e.a.depth - e.b.depth) > 1 ? "skip"
        : (e.b.depth > e.a.depth ? "fwd" : "back"),
      baseBend, candidates: [], chosen: null,
    };
    if (rt.kind === "same") {
      // Smooth bow: each same-column edge has its own lane width (80 + 70
      // per lane) so nested same-side bows stay ≥20px apart, plus outward
      // escalation rungs for crowded corridors (capped by the corridor
      // width; open space past the last column gets the generous cap).
      const cap = bowCapFor(e.a.depth);
      const bowBase = 80 + (sameColLane[e.key] || 0) * 70;
      const mags = [];
      for (let k = 0; k < 4; k++) {
        const m = k * 56;
        if (bowBase + m <= cap) mags.push(m);
      }
      mags.forEach((m) => rt.candidates.push({ kind: "same", sign: 1, magnitude: m }));
      mags.forEach((m) => rt.candidates.push({ kind: "same", sign: -1, magnitude: m }));
    } else if (rt.kind === "skip") {
      // Two families, smooth arch first — the router picks the first
      // zero-penalty candidate, so smooth curves win whenever they clear —
      // then the rounded rise/run/drop fallback for skips a single arch
      // cannot serve (corner-to-corner across fully stacked columns).
      // The smooth arch's apex sits at 0.125*(y0+y1) + 0.75*arcY; the
      // orthogonal run sits exactly at arcY — each family solves its own
      // exact clearance magnitude, plus the fixed ladder and two higher
      // rungs so the router can dodge other edges' pills by flying higher.
      const y0 = rt.a.y + (offsets[rt.key]?.aOff || 0);
      const y1 = rt.b.y + (offsets[rt.key]?.bOff || 0);
      const dxAbs = Math.abs(rt.b.x - rt.a.x);
      const base = Math.min(ARC_MAX, Math.max(ARC_MIN, dxAbs * 0.28));
      const between = cards.filter((c) => c !== rt.a && c !== rt.b
        && c.depth > Math.min(rt.a.depth, rt.b.depth)
        && c.depth < Math.max(rt.a.depth, rt.b.depth));
      const CLEAR = PILL_H / 2 + PILL_MARGIN + CARD_MARGIN;
      const apexBase = 0.125 * (y0 + y1);
      const minY = Math.min(y0, y1), maxY = Math.max(y0, y1);
      let needAbove = 0, needBelow = 0, needAboveSmooth = 0, needBelowSmooth = 0;
      if (between.length) {
        const minTop = Math.min(...between.map((c) => c.y - c.h / 2));
        const maxBot = Math.max(...between.map((c) => c.y + c.h / 2));
        needAbove = Math.max(0, Math.ceil(minY - base - (minTop - CLEAR)));
        needBelow = Math.max(0, Math.ceil((maxBot + CLEAR) - maxY - base));
        needAboveSmooth = Math.max(0, Math.ceil((apexBase + 0.75 * (minY - base) - (minTop - CLEAR)) / 0.75));
        needBelowSmooth = Math.max(0, Math.ceil((maxBot + CLEAR - apexBase) / 0.75 - maxY - base));
      }
      const ladder = (need) => {
        const list = [];
        for (let k = 0; k < ARC_TRIES; k++) list.push(k * ARC_STEP);
        list.push(need, need + 80, need + 160);
        return Array.from(new Set(list)).sort((a, b) => a - b);
      };
      ladder(needAboveSmooth).forEach((m) => rt.candidates.push({ kind: "skip", sign: 1, magnitude: m, smooth: true }));
      ladder(needBelowSmooth).forEach((m) => rt.candidates.push({ kind: "skip", sign: -1, magnitude: m, smooth: true }));
      ladder(needAbove).forEach((m) => rt.candidates.push({ kind: "skip", sign: 1, magnitude: m, smooth: false }));
      ladder(needBelow).forEach((m) => rt.candidates.push({ kind: "skip", sign: -1, magnitude: m, smooth: false }));
    } else {
      const sign = baseBend >= 0 ? 1 : -1;
      const mag0 = Math.max(BEND_MIN, Math.abs(baseBend));
      for (let k = 0; k < BEND_TRIES; k++) {
        rt.candidates.push({ kind: rt.kind, sign, magnitude: Math.min(BEND_ESCALATE_MAX, mag0 + k * BEND_ESCALATE) });
      }
      for (let k = 0; k < BEND_TRIES; k++) {
        rt.candidates.push({ kind: rt.kind, sign: -sign, magnitude: Math.min(BEND_ESCALATE_MAX, BEND_MIN + k * BEND_ESCALATE) });
      }
    }
    routes.push(rt);
  });

  const applyCandidate = (rt, cand) => {
    const g = geomFor(rt, cand);
    rt.start = g.start; rt.cp1 = g.cp1; rt.cp2 = g.cp2; rt.end = g.end;
    rt.segs = g.segs;
    rt.chosen = cand;
    rt.path = "M " + g.segs[0].p0.x + " " + g.segs[0].p0.y
      + g.segs.map((s) => " C " + s.c1.x + " " + s.c1.y + " " + s.c2.x + " " + s.c2.y + " " + s.p3.x + " " + s.p3.y).join("");
  };

  // ── Collision scoring ──
  const cardRects = () => {
    const rects = [];
    cards.forEach((c) => rects.push({ name: c.name, x: c.x - c.w / 2, y: c.y - c.h / 2, w: c.w, h: c.h }));
    extCards.forEach((c) => rects.push({ name: c.name, x: c.x - c.w / 2, y: c.y - c.h / 2, w: c.w, h: c.h }));
    return rects;
  };
  const rects = cardRects();

  // Sample a route's whole path (all segments) into points.
  const curvePts = (rt, n = SEGS) => {
    const pts = [];
    rt.segs.forEach((s, si) => {
      for (let i = si === 0 ? 0 : 1; i <= n; i++) {
        pts.push(cubicPoint(s.p0, s.c1, s.c2, s.p3, i / n));
      }
    });
    return pts;
  };

  // Point on a route at parameter t (0..1 across all segments evenly).
  const pointOnRoute = (rt, t) => {
    const n = rt.segs.length;
    const idx = Math.min(n - 1, Math.floor(t * n));
    const local = t * n - idx;
    const s = rt.segs[idx];
    return cubicPoint(s.p0, s.c1, s.c2, s.p3, local);
  };

  // 1 per crossing non-endpoint card, weighted by how deep the curve dips
  // into it (kept deterministic — no randomness anywhere).
  const curveVsCardsPenalty = (rt) => {
    const pts = curvePts(rt);
    let pen = 0;
    rects.forEach((r) => {
      if (r.name === rt.a.name || r.name === rt.b.name) return;
      const ir = inflate(r, CARD_MARGIN);
      for (let i = 0; i < pts.length - 1; i++) {
        if (segHitsRect(pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y, ir.x, ir.y, ir.x + ir.w, ir.y + ir.h)) {
          pen += 1;
          break;
        }
      }
    });
    return pen;
  };

  const curveVsCurvePenalty = (rt, others) => {
    const pts = curvePts(rt);
    let pen = 0;
    others.forEach((o) => {
      const shareCard = o.a.name === rt.a.name || o.a.name === rt.b.name
        || o.b.name === rt.a.name || o.b.name === rt.b.name;
      const opts = curvePts(o);
      let hit = false;
      outer:
      for (let i = 0; i < pts.length - 1; i++) {
        for (let j = 0; j < opts.length - 1; j++) {
          const d = segSegDist(pts[i], pts[i + 1], opts[j], opts[j + 1]);
          if (d >= CURVE_GAP) continue;
          // Legitimate meeting at a shared card face: both curves within
          // ATTACH_R of their own endpoints near that card.  Measure from
          // the actual closest point between the two segments — the crossing
          // often sits deep inside the last sampled segment, whose start
          // point lies beyond the attachment radius even though the crossing
          // itself is at the face.
          const cp = segSegClosestPoint(pts[i], pts[i + 1], opts[j], opts[j + 1]);
          const dSelf = Math.min(dist(cp, rt.start), dist(cp, rt.end));
          const dOther = Math.min(dist(cp, o.start), dist(cp, o.end));
          if (shareCard && dSelf < ATTACH_R && dOther < ATTACH_R) continue;
          // Perpendicular point-crossings (a horizontal lane corner crossing
          // another lane's vertical run, circuit-board style) are visually
          // fine; only near-parallel approaches within the gap count.
          const d1x = pts[i + 1].x - pts[i].x, d1y = pts[i + 1].y - pts[i].y;
          const d2x = opts[j + 1].x - opts[j].x, d2y = opts[j + 1].y - opts[j].y;
          const sin = Math.abs(d1x * d2y - d1y * d2x) / (Math.hypot(d1x, d1y) * Math.hypot(d2x, d2y) || 1e-6);
          if (sin > 0.6) continue;
          hit = true;
          break outer;
        }
      }
      if (hit) pen += 1;
    });
    return pen;
  };

  // ── Pass 1: clear every non-endpoint card (order-independent) ──
  routes.forEach((rt) => {
    let best = rt.candidates[0], bestPen = Infinity;
    for (const cand of rt.candidates) {
      applyCandidate(rt, cand);
      const pen = curveVsCardsPenalty(rt);
      if (pen === 0) { best = cand; bestPen = 0; break; }
      if (pen < bestPen) { best = cand; bestPen = pen; }
    }
    applyCandidate(rt, best);
  });

  // ── Pass 2: iterative refinement — curves + pills, to a fixpoint ──
  // Each route re-picks its candidate minimizing curve↔card crossings,
  // curve↔curve approaches AND crossings through other edges' label pill
  // rects; pills are then re-placed on the new curves.  Iterating lets a
  // blocked label (e.g. a pill whose slide range is cut off by its own
  // endpoint card) bend its curve around the other edges' pills instead —
  // the galaxy view's arc-over corridor idea applied per edge.  Stops when
  // no pill↔pill overlap remains (or the iteration cap hits).
  const pillRectFor = (rt, pillsMap) => {
    const pl = pillsMap[rt.key];
    if (!pl) return null;
    const pw = (pillWFor[rt.key] || 0) + PILL_W_PAD;
    return { x: pl.x - pw / 2, y: pl.y - PILL_H / 2, w: pw, h: PILL_H };
  };

  const curveVsPillPenalty = (rt, pillRects) => {
    const pts = curvePts(rt);
    let pen = 0;
    pillRects.forEach((pr) => {
      const ir = { x: pr.x - 6, y: pr.y - 6, w: pr.w + 12, h: pr.h + 12 };
      for (let i = 0; i < pts.length - 1; i++) {
        if (segHitsRect(pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y, ir.x, ir.y, ir.x + ir.w, ir.y + ir.h)) {
          pen += 2; // a curve through a label is worse than two lines near each other
          break;
        }
      }
    });
    return pen;
  };

  const countPillOverlaps = (pillsMap) => {
    const list = routes.map((rt) => ({ key: rt.key, rect: pillRectFor(rt, pillsMap) })).filter((p) => p.rect);
    let n = 0;
    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        const a = list[i].rect, b = list[j].rect;
        const ix = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
        const iy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
        if (ix > 0 && iy > 0) n++;
      }
    }
    return n;
  };

  let pillsByKey = placeFlowPills(routes, rects, pillWFor);
  const ROUTE_REFINE_TRIES = 4;
  for (let iter = 0; iter < ROUTE_REFINE_TRIES; iter++) {
    routes.forEach((rt) => {
      const others = routes.filter((o) => o !== rt);
      const otherPills = routes
        .filter((o) => o !== rt)
        .map((o) => pillRectFor(o, pillsByKey))
        .filter(Boolean);
      let best = rt.candidates[0], bestPen = Infinity;
      for (const cand of rt.candidates) {
        applyCandidate(rt, cand);
        const pen = curveVsCardsPenalty(rt) + curveVsCurvePenalty(rt, others) + curveVsPillPenalty(rt, otherPills);
        if (pen === 0) { best = cand; bestPen = 0; break; }
        if (pen < bestPen) { best = cand; bestPen = pen; }
      }
      applyCandidate(rt, best);
    });
    const next = placeFlowPills(routes, rects, pillWFor);
    pillsByKey = next;
    if (countPillOverlaps(next) === 0) break;
  }

  // ── Final pills: slide ALONG each curve to the first clear spot ──
  const pills = pillsByKey;

  // ── Bounds: cards + externals + sampled curves + pills ──
  let l = Infinity, r = -Infinity, t = Infinity, b = -Infinity;
  const grow = (x0, y0, x1, y1) => {
    l = Math.min(l, x0); r = Math.max(r, x1);
    t = Math.min(t, y0); b = Math.max(b, y1);
  };
  rects.forEach((rc) => grow(rc.x, rc.y, rc.x + rc.w, rc.y + rc.h));
  routes.forEach((rt) => {
    curvePts(rt, 12).forEach((p) => grow(p.x, p.y, p.x, p.y));
  });
  Object.values(pills).forEach((pl) => {
    const pw = (pillWFor[pl.key] || 0) + PILL_W_PAD;
    grow(pl.x - pw / 2, pl.y - PILL_H / 2, pl.x + pw / 2, pl.y + PILL_H / 2);
  });
  if (!isFinite(l)) { l = 0; r = 0; t = 0; b = 0; }

  return {
    positions: cards.map((c) => ({ repo: c.name, x: c.x, y: c.y, w: c.w, h: c.h })),
    externalPos: extCards.map((c) => ({ key: c.name, targetRepo: c.targetRepo, x: c.x, y: c.y, w: c.w, h: c.h })),
    routes: routes.map((rt) => ({
      key: rt.key, from: rt.from, to: rt.to,
      skip: rt.kind === "skip",
      path: rt.path,
      start: rt.start, cp1: rt.cp1, cp2: rt.cp2, end: rt.end,
      segs: rt.segs,
      mid: pointOnRoute(rt, 0.5),
    })),
    pills,
    bounds: { l, r, t, b },
    colStep,
  };
}

// ── Coordinated pill placement (shared with the renderer) ──
//
// Pills are placed widest-first and each slides ALONG its own curve to the
// first spot whose rect clears every card (endpoints included — a label must
// never sit on a card) and every previously placed pill.  The label therefore
// always sits exactly on its edge line — the old "lift above the column"
// fallback is gone.  A pill with no fully clear spot takes the least-bad
// candidate (deterministic tie-break).  Returns { key: { t, x, y, key } }.
export function placeFlowPills(routes, rects, pillWFor) {
  const ts = [0.5, 0.45, 0.55, 0.4, 0.6, 0.35, 0.65, 0.3, 0.7, 0.25, 0.75, 0.2, 0.8, 0.15, 0.85, 0.1, 0.9, 0.05, 0.95];
  const order = routes
    .map((rt) => ({ rt, pw: (pillWFor[rt.key] || 0) + PILL_W_PAD }))
    .filter((o) => o.pw > PILL_W_PAD)
    .sort((A, B) => (B.pw - A.pw) || (A.rt.key < B.rt.key ? -1 : 1));

  const placed = [];
  const out = {};
  for (const { rt, pw } of order) {
    let best = null;
    for (const t of ts) {
      const p = pointOnRouteLocal(rt, t);
      const rect = { x: p.x - pw / 2, y: p.y - PILL_H / 2, w: pw, h: PILL_H };
      let pen = 0;
      for (const c of rects) {
        const ir = inflate(c, PILL_MARGIN);
        const ix = Math.min(rect.x + rect.w, ir.x + ir.w) - Math.max(rect.x, ir.x);
        const iy = Math.min(rect.y + rect.h, ir.y + ir.h) - Math.max(rect.y, ir.y);
        if (ix > 0 && iy > 0) pen += Math.min(ix, iy) + 1;
      }
      for (const pl of placed) {
        const ix = Math.min(rect.x + rect.w, pl.x + pl.w) - Math.max(rect.x, pl.x);
        const iy = Math.min(rect.y + rect.h, pl.y + pl.h) - Math.max(rect.y, pl.y);
        if (ix > 0 && iy > 0) pen += Math.min(ix, iy) + 1;
      }
      if (pen === 0) { best = { t, p, rect }; break; }
      if (!best || pen < best.pen) best = { t, p, rect, pen };
    }
    placed.push({ x: best.rect.x, y: best.rect.y, w: best.rect.w, h: best.rect.h });
    out[rt.key] = { t: best.t, x: best.p.x, y: best.p.y, key: rt.key };
  }
  return out;
}

// Point on a route at parameter t (0..1, evenly split across its segments).
const pointOnRouteLocal = (rt, t) => {
  const n = rt.segs.length;
  const idx = Math.min(n - 1, Math.floor(t * n));
  const local = t * n - idx;
  const s = rt.segs[idx];
  return cubicPoint(s.p0, s.c1, s.c2, s.p3, local);
};
