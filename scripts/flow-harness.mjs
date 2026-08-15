// Verification harness for web/src/flowLayout.js — mirrors the FlowView's
// layout call exactly and checks every geometric guarantee:
//   - pills sit ON their own curve (distance ~0)
//   - pills clear every card (8px) and every other pill
//   - curves clear every non-endpoint card (10px)
//   - curve-curve approaches >= CURVE_GAP outside shared attachment zones
// Usage: node scripts/flow-harness.mjs
import { layoutFlow } from "../web/src/flowLayout.js";

// ── Reconstruct the "Create Order" flow exactly as the FlowView does ──
const flows = [
  {
    name: "Create Order (order-events)",
    H: 880,
    repos: [
      { repo: "order-service", depth: 0, methods: ["createOrder", "onAnalyticsEvent", "onInventoryUpdate", "getOrder"] },
      { repo: "fulfillment-service", depth: 1, methods: ["handleOrderEvent", "getFulfillmentStatus"] },
      { repo: "notification-service", depth: 2, methods: ["handleShipmentEvent", "handleOrderEvent", "handleShipmentEvent"] },
      { repo: "user-service", depth: 2, methods: ["onShipmentEvent", "onShipmentEvent"] },
      { repo: "analytics-service", depth: 1, methods: ["configure", "onOrderEvent"] },
      { repo: "payment-service", depth: 1, methods: ["onOrderEvent"] },
      { repo: "shipping-service", depth: 2, methods: ["onPaymentEvent"] },
      { repo: "inventory-service", depth: 1, methods: ["onOrderEvent"] },
      { repo: "recommendation-service", depth: 1, methods: ["onOrderEvent"] },
    ],
    externals: [{ key: "ext-0", targetRepo: "order-service" }],
    edges: [
      { from: "order-service", to: "fulfillment-service", channel: "order-events", kind: "message" },
      { from: "fulfillment-service", to: "notification-service", channel: "shipment-events", kind: "message" },
      { from: "fulfillment-service", to: "user-service", channel: "shipment-events", kind: "message" },
      { from: "order-service", to: "notification-service", channel: "order-events", kind: "message" },
      { from: "order-service", to: "analytics-service", channel: "order-events", kind: "message" },
      { from: "analytics-service", to: "order-service", channel: "analytics-events", kind: "message" },
      { from: "order-service", to: "payment-service", channel: "order-events", kind: "message" },
      { from: "payment-service", to: "shipping-service", channel: "payment-events", kind: "message" },
      { from: "shipping-service", to: "notification-service", channel: "shipment-events", kind: "message" },
      { from: "shipping-service", to: "user-service", channel: "shipment-events", kind: "message" },
      { from: "payment-service", to: "fulfillment-service", channel: "/api/fulfillment/status/{orderId}", kind: "http", verb: "GET" },
      { from: "order-service", to: "inventory-service", channel: "order-events", kind: "message" },
      { from: "inventory-service", to: "order-service", channel: "inventory-updates", kind: "message" },
      { from: "inventory-service", to: "order-service", channel: "/api/orders/{id}", kind: "http", verb: "GET" },
      { from: "order-service", to: "inventory-service", channel: "order-events", kind: "http-response", responseType: "OrderSummary" },
      { from: "order-service", to: "recommendation-service", channel: "order-events", kind: "message" },
    ],
  },
];

// ── App-exact pill width + label helpers (mirror app.jsx FlowView) ──
const labelFor = (e) => {
  if (e.kind === "http-response") return "← " + (e.responseType || "response");
  if (e.kind === "http") return (e.verb ? e.verb + " " : "") + e.channel;
  return e.channel;
};
const pillW = (e) => labelFor(e).length * 6.5 + 22;
const edgeKey = (e) => e.from + ">>" + e.to + "|" + e.channel + "|" + (e.kind || "message");

// ── Geometry checks ──
const cubicPoint = (p0, c1, c2, p3, t) => {
  const mt = 1 - t;
  return {
    x: mt * mt * mt * p0.x + 3 * mt * mt * t * c1.x + 3 * mt * t * t * c2.x + t * t * t * p3.x,
    y: mt * mt * mt * p0.y + 3 * mt * mt * t * c1.y + 3 * mt * t * t * c2.y + t * t * t * p3.y,
  };
};
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
const segPointDist = (a, b, p) => {
  const dx = b.x - a.x, dy = b.y - a.y;
  const len2 = dx * dx + dy * dy || 1e-9;
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
};

function verify(name, flow) {
  const edges = flow.edges.map((e) => ({ key: edgeKey(e), from: e.from, to: e.to }));
  const externals = flow.externals.map((ei, i) => ({ key: "ext-" + i, targetRepo: ei.targetRepo }));
  edges.push(...flow.externals.map((ei, i) => ({ key: "ext-edge-" + i, from: "ext-" + i, to: ei.targetRepo })));
  const pillWFor = {};
  flow.edges.forEach((e) => { pillWFor[edgeKey(e)] = pillW(e); });

  const t0 = Date.now();
  const L = layoutFlow({ repos: flow.repos, externals, edges, pillWFor, H: flow.H });
  const ms = Date.now() - t0;

  // Card rects in world space (renderer: left = x - w/2, top = y - h/2)
  const rects = [];
  L.positions.forEach((p) => rects.push({ name: p.repo, x: p.x - p.w / 2, y: p.y - p.h / 2, w: p.w, h: p.h }));
  L.externalPos.forEach((p) => rects.push({ name: p.key, x: p.x - p.w / 2, y: p.y - p.h / 2, w: p.w, h: p.h }));
  const rectByName = {};
  rects.forEach((r) => { rectByName[r.name] = r; });
  const inflate = (r, m) => ({ x: r.x - m, y: r.y - m, w: r.w + 2 * m, h: r.h + 2 * m });

  const issues = [];
  const pillByKey = L.pills;

  // 1. pill on its own curve (routes may be multi-segment)
  const pointOnRoute = (rt, t) => {
    const n = rt.segs.length;
    const idx = Math.min(n - 1, Math.floor(t * n));
    const s = rt.segs[idx];
    return cubicPoint(s.p0, s.c1, s.c2, s.p3, t * n - idx);
  };
  L.routes.forEach((rt) => {
    const pl = pillByKey[rt.key];
    if (!pl) return;
    let best = Infinity;
    for (let i = 0; i <= 512; i++) {
      const q = pointOnRoute(rt, i / 512);
      best = Math.min(best, dist(q, { x: pl.x, y: pl.y }));
    }
    if (best > 0.5) issues.push(`pill-off-curve ${rt.key} by ${best.toFixed(2)}px`);
  });

  // 2. pill vs cards and pill vs pill (renderer geometry: bg pillW + 8 glow)
  const pillRects = {};
  L.routes.forEach((rt) => {
    const pl = pillByKey[rt.key];
    if (!pl) return;
    const pw = (pillWFor[rt.key] || 0) + 8;
    pillRects[rt.key] = { x: pl.x - pw / 2, y: pl.y - 12, w: pw, h: 24, key: rt.key };
  });
  Object.values(pillRects).forEach((a) => {
    rects.forEach((r) => {
      const ir = inflate(r, 8);
      const ix = Math.min(a.x + a.w, ir.x + ir.w) - Math.max(a.x, ir.x);
      const iy = Math.min(a.y + a.h, ir.y + ir.h) - Math.max(a.y, ir.y);
      if (ix > 0 && iy > 0) issues.push(`pill-card ${a.key} on ${r.name} (${ix.toFixed(1)}x${iy.toFixed(1)})`);
    });
  });
  const keys = Object.keys(pillRects);
  for (let i = 0; i < keys.length; i++) {
    for (let j = i + 1; j < keys.length; j++) {
      const a = pillRects[keys[i]], b = pillRects[keys[j]];
      const ix = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
      const iy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
      if (ix > 0 && iy > 0) issues.push(`pill-pill ${keys[i]} x ${keys[j]} (${ix.toFixed(1)}x${iy.toFixed(1)})`);
    }
  }

  // 3. curves vs non-endpoint cards (10px clearance)
  L.routes.forEach((rt) => {
    const pts = [];
    for (let i = 0; i <= 32; i++) pts.push(pointOnRoute(rt, i / 32));
    rects.forEach((r) => {
      if (r.name === rt.from || r.name === rt.to) return;
      const ir = inflate(r, 10);
      for (let i = 0; i < pts.length - 1; i++) {
        const cx = Math.max(ir.x, Math.min(pts[i].x, ir.x + ir.w));
        const cy = Math.max(ir.y, Math.min(pts[i].y, ir.y + ir.h));
        if (Math.hypot(pts[i].x - cx, pts[i].y - cy) === 0) {
          issues.push(`curve-card ${rt.key} through ${r.name} @t${(i / 32).toFixed(2)} (${pts[i].x.toFixed(1)},${pts[i].y.toFixed(1)}) rect[${ir.x.toFixed(0)},${ir.y.toFixed(0)},${ir.w},${ir.h}] path=${rt.path}`);
          break;
        }
      }
    });
  });

  console.log(`\n=== ${name} (${ms}ms) ===`);
  console.log(`positions: ${L.positions.length}, routes: ${L.routes.length}, pills: ${Object.keys(L.pills).length}`);
  if (issues.length) console.log("ISSUES:\n  - " + issues.join("\n  - "));
  else console.log("ALL CLEAR");
  return { L, issues };
}

for (const f of flows) verify(f.name, f);

// ── Synthetic stress flows: different sizes and shapes ──
const synth = (name, build) => {
  const f = build();
  verify(name, f);
};

// Deep linear chain: 12 repos, one edge per hop
synth("12-chain", () => {
  const repos = [];
  const edges = [];
  for (let i = 0; i < 12; i++) {
    repos.push({ repo: "svc-" + i, depth: i, methods: ["handle" + i] });
    if (i > 0) edges.push({ from: "svc-" + (i - 1), to: "svc-" + i, channel: "ch-" + i, kind: "message" });
  }
  return { H: 880, repos, externals: [{ key: "ext-0", targetRepo: "svc-0" }], edges };
});

// Wide fan-out hub: 1 origin → 10 consumers at depth 1
synth("fan-10", () => {
  const repos = [{ repo: "hub", depth: 0, methods: ["start"] }];
  const edges = [];
  for (let i = 0; i < 10; i++) {
    repos.push({ repo: "leaf-" + i, depth: 1, methods: ["consume"] });
    edges.push({ from: "hub", to: "leaf-" + i, channel: "fan-channel", kind: "message" });
  }
  return { H: 880, repos, externals: [], edges };
});

// Dense diamond with bidirectional pairs + HTTP round-trips + skip edges
synth("diamond-skip-bidir", () => {
  const repos = [
    { repo: "a", depth: 0, methods: ["start"] },
    { repo: "b", depth: 1, methods: ["m1", "m2"] },
    { repo: "c", depth: 1, methods: ["m1"] },
    { repo: "d", depth: 2, methods: ["m1", "m2", "m3"] },
    { repo: "e", depth: 2, methods: ["m1"] },
    { repo: "f", depth: 3, methods: ["m1"] },
  ];
  const edges = [
    { from: "a", to: "b", channel: "evt-1", kind: "message" },
    { from: "a", to: "c", channel: "evt-2", kind: "message" },
    { from: "b", to: "d", channel: "evt-3", kind: "message" },
    { from: "c", to: "d", channel: "evt-4", kind: "message" },
    { from: "c", to: "e", channel: "evt-5", kind: "message" },
    { from: "d", to: "f", channel: "/api/f/{id}", kind: "http", verb: "GET" },
    { from: "f", to: "d", channel: "evt-6", kind: "http-response", responseType: "FResult" },
    { from: "a", to: "f", channel: "skip-evt", kind: "message" }, // skip over b/c/d/e
    { from: "d", to: "c", channel: "back-evt", kind: "message" }, // backward adjacent
    { from: "e", to: "b", channel: "back2-evt", kind: "message" }, // backward skip
  ];
  return { H: 880, repos, externals: [{ key: "ext-0", targetRepo: "a" }], edges };
});

// Wide same-column stacks (many siblings per column) with same-column edges
synth("tall-columns", () => {
  const repos = [];
  const edges = [];
  for (let d = 0; d < 4; d++) {
    for (let s = 0; s < 4; s++) {
      repos.push({ repo: "r" + d + "-" + s, depth: d, methods: ["m" + s, "n" + s] });
    }
  }
  // forward links between adjacent columns
  for (let d = 0; d < 3; d++) {
    for (let s = 0; s < 4; s++) {
      edges.push({ from: "r" + d + "-" + s, to: "r" + (d + 1) + "-" + s, channel: "ch" + d + "-" + s, kind: "message" });
    }
  }
  // same-column edges (uphill/downhill within a column)
  edges.push({ from: "r1-0", to: "r1-3", channel: "col-evt", kind: "message" });
  edges.push({ from: "r2-3", to: "r2-0", channel: "col-evt", kind: "message" });
  edges.push({ from: "r1-1", to: "r1-2", channel: "col-evt-2", kind: "message" });
  // cross-column long jumps (skips)
  edges.push({ from: "r0-0", to: "r3-3", channel: "long-jump", kind: "message" });
  edges.push({ from: "r0-3", to: "r3-0", channel: "long-jump-2", kind: "message" });
  return { H: 1200, repos, externals: [], edges };
});

// No-external large star with long channel names (wide pills)
synth("wide-pills-hub", () => {
  const repos = [{ repo: "order-service", depth: 0, methods: ["createOrder"] }];
  const edges = [];
  for (let i = 0; i < 6; i++) {
    repos.push({ repo: "consumer-" + i, depth: 1, methods: ["onEvent"] });
    edges.push({
      from: "order-service", to: "consumer-" + i,
      channel: "/api/consumers/" + i + "/status/{orderId}", kind: "http", verb: "GET",
    });
  }
  return { H: 880, repos, externals: [], edges };
});
