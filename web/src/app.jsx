/* ============================================================
   CONSTELLATION — Codebase Mapper frontend
   React 18 + Vite (ES module build)
   ============================================================ */

import React, { useState, useEffect, useRef, useMemo, useLayoutEffect, useCallback } from "react";
import { marked } from "marked";
import "./styles.css";
import {
  detectFlows,
  buildServiceStats,
  landscapeSummary,
  entryEmits,
  flowsByChannel,
  channelMessageTypes,
  ROLE_META,
} from "./derived.js";

/* ---------------- helpers ---------------- */
const repoFromId = (id) => (typeof id === "string" ? id.split(":")[0] : "");

const TYPE_META = {
  "rest-endpoint":     { color: "#ff4d6d", label: "REST",      glow: "rgba(255,77,109,.55)" },
  "kafka-consumer":    { color: "#ffd60a", label: "Kafka",     glow: "rgba(255,214,10,.55)" },
  "rabbitmq-consumer": { color: "#ff8c42", label: "RabbitMQ",  glow: "rgba(255,140,66,.55)" },
  "event-listener":    { color: "#4895ef", label: "Event",     glow: "rgba(72,149,239,.55)" },
  "scheduled-task":    { color: "#34d399", label: "Scheduled", glow: "rgba(52,211,153,.55)" },
  "websocket":         { color: "#a855f7", label: "WebSocket", glow: "rgba(168,85,247,.55)" },
  "jms-consumer":      { color: "#2dd4bf", label: "JMS",       glow: "rgba(45,212,191,.55)" },
  "sqs-consumer":      { color: "#e879f9", label: "SQS",       glow: "rgba(232,121,249,.55)" },
  // Extra framework entry kinds (deterministic detection).
  "servlet":           { color: "#38bdf8", label: "Servlet",   glow: "rgba(56,189,248,.55)" },
  "soap-service":      { color: "#d4a373", label: "SOAP",      glow: "rgba(212,163,115,.55)" },
  "graphql":           { color: "#f472b6", label: "GraphQL",   glow: "rgba(244,114,182,.55)" },
  "grpc-service":      { color: "#14b8a6", label: "gRPC",      glow: "rgba(20,184,166,.55)" },
  "lifecycle":         { color: "#64748b", label: "Lifecycle", glow: "rgba(100,116,139,.55)" },
  "main":              { color: "#818cf8", label: "Main",      glow: "rgba(129,140,248,.55)" },
  "cloud-function":    { color: "#c084fc", label: "Function",  glow: "rgba(192,132,252,.55)" },
};

// Galaxy edge colors by link kind: async (message-only), sync (HTTP-only), both (mixed)
const EDGE_KINDS = {
  async: { color: "#00d4ff", label: "Async" },
  sync:  { color: "#00e0a8", label: "Sync HTTP" },
  both:  { color: "#a78bfa", label: "Both" },
};
const typeMeta = (t) => TYPE_META[t] || { color: "#94a3b8", label: (t || "Unknown"), glow: "rgba(148,163,184,.5)" };

// Display name for a call node: Class.method when the engine resolved the
// class, plain method/receiver form otherwise.
const nodeDisplayName = (d) => {
  const method = d.method || "";
  const cls = d.class_name || "";
  if (cls && method && !method.includes(".")) return cls + "." + method;
  return method || cls || "unknown";
};

const CONFIDENCE = {
  EXTRACTED: { color: "#34d399" },
  INFERRED:  { color: "#fbbf24" },
  AMBIGUOUS: { color: "#f87171" },
  TRUNCATED: { color: "#a78bfa" },
};
const confMeta = (c) => CONFIDENCE[c] || { color: "#94a3b8" };

function renderMarkdown(src) {
  if (!src) return "";
  return sanitizeHTML(marked.parse(src, { breaks: true }));
}

function sanitizeHTML(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  doc.querySelectorAll("script,style,iframe,object,embed,link,meta").forEach((el) => el.remove());
  doc.querySelectorAll("*").forEach((el) => {
    [...el.attributes].forEach((a) => {
      const name = a.name.toLowerCase();
      if (name.startsWith("on")) { el.removeAttribute(a.name); return; }
      if ((name === "href" || name === "src") && a.value.trim().toLowerCase().startsWith("javascript:")) {
        el.removeAttribute(a.name);
      }
    });
  });
  return doc.body.innerHTML;
}

function escapeHTML(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function fetchJSON(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error("HTTP " + res.status + " " + res.statusText);
  return res.json();
}

function fmtFile(path) {
  if (!path) return "";
  const p = path.split("/");
  return p.slice(-2).join("/");
}

function sameNode(a, b) {
  return !!(a && b && a.method === b.method && a.file === b.file && a.line === b.line);
}

// When a node is selected on a deep view, the right-hand detail panel and/or the
// AI chat window can cover it. This computes a viewport offset that nudges the
// selected node into the unobstructed area (immediately left of the panel w/live DOM
// because .detail-panel is `position:fixed; right:0` and can move with the chat open).
// Returns {x, y} to move to, or null if the node is already fully clear.
function computeRevealOffset(containerRef, layout, selectedNode, viewport) {
  if (!selectedNode) return null;
  const el = containerRef.current;
  if (!el) return null;
  const hit = layout.nodes.find((n) => sameNode(n.data, selectedNode));
  if (!hit) return null;

  const z = viewport.zoom;
  // Prefer the real mounted node's geometry when available (labels/chips make it wider than PV_NODE_W)
  const selEl = el.querySelector(".pv-node.sel .pv-node-body");
  let nodeL, nodeR, nodeT, nodeB;
  if (selEl) {
    const crect = el.getBoundingClientRect();
    const r = selEl.getBoundingClientRect();
    nodeL = r.left - crect.left;
    nodeT = r.top - crect.top;
    nodeR = r.right - crect.left;
    nodeB = r.bottom - crect.top;
  } else {
    nodeL = viewport.x + hit.x * z;
    nodeT = viewport.y + hit.y * z;
    nodeR = nodeL + PV_NODE_W * z;
    nodeB = nodeT + PV_NODE_H * z;
  }

  // Clear-space boundaries relative to the canvas origin (crect.left may be >0 for a wrapper).
  const crect = el.getBoundingClientRect();
  let clearRight = crect.width;   // rightmost px the node may occupy, from canvas left
  let clearBottom = crect.height;

  const panel = document.querySelector(".detail-panel");
  if (panel) {
    // .detail-panel is position:fixed; right:0, but animates in with `panelIn`
    // (translateX(40px -> 0)). Reading getBoundingClientRect() mid-animation
    // overstates pr.left by up to ~40px, so the first nudge comes up short and
    // a second click later fixes it. Use the SETTLED left instead: viewport
    // width minus the panel's own width (== its right:0 resting position).
    const pw = panel.offsetWidth || panel.getBoundingClientRect().width;
    const settledLeft = window.innerWidth - pw;
    clearRight = Math.min(clearRight, settledLeft - crect.left - 16);
  }
  const chat = document.querySelector(".global-chat.open .chat-window");
  if (chat) {
    // Same settled-position trick as the panel: the window animates in with
    // chatSlideUp (translateY + scale 0.95) and the wrapper <transition>s
    // right, so getBoundingClientRect() mid-animation under-reports edges.
    // Derive the window's settled box from constants instead:
    //   wrapper is fixed at bottom:24px, right:24px — or right: calc(panel + 24px)
    //   when .detail-open (desktop only; mobile forces right:24 via media query).
    const wrap = chat.closest(".global-chat");
    const ww = chat.offsetWidth || chat.getBoundingClientRect().width;
    const wh = chat.offsetHeight || chat.getBoundingClientRect().height;
    let wrapRight = 24;
    if (panel && wrap && wrap.classList.contains("detail-open") && window.innerWidth > 720) {
      wrapRight = (panel.offsetWidth || 460) + 24;
    }
    const settledChatLeft = window.innerWidth - wrapRight - ww;
    const settledChatTop = window.innerHeight - 24 - wh;
    clearRight = Math.min(clearRight, settledChatLeft - crect.left - 16);
    clearBottom = Math.min(clearBottom, settledChatTop - crect.top - 16);
  }

  let dx = 0, dy = 0;
  if (nodeR > clearRight) dx = clearRight - nodeR;               // push left out from under panel
  if (nodeL + dx < 8 && dx < 0) dx = 8 - nodeL;                  // don't drive it past the left edge
  if (nodeB > clearBottom) dy = clearBottom - nodeB;             // push up out from under chat
  if (nodeT + dy < 8 && dy < 0) dy = 8 - nodeT;

  if (dx === 0 && dy === 0) return null;
  return { x: viewport.x + dx, y: viewport.y + dy, zoom: viewport.zoom };
}

function findRelations(root, target) {
  let parent = null, calls = [];
  if (!root) return { parent, calls };
  (function walk(n, p) {
    if (sameNode(n, target)) { parent = p; calls = n.children || []; return true; }
    for (const c of (n.children || [])) { if (walk(c, n)) return true; }
    return false;
  })(root, null);
  return { parent, calls };
}

/* ---------------- usePanZoom (shared infinite-canvas hook) ---------------- */
// Returns everything needed for a pan/zoom container:
// { containerRef, viewport, animating, dragRef, handlers..., zoomControls }
// - clickSelector: CSS selector for elements that should NOT trigger a pan (e.g. ".repo-node, .flow-card")
//   clicks on these are passed through; clicks on empty space start a pan
function usePanZoom(clickSelector) {
  const containerRef = useRef(null);
  const [viewport, setViewport] = useState({ x: 0, y: 0, zoom: 1 });
  const [animating, setAnimating] = useState(false);
  const dragRef = useRef({ active: false, startX: 0, startY: 0, vpX: 0, vpY: 0, moved: false });

  const reset = useCallback((vp) => {
    setAnimating(true);
    setViewport(vp || { x: 0, y: 0, zoom: 1 });
    setTimeout(() => setAnimating(false), 400);
  }, []);

  const zoomBy = useCallback((delta) => {
    setAnimating(true);
    setViewport((vp) => ({ ...vp, zoom: Math.max(0.2, Math.min(3, vp.zoom + delta)) }));
    setTimeout(() => setAnimating(false), 400);
  }, []);

  const onMouseDown = useCallback((e) => {
    if (clickSelector && e.target.closest(clickSelector)) return;
    dragRef.current = {
      active: true,
      startX: e.clientX,
      startY: e.clientY,
      vpX: viewport.x,
      vpY: viewport.y,
      moved: false,
    };
  }, [viewport.x, viewport.y, clickSelector]);

  const onMouseMove = useCallback((e) => {
    if (!dragRef.current.active) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) dragRef.current.moved = true;
    setViewport((vp) => ({ ...vp, x: dragRef.current.vpX + dx, y: dragRef.current.vpY + dy }));
  }, []);

  const onMouseUp = useCallback(() => { dragRef.current.active = false; }, []);

  const onWheel = useCallback((e) => {
    e.preventDefault();
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    setViewport((vp) => {
      const delta = -e.deltaY * 0.0015;
      const newZoom = Math.max(0.2, Math.min(3, vp.zoom * (1 + delta)));
      const zr = newZoom / vp.zoom;
      return { x: mx - (mx - vp.x) * zr, y: my - (my - vp.y) * zr, zoom: newZoom };
    });
  }, []);

  // Wheel needs passive: false
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handler = (e) => onWheel(e);
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, [onWheel]);

  const zoomControls = (
    <div className="zoom-controls">
      <button onClick={() => zoomBy(0.15)} title="Zoom in">+</button>
      <span className="zoom-level">{Math.round(viewport.zoom * 100)}%</span>
      <button onClick={() => zoomBy(-0.15)} title="Zoom out">−</button>
      <button onClick={() => reset({ x: 0, y: 0, zoom: 1 })} title="Reset view">⤢</button>
    </div>
  );

  return {
    containerRef,
    viewport,
    setViewport,
    animating,
    setAnimating,
    dragRef,
    reset,
    zoomBy,
    handlers: { onMouseDown, onMouseMove, onMouseUp, onMouseLeave: onMouseUp },
    zoomControls,
  };
}

/* ---------------- Starfield ---------------- */
function Starfield() {
  return (
    <div className="starfield" aria-hidden="true">
      <div className="stars-layer s1"></div>
      <div className="stars-layer s2"></div>
      <div className="stars-layer s3"></div>
      <div className="nebula"></div>
    </div>
  );
}

/* ---------------- Loading / Error ---------------- */
function LoadingScreen() {
  return (
    <div className="screen-center">
      <div className="orbit-loader"><span></span></div>
      <div className="loader-text">Mapping the codebase galaxy…</div>
    </div>
  );
}
function ErrorScreen({ message }) {
  return (
    <div className="screen-center">
      <div className="err-icon">⚠</div>
      <h2>Couldn't load the graph</h2>
      <p className="muted">{message}</p>
      <p className="muted small">
        Make sure the Constellation backend is running and <code>/api/graph</code> is reachable.
      </p>
    </div>
  );
}

/* ---------------- Header ---------------- */
function Header({ graph, mode, onModeChange, projectName, onHome, stale }) {
  const gen = graph && graph.generated_at
    ? new Date(graph.generated_at).toLocaleString()
    : "";
  const statusLabel = stale ? "Stale" : "Up to date";
  const statusCls = stale ? "stale" : "ok";
  return (
    <header className="topbar glass">
      <div className="brand">
        <button className="brand-mark-btn" onClick={onHome} title="Back to projects">
          <span className="brand-mark">✦</span>
        </button>
        <div>
          <div className="brand-name">CONSTELLATION</div>
          <div className="brand-sub">
            {onHome && projectName ? (
              <span className="brand-crumb">
                <button className="crumb back link" onClick={onHome} title="Back to projects">
                  <span className="crumb-arrow">←</span> Projects
                </button>
                <span className="crumb-sep">›</span>
                <span className="crumb">{projectName}</span>
              </span>
            ) : "Codebase Mapper"}
          </div>
        </div>
      </div>
      {onModeChange && (
        <div className="mode-toggle">
          <button
            className={"mode-btn" + (mode === "topology" ? " active" : "")}
            onClick={() => onModeChange("topology")}
          >System</button>
          <button
            className={"mode-btn" + (mode === "flows" ? " active" : "")}
            onClick={() => onModeChange("flows")}
          >Flows</button>
        </div>
      )}
      <div className="meta">
        {gen && (
          <span className={"meta-pill status-" + statusCls}>
            <span className="pill-date" aria-hidden="true">{gen ? "Last scanned: " + gen : ""}</span>
            <span className="pill-status">
              <span className="status-dot" />
              <span className="status-label">{statusLabel}</span>
            </span>
          </span>
        )}
      </div>
    </header>
  );
}

/* ---------------- Breadcrumb ---------------- */
function Breadcrumb({ items }) {
  return (
    <nav className="breadcrumb">
      {items.map((it, i) => (
        <span className="crumb-wrap" key={i}>
          {i > 0 && <span className="crumb-sep">›</span>}
          {it.onClick
            ? <button className="crumb link" onClick={it.onClick}>{it.label}</button>
            : <span className="crumb">{it.label}</span>}
        </span>
      ))}
    </nav>
  );
}

/* ---------------- Legend ---------------- */
function Legend({ types = [], roles = [] }) {
  const shown = Object.keys(TYPE_META).filter((k) => types.includes(k));
  const shownRoles = Object.keys(ROLE_META).filter((k) => roles.includes(k));
  return (
    <div className="legend glass">
      <div className="legend-title">Entry point types</div>
      {shown.length === 0 ? (
        <div className="legend-item">No entry points</div>
      ) : (
        shown.map((k) => (
          <div className="legend-item" key={k}>
            <span className="legend-dot" style={{ background: TYPE_META[k].color, color: TYPE_META[k].color }}></span>
            {TYPE_META[k].label}
          </div>
        ))
      )}
      {shownRoles.length > 0 && (
        <>
          <div className="legend-sep">Service role</div>
          {shownRoles.map((k) => (
            <div className="legend-item" key={k}>
              <span className="legend-dot" style={{ background: ROLE_META[k].color, color: ROLE_META[k].color }}></span>
              {ROLE_META[k].label}
            </div>
          ))}
        </>
      )}
      <div className="legend-sep">Links</div>
      {["async", "sync", "both"].map((k) => (
        <div className="legend-item" key={k}>
          <span className="legend-line" style={{ background: EDGE_KINDS[k].color }}></span>
          {EDGE_KINDS[k].label}
        </div>
      ))}
      <div className="legend-sep">Badges</div>
      <div className="legend-item"><span className="legend-badge hub">★ hub</span> most connected</div>
      <div className="legend-item"><span className="legend-badge orphan">⚠ isolated</span> no links</div>
      <div className="legend-item"><span className="legend-badge sink">✖ sink</span> consumes, emits nothing</div>
      <div className="legend-hint">Click a service to explore it</div>
    </div>
  );
}

/* ---------------- Galaxy View ---------------- */
function GalaxyView({ graph, dims, onSelectRepo }) {
  const repos = graph.repos || [];
  const entryPoints = graph.entry_points || [];
  const links = graph.cross_repo_links || [];
  const pz = usePanZoom(".repo-wrap, .legend, .filter-chip, .stat-chip");

  // Hovered direction edge → bundled message details shown in a popup
  const [hoverEdge, setHoverEdge] = useState(null); // { items, from, to, mid:{x,y} }

  // ── Derived analytics (single source of truth: derived.js) ──
  const flows = useMemo(() => detectFlows(graph), [graph]);
  const stats = useMemo(() => buildServiceStats(graph), [graph]);
  const summary = useMemo(() => landscapeSummary(graph, stats, flows), [graph, stats, flows]);

  // Which entry point types does the whole project use (drives the legend)?
  const usedTypes = useMemo(() => {
    const s = new Set();
    entryPoints.forEach((ep) => s.add(ep.type));
    return Array.from(s);
  }, [graph]);

  // Which service roles appear (drives the legend)?
  const usedRoles = useMemo(() => {
    const s = new Set();
    repos.forEach((r) => { if (stats[r]) s.add(stats[r].role); });
    return Array.from(s);
  }, [repos, stats]);

  const W = dims.w;
  const H = dims.h;
  // Canvas is reduced by the headline row's height so orbs never hide under it.
  const Hc = Math.max(320, H - 64);
  const cx = W / 2, cy = Hc / 2;
  const radius = Math.max(120, Math.min(W, Hc) * 0.34);

  // Orb radius ∝ call-tree complexity (total nodes); count = entry points.
  const maxNodes = useMemo(
    () => Math.max(1, ...repos.map((r) => (stats[r] ? stats[r].totalNodes : 1))),
    [repos, stats]
  );

  const positions = useMemo(() => {
    const n = repos.length;
    return repos.map((name, i) => {
      const angle = (2 * Math.PI * i) / Math.max(n, 1) - Math.PI / 2;
      const s = stats[name] || { epCount: 0, totalNodes: 1 };
      const r = Math.max(42, Math.min(88, 40 + 24 * Math.sqrt(s.totalNodes / maxNodes)));
      return {
        name, count: s.epCount, totalNodes: s.totalNodes, r,
        x: cx + radius * Math.cos(angle),
        y: cy + radius * Math.sin(angle),
      };
    });
    // eslint-disable-next-line
  }, [graph, stats, maxNodes, W, H]);

  // Spinning type orbs around each orb — one colored dot per entry type.
  const typesOf = (name) => {
    const s = stats[name];
    if (!s) return [];
    return Object.entries(s.types).sort((a, b) => b[1] - a[1]).map(([t]) => t);
  };

  const posMap = useMemo(() => {
    const m = {};
    positions.forEach((p) => (m[p.name] = p));
    return m;
  }, [positions]);

  // Group links by direction (from-repo → to-repo): ONE line per direction, bundling
  // all of that direction's channels / HTTP calls (details shown in a hover popup).
  const edges = useMemo(() => {
    const map = {};
    links.forEach((link) => {
      const pRepos = Array.from(new Set((link.producers || []).map(repoFromId)));
      const cRepos = Array.from(new Set((link.consumers || []).map(repoFromId)));
      pRepos.forEach((pr) => cRepos.forEach((cr) => {
        if (pr === cr) return;
        const key = pr + ">>" + cr;
        if (!map[key]) map[key] = { from: pr, to: cr, items: [] };
        const items = map[key].items;
        if (!items.some((it) => it.channel === link.channel)) {
          items.push({
            channel: link.channel,
            kind: link.kind || "message",
            verb: link.verb || "",
          });
        }
      }));
    });
    return Object.values(map);
  }, [graph]);

  // One curved line per direction pair. Opposite directions bend to opposite sides
  // automatically (the control point offsets along the perpendicular, which flips
  // when a→b becomes b→a), so the two directions stay visually separate.
  const edgeGeom = (a, b) => {
    const GAP = 3; // uniform clearance at both orb edges
    const dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.hypot(dx, dy) || 1;
    const ux = dx / d, uy = dy / d;
    const start = { x: a.x + ux * (a.r + GAP), y: a.y + uy * (a.r + GAP) };
    const end = { x: b.x - ux * (b.r + GAP), y: b.y - uy * (b.r + GAP) };
    const bend = Math.min(130, d * 0.26);
    const c = { x: (start.x + end.x) / 2 - uy * bend, y: (start.y + end.y) / 2 + ux * bend };
    const mid = {
      x: 0.25 * start.x + 0.5 * c.x + 0.25 * end.x,
      y: 0.25 * start.y + 0.5 * c.y + 0.25 * end.y,
    };
    const path = "M " + start.x + " " + start.y + " Q " + c.x + " " + c.y + " " + end.x + " " + end.y;
    return { mid, path };
  };

  return (
    <div className="galaxy galaxy-system">
      <div className="view-top">
        <Breadcrumb items={[{ label: "Galaxy" }]} />
      </div>

      {/* Headline strip — the story at a glance */}
      <div className="galaxy-headline glass">
        <div className="headline-stats">
          <div className="stat-chip"><span className="stat-num">{summary.repoCount}</span><span className="stat-label">services</span></div>
          <div className="stat-chip"><span className="stat-num">{summary.flowCount}</span><span className="stat-label">flows</span></div>
          <div className="stat-chip"><span className="stat-num">{summary.channelCount}</span><span className="stat-label">channels</span></div>
          <div className="stat-chip"><span className="stat-num">{summary.entryPointCount}</span><span className="stat-label">entry points</span></div>
        </div>
        {summary.insight && <div className="headline-insight">{summary.insight}</div>}
      </div>
      <div
        className="canvas pan-canvas"
        ref={pz.containerRef}
        style={{ height: Hc }}
        {...pz.handlers}
      >
        <div
          className={"canvas-world" + (pz.animating ? " animating" : "")}
          style={{
            transform: `translate(${pz.viewport.x}px, ${pz.viewport.y}px) scale(${pz.viewport.zoom})`,
            transformOrigin: "0 0",
          }}
        >
        <svg className="edges" width={W} height={H}>
          <defs>
            <marker id="arrow-async" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="#00d4ff" opacity="0.9"></path>
            </marker>
            <marker id="arrow-sync" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="#00e0a8" opacity="0.95"></path>
            </marker>
            <marker id="arrow-both" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="#a78bfa" opacity="0.95"></path>
            </marker>
          </defs>
          {edges.map((e) => {
            const a = posMap[e.from], b = posMap[e.to];
            if (!a || !b) return null;
            const g = edgeGeom(a, b);
            const httpItems = e.items.filter((it) => it.kind === "http");
            const messages = e.items.filter((it) => it.kind !== "http");
            // Three line colors: sync (HTTP-only), async (message-only), both (mixed)
            const kind = (httpItems.length > 0 && messages.length > 0) ? "both"
              : (httpItems.length > 0 ? "sync" : "async");
            const km = EDGE_KINDS[kind];
            const prominent = kind !== "async"; // sync/both lines are bolder
            let label;
            if (e.items.length === 1) {
              const it = e.items[0];
              label = (it.kind === "http" && it.verb) ? (it.verb + " " + it.channel) : it.channel;
            } else {
              label = [
                messages.length ? messages.length + " msg" + (messages.length > 1 ? "s" : "") : "",
                httpItems.length ? httpItems.length + " HTTP" : "",
              ].filter(Boolean).join(" · ");
            }
            const pillW = label.length * 6.5 + 22;
            const pillH = 20;
            return (
              <g
                className={"edge edge-" + kind}
                key={e.from + ">>" + e.to}
                onMouseEnter={() => setHoverEdge({ items: e.items, from: e.from, to: e.to, mid: g.mid })}
                onMouseLeave={() => setHoverEdge(null)}
              >
                <path d={g.path} fill="none" stroke={km.color}
                      strokeWidth={prominent ? 2.2 : 1.6}
                      opacity={prominent ? 0.95 : 0.5} markerEnd={"url(#arrow-" + kind + ")"}></path>
                <g className="edge-label-pill" transform={"translate(" + g.mid.x + "," + g.mid.y + ")"}>
                  <rect className={"edge-label-glow " + kind} x={-pillW / 2 - 4} y={-pillH / 2 - 4} width={pillW + 8} height={pillH + 8} rx={(pillH + 8) / 2}></rect>
                  <rect className={"edge-label-bg " + kind} x={-pillW / 2} y={-pillH / 2} width={pillW} height={pillH} rx={pillH / 2}></rect>
                  <text className={"edge-label " + kind} x={0} y={0} dominantBaseline="central" textAnchor="middle">{label}</text>
                </g>
              </g>
            );
          })}
        </svg>
        {positions.map((p) => {
          const s = stats[p.name];
          const role = s ? ROLE_META[s.role] : ROLE_META.utility;
          // One priority badge per orb: hub > sink > isolated
          let badge = null;
          if (s && s.partnerCount >= summary.hubThreshold) badge = { cls: "hub", label: "★ hub" };
          else if (s && s.inbound.length > 0 && s.outbound.length === 0) badge = { cls: "sink", label: "✖ sink" };
          else if (s && s.partnerCount === 0) badge = { cls: "orphan", label: "⚠ isolated" };
          const types = typesOf(p.name);
          const orbitR = p.r + 18;
          return (
            <div className="repo-wrap" key={p.name} style={{ left: p.x, top: p.y }} onClick={(e) => onSelectRepo(p.name, e)}>
              <button className="repo-node" style={{ width: p.r * 2, height: p.r * 2 }}>
                <div className="repo-count-wrap">
                  <span className="repo-count">{p.count}</span>
                  <span className="repo-count-label">entry point{p.count === 1 ? "" : "s"}</span>
                </div>
              </button>
              {types.map((t, i) => {
                const meta = typeMeta(t);
                return (
                  <div
                    key={t}
                    className="type-orbit"
                    style={{
                      width: orbitR * 2,
                      height: orbitR * 2,
                      "--start-angle": (360 / types.length) * i + "deg",
                      animationDuration: (40 + i * 12) + "s",
                      animationDirection: i % 2 === 0 ? "normal" : "reverse",
                    }}
                  >
                    <span
                      className="type-orb"
                      style={{
                        marginLeft: orbitR - 6 + "px",
                        marginTop: -6 + "px",
                        "--c": meta.color,
                        "--glow": meta.glow,
                        animationDelay: (i * 0.3) + "s",
                      }}
                      title={meta.label}
                    />
                  </div>
                );
              })}
              {badge && <span className={"repo-badge " + badge.cls} style={{ top: -(p.r + 10) }}>{badge.label}</span>}
              <div className="repo-label" style={{ top: p.r + 26 }}>
                <span className="repo-label-name">{p.name}</span>
                {role && <span className="repo-role" style={{ "--c": role.color }}>{role.label}</span>}
              </div>
            </div>
          );
        })}
        </div>
        {hoverEdge && (() => {
          const px = pz.viewport.x + hoverEdge.mid.x * pz.viewport.zoom;
          const py = Math.max(150, pz.viewport.y + hoverEdge.mid.y * pz.viewport.zoom);
          return (
            <div className="edge-popup" style={{ left: px, top: py }}>
              <div className="edge-popup-title">
                <span className="mono">{hoverEdge.from}</span>
                <span className="edge-popup-arrow">→</span>
                <span className="mono">{hoverEdge.to}</span>
              </div>
              {hoverEdge.items.map((it, i) => (
                <div className="edge-popup-item" key={i}>
                  <span className={"edge-popup-dot " + (it.kind === "http" ? "http" : "msg")} />
                  <span className="edge-popup-channel mono">
                    {it.kind === "http" && it.verb ? it.verb + " " + it.channel : it.channel}
                  </span>
                  <span className={"edge-popup-kind " + (it.kind === "http" ? "http" : "msg")}>
                    {it.kind === "http" ? "HTTP" : "msg"}
                  </span>
                </div>
              ))}
            </div>
          );
        })()}
      </div>
      <Legend types={usedTypes} roles={usedRoles} />
      {pz.zoomControls}
    </div>
  );
}

/* ---------------- Service View ---------------- */
// Replaces the solar-system scatter + "Outbound" panel. One place to see
// a service: its entry-point star map, a sortable entry-point table, its
// inbound/outbound channels (with partners + methods), and the flows it
// participates in. Shared ChannelCard = the one canonical channel renderer.

function StatChip({ n, label }) {
  return (
    <div className="stat-chip">
      <span className="stat-num">{n}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

// Canonical channel presentation — used by the service view's channel
// panels AND the call-path view's "emits to" strip (no duplication).
// `ownRepo` hides the repo label on the near side (it's always the
// current service). Far-side methods/repos and the channel's flows
// are click-through: methods → call path, repos → service view,
// flows → flows mode.
function ChannelCard({ ch, wide, ownRepo, flowsFor, onOpenRepo, onOpenEntry, onOpenFlow }) {
  const mts = channelMessageTypes(ch);
  const sideList = (items, arrow) => {
    if (!items || items.length === 0) return null;
    const uniq = [];
    const seen = new Set();
    items.forEach((x) => {
      const k = x.repo + "::" + x.method;
      if (seen.has(k)) return;
      seen.add(k);
      uniq.push(x);
    });
    const shown = uniq.slice(0, 4);
    return (
      <div className="ch-side">
        <span className="ch-arrow">{arrow}</span>
        <div className="ch-side-body">
          {shown.map((x, i) => {
            const isOwn = !!(ownRepo && x.repo === ownRepo);
            const openMethod = (e) => {
              if (isOwn) return;
              if (x.id && onOpenEntry) onOpenEntry(x.id, e);
              else if (onOpenRepo) onOpenRepo(x.repo, e);
            };
            return (
              <div
                key={i}
                className={"ch-method-row" + (isOwn ? "" : " clickable")}
                onClick={isOwn ? undefined : openMethod}
                title={isOwn ? x.method : "Open call path: " + x.method}
              >
                <div className="ch-method-line">
                  <span className="ch-method mono">{x.method}</span>
                  {!isOwn && <span className="ch-open">→</span>}
                </div>
                {!isOwn && (
                  <button
                    className="ch-repo"
                    title={"Open service: " + x.repo}
                    onClick={(e) => { e.stopPropagation(); if (onOpenRepo) onOpenRepo(x.repo, e); }}
                  >
                    {x.repo}
                  </button>
                )}
              </div>
            );
          })}
          {uniq.length > 4 && <div className="ch-more muted small">+{uniq.length - 4} more</div>}
        </div>
      </div>
    );
  };
  const flows = flowsFor ? flowsFor(ch.channel) || [] : [];
  return (
    <div className={"channel-card glass" + (wide ? " wide" : "")}>
      <div className="ch-head">
        <span className="ch-name mono" title={ch.channel}>{ch.channel}</span>
        <span className={"ch-kind " + (ch.kind === "http" ? "http" : "msg")}>
          {ch.kind === "http" ? "HTTP" + (ch.verb ? " " + ch.verb : "") : "MSG"}
        </span>
      </div>
      {sideList(ch.producers, "→")}
      {sideList(ch.consumers, "←")}
      {mts.length > 0 && (
        <div className="ch-mts">
          {mts.map((t) => <span className="ch-mt-chip mono" key={t}>{t}</span>)}
        </div>
      )}
      {flows.length > 0 && onOpenFlow && (
        <div className="ch-flows">
          <span className="ch-flows-label">flows</span>
          {flows.slice(0, 3).map((f) => (
            <button
              className="ch-flow-chip"
              key={f.id}
              title={"Open flow: " + f.name}
              onClick={() => onOpenFlow(f.id)}
            >
              <span className={"ch-flow-origin " + (f.originClass || "external")}>{f.originTag || "EXT"}</span>
              <span className="ch-flow-name">{f.name}</span>
            </button>
          ))}
          {flows.length > 3 && <span className="muted small">+{flows.length - 3}</span>}
        </div>
      )}
    </div>
  );
}

function ServiceView({ graph, repo, flows, onHome, onSelectEntry, onSelectFlow, onOpenRepo }) {
  const stats = useMemo(() => buildServiceStats(graph), [graph]);
  const svc = stats[repo] || {
    name: repo, epCount: 0, prodCount: 0, totalNodes: 0, maxDepth: 0, filesCount: 0,
    types: {}, inbound: [], outbound: [], partnerCount: 0, channelCount: 0, role: "utility",
  };
  const eps = useMemo(() => (
    (graph.entry_points || [])
      .filter((e) => e.repo === repo)
      .sort((a, b) => ((b.metrics || {}).total_nodes || 0) - ((a.metrics || {}).total_nodes || 0))
  ), [graph, repo]);
  const flowsIndex = useMemo(() => flowsByChannel(flows || []), [flows]);
  const [hidden, setHidden] = useState({});
  const typesPresent = Array.from(new Set(eps.map((e) => e.type)));
  const role = ROLE_META[svc.role] || ROLE_META.utility;

  // ── Left canvas: classic infinite pan/zoom star map ──
  const pz = usePanZoom(".star, .star-label");
  const canvasRef = useRef(null);
  const [canvasSize, setCanvasSize] = useState({ w: 0, h: 0 });

  // Measure the flex-sized canvas so stars center in the real box.
  useLayoutEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const update = () => {
      const r = el.getBoundingClientRect();
      setCanvasSize((prev) => (prev.w === r.width && prev.h === r.height ? prev : { w: r.width, h: r.height }));
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const stars = useMemo(() => {
    const { w, h } = canvasSize;
    if (!w || !h) return [];
    const maxNodes = Math.max(1, ...eps.map((e) => (e.metrics && e.metrics.total_nodes) || 1));
    const cx = w / 2, cy = h / 2;
    return eps.map((ep, i) => {
      const angle = i * 2.39996323;
      const rr = Math.sqrt(i + 0.6) * Math.min(w, h) * 0.085;
      const meta = typeMeta(ep.type);
      const nodes = (ep.metrics && ep.metrics.total_nodes) || 1;
      const size = 14 + (nodes / maxNodes) * 34;
      return {
        ep, size,
        x: cx + rr * Math.cos(angle),
        y: cy + rr * Math.sin(angle),
        color: meta.color, glow: meta.glow,
      };
    });
  }, [eps, canvasSize]);

  const visible = stars.filter((s) => !hidden[s.ep.type]);
  const visibleEps = eps.filter((e) => !hidden[e.type]);

  const triggerOf = (ep) => {
    if (ep.type === "rest-endpoint") return (ep.method_type || "POST") + " " + (ep.channel || "");
    return ep.channel || "—";
  };

  return (
    <div className="service-view">
      <div className="view-top">
        <Breadcrumb items={[{ label: "Galaxy", onClick: onHome }, { label: repo }]} />
      </div>

      <div className="sv-split">
        {/* ── Left: star map canvas (pan/zoom, classic style) ── */}
        <div className={"sv-canvas-wrap" + (visible.length <= 8 ? " labels-static" : "")}>
          <div className="filters">
            {typesPresent.map((t) => {
              const m = typeMeta(t);
              return (
                <button
                  key={t}
                  className={"filter-chip" + (hidden[t] ? " off" : "")}
                  style={{ "--c": m.color }}
                  onClick={() => setHidden((h) => ({ ...h, [t]: !h[t] }))}
                >
                  <span className="chip-dot" style={{ background: m.color, color: m.color }}></span>
                  {m.label}
                </button>
              );
            })}
          </div>
          <div
            className="canvas solar-canvas pan-canvas"
            ref={canvasRef}
            {...pz.handlers}
          >
            <div
              className={"canvas-world" + (pz.animating ? " animating" : "")}
              style={{
                transform: `translate(${pz.viewport.x}px, ${pz.viewport.y}px) scale(${pz.viewport.zoom})`,
                transformOrigin: "0 0",
              }}
            >
              {visible.map((s) => (
                <div key={s.ep.id} className="sv-star-wrap" style={{ left: s.x, top: s.y }}>
                  <button
                    className="star"
                    style={{ width: s.size, height: s.size, "--c": s.color, "--glow": s.glow }}
                    title={s.ep.id}
                    onClick={(e) => onSelectEntry(s.ep.id, e)}
                  >
                    <span className="star-core" style={{ width: s.size, height: s.size }}></span>
                  </button>
                  <div className="star-label" style={{ top: s.size / 2 + 8 }}>
                    <span className="star-label-name">{s.ep.method || s.ep.id.split(":").pop()}</span>
                  </div>
                </div>
              ))}
              {visible.length === 0 && <div className="sv-empty muted small">No entry points in view.</div>}
            </div>
          </div>
          {pz.zoomControls}
        </div>

        {/* ── Right: data panels ── */}
        <aside className="sv-panel glass">
          <div className="sv-panel-head">
            <span className="sv-title">{repo}</span>
            <span className="sv-role-tag" style={{ "--c": role.color }}>{role.label}</span>
          </div>
          <div className="sv-panel-stats">
            <StatChip n={svc.prodCount} label="producers" />
            <StatChip n={svc.partnerCount} label="partners" />
            <StatChip n={svc.totalNodes} label="call nodes" />
          </div>

          <section className="sv-section">
            <div className="sv-section-head">
              <h3>Entry points <span className="muted">({visibleEps.length})</span></h3>
              <span className="sv-hint muted small">by complexity</span>
            </div>
            <div className="entry-table">
              {visibleEps.length === 0 && <div className="et-empty muted">No entry points.</div>}
              {visibleEps.map((ep) => {
                const m = typeMeta(ep.type);
                const cx = ep.metrics || {};
                return (
                  <div className="et-row" key={ep.id} onClick={() => onSelectEntry(ep.id)}>
                    <div className="et-line1">
                      <span className="et-type" style={{ "--c": m.color }}>{m.label}</span>
                      <span className="et-method" title={ep.method}>{ep.method}</span>
                      <span className="et-cx mono" title="call nodes · depth">{cx.total_nodes || 1} · d{cx.depth || 0}</span>
                    </div>
                    <div className="et-line2">
                      <span className="et-trigger mono" title={triggerOf(ep)}>{triggerOf(ep)}</span>
                      <span className="et-mt mono">{ep.message_type || "—"}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="sv-section">
            <div className="sv-section-head">
              <h3>Channels</h3>
            </div>
            <div className="channels-grid">
              <div className="channels-col">
                <h4 className="ch-col-title">Inbound <span className="muted">({svc.inbound.length})</span></h4>
                {svc.inbound.length === 0 && <p className="muted small">No inbound channels.</p>}
                {svc.inbound.map((ch) => (
                  <ChannelCard
                    key={ch.channel}
                    ch={ch}
                    ownRepo={repo}
                    flowsFor={(c) => flowsIndex[c] || []}
                    onOpenRepo={onOpenRepo}
                    onOpenEntry={onSelectEntry}
                    onOpenFlow={onSelectFlow}
                  />
                ))}
              </div>
              <div className="channels-col">
                <h4 className="ch-col-title">Outbound <span className="muted">({svc.outbound.length})</span></h4>
                {svc.outbound.length === 0 && <p className="muted small">No outbound channels.</p>}
                {svc.outbound.map((ch) => (
                  <ChannelCard
                    key={ch.channel}
                    ch={ch}
                    ownRepo={repo}
                    flowsFor={(c) => flowsIndex[c] || []}
                    onOpenRepo={onOpenRepo}
                    onOpenEntry={onSelectEntry}
                    onOpenFlow={onSelectFlow}
                  />
                ))}
              </div>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

/* ---------------- Path View ---------------- */

const PV_NODE_W = 240;
const PV_TOGGLE_W = 32;  // toggle bar on the right of nodes with children
const PV_NODE_H = 110;  // estimated height including toggle footer
const PV_HSPACE = 340;  // horizontal distance between depth levels
const PV_VGAP = 50;     // vertical gap between sibling nodes

function PathView({ entryPoint, graph, onHome, onBack, selectedNode, onSelectNode, chatOpen, flows, onOpenRepo, onOpenEntry, onOpenFlow }) {
  const tree = entryPoint.call_tree;

  // Outbound channels for this entry point (enriched via derived.js —
  // kind/verb/partners/methods, same facts as the service view).
  const outboundChannels = useMemo(() => entryEmits(graph, entryPoint), [graph, entryPoint]);
  const flowsIndex = useMemo(() => flowsByChannel(flows || []), [flows]);

  // ── Collapse state ──────────────────────────────────────────
  // Default: root + depth-1 children expanded
  const [expanded, setExpanded] = useState(() => {
    const set = new Set(["0"]);
    if (tree && tree.children) {
      tree.children.forEach((_, i) => set.add("0/" + i));
    }
    return set;
  });

  // Reset expand state + center when switching entry points
  useEffect(() => {
    const set = new Set(["0"]);
    if (tree && tree.children) {
      tree.children.forEach((_, i) => set.add("0/" + i));
    }
    setExpanded(set);
    // Center after a tick so the container ref + layout are ready
    requestAnimationFrame(() => centerView());
  }, [entryPoint.id]); // eslint-disable-line

  // When a node is selected, pan so it stays visible left of the detail panel / chat
  useEffect(() => {
    if (!selectedNode) return;
    const nv = computeRevealOffset(containerRef, layout, selectedNode, viewport);
    if (nv) {
      setAnimating(true);
      setViewport(nv);
      setTimeout(() => setAnimating(false), 400);
    }
  }, [selectedNode, chatOpen]); // eslint-disable-line

  const toggleExpand = (path) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  // ── Layout: horizontal tree, only visible (expanded) nodes ──
  // Root on the left. Depth goes rightward (X). Siblings stacked vertically (Y).
  const layout = useMemo(() => {
    const placed = [];
    let leafIdx = 0;

    function walk(node, depth, path) {
      const isExpanded = expanded.has(path);
      const kids = (node.children || []);
      const hasKids = kids.length > 0;

      if (isExpanded && hasKids) {
        const childIndices = [];
        kids.forEach((k, i) => {
          const childPath = path + "/" + i;
          walk(k, depth + 1, childPath);
          childIndices.push(placed.length - 1);
        });
        const firstChild = placed[childIndices[0]];
        const lastChild = placed[childIndices[childIndices.length - 1]];
        const y = (firstChild.y + lastChild.y) / 2;
        const x = depth * PV_HSPACE;
        const entry = { data: node, path, depth, x, y, hasKids, isExpanded, children: childIndices };
        placed.push(entry);
      } else {
        const y = leafIdx * (PV_NODE_H + PV_VGAP);
        const x = depth * PV_HSPACE;
        const entry = { data: node, path, depth, x, y, hasKids, isExpanded: false, children: [] };
        placed.push(entry);
        leafIdx++;
      }
    }

    walk(tree || { method: "?", children: [] }, 0, "0");

    const maxX = Math.max(...placed.map((p) => p.x)) + PV_NODE_W;
    const maxY = Math.max(...placed.map((p) => p.y)) + PV_NODE_H;

    return { nodes: placed, maxX, maxY };
  }, [tree, expanded]);

  // ── Edges — parent right-center → child left-center ─────────
  // The expand toggle sits INSIDE the node's PV_NODE_W width (body flexes, toggle is a
  // fixed 32px right column), so the node's right edge is simply node.x + PV_NODE_W.
  // Vertical anchors use each button's MEASURED center — node bodies grow with content
  // (method name, loc, confidence badge), so the fixed PV_NODE_H estimate can't give a
  // true center and the line would miss short nodes.
  const [nodeHeights, setNodeHeights] = useState([]);
  const edges = useMemo(() => {
    const result = [];
    layout.nodes.forEach((node, idx) => {
      if (node.isExpanded && node.children.length > 0) {
        node.children.forEach((ci) => {
          const child = layout.nodes[ci];
          const parentH = nodeHeights[idx] || PV_NODE_H;
          const childH = nodeHeights[ci] || PV_NODE_H;
          result.push({
            x1: node.x + PV_NODE_W,
            y1: node.y + parentH / 2,
            x2: child.x,
            y2: child.y + childH / 2,
          });
        });
      }
    });
    return result;
  }, [layout.nodes, nodeHeights]);

  // ── Infinite canvas viewport ────────────────────────────────
  const containerRef = useRef(null);

  // Measure real node heights after each layout so edge anchors hit true centers.
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const nodes = Array.from(el.querySelectorAll(".pv-node"));
    if (nodes.length === layout.nodes.length) {
      setNodeHeights(nodes.map((n) => n.offsetHeight));
    }
  }, [layout.nodes]); // eslint-disable-line

  const [viewport, setViewport] = useState({ x: 100, y: 50, zoom: 1 });
  const [animating, setAnimating] = useState(false);
  const dragRef = useRef({ active: false, startX: 0, startY: 0, vpX: 0, vpY: 0, moved: false });

  // Center the content in the canvas (with smooth animation)
  const centerView = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const cw = el.clientWidth;
    const ch = el.clientHeight;
    const contentW = layout.maxX;
    const contentH = layout.maxY;
    setAnimating(true);
    setViewport({
      x: (cw - contentW) / 2,
      y: (ch - contentH) / 2,
      zoom: 1,
    });
    // Remove transition class after animation completes
    setTimeout(() => setAnimating(false), 400);
  }, [layout.maxX, layout.maxY]);

  // Pan + zoom handlers on the container
  const onMouseDown = (e) => {
    // Don't pan if clicking on a button or expand toggle
    if (e.target.closest(".tree-node") || e.target.closest(".exit-point")) return;
    dragRef.current = {
      active: true,
      startX: e.clientX,
      startY: e.clientY,
      vpX: viewport.x,
      vpY: viewport.y,
      moved: false,
    };
  };

  const onMouseMove = (e) => {
    if (!dragRef.current.active) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) dragRef.current.moved = true;
    setViewport((vp) => ({
      ...vp,
      x: dragRef.current.vpX + dx,
      y: dragRef.current.vpY + dy,
    }));
  };

  const onMouseUp = () => {
    dragRef.current.active = false;
  };

  const onWheel = (e) => {
    e.preventDefault();
    const rect = containerRef.current.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    setViewport((vp) => {
      const delta = -e.deltaY * 0.0015;
      const newZoom = Math.max(0.2, Math.min(3, vp.zoom * (1 + delta)));
      const zoomRatio = newZoom / vp.zoom;

      // Keep point under cursor stable:
      // worldPoint = (screenPoint - pan) / oldZoom
      // screenPoint = worldPoint * newZoom + newPan
      // newPan = screenPoint - worldPoint * newZoom
      //        = screenPoint - (screenPoint - pan) / oldZoom * newZoom
      //        = screenPoint - (screenPoint - pan) * zoomRatio
      const newX = mouseX - (mouseX - vp.x) * zoomRatio;
      const newY = mouseY - (mouseY - vp.y) * zoomRatio;

      return { x: newX, y: newY, zoom: newZoom };
    });
  };

  // Wheel listener (needs passive: false to preventDefault)
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handler = (e) => onWheel(e);
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, [viewport]); // eslint-disable-line

  const resetView = () => centerView();

  const m = typeMeta(entryPoint.type);

  // ── Render ──────────────────────────────────────────────────
  return (
    <div className="path-view">
      <div className="view-top">
        <Breadcrumb items={[
          { label: "Galaxy", onClick: onHome },
          { label: entryPoint.repo, onClick: onBack },
          { label: entryPoint.method || entryPoint.id.split(":").pop() },
        ]} />
        <div className="view-hint">
          <span className="ep-tag" style={{ "--c": m.color }}>{m.label}</span>
          <span className="mono">{entryPoint.channel}</span>
          {entryPoint.metrics && (
            <>· depth {entryPoint.metrics.depth} · {entryPoint.metrics.total_nodes} nodes</>
          )}
        </div>
      </div>

      <div
        className="pv-canvas"
        ref={containerRef}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        <div
          className={"pv-world" + (animating ? " animating" : "")}
          style={{
            transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.zoom})`,
            transformOrigin: "0 0",
          }}
        >
          {/* Edges */}
          <svg
            className="pv-edges"
            width={layout.maxX + 100}
            height={layout.maxY + 200}
            style={{ position: "absolute", top: 0, left: 0, pointerEvents: "none", overflow: "visible" }}
          >
            {edges.map((edge, i) => {
              const midX = (edge.x1 + edge.x2) / 2;
              return (
                <path
                  key={i}
                  d={`M ${edge.x1} ${edge.y1} C ${midX} ${edge.y1} ${midX} ${edge.y2} ${edge.x2} ${edge.y2}`}
                  fill="none"
                  stroke="#00d4ff"
                  strokeWidth="1.5"
                  opacity="0.35"
                />
              );
            })}
          </svg>

          {/* Nodes */}
          {layout.nodes.map((node) => {
            const d = node.data;
            const conf = confMeta(d.confidence);
            const isSel = selectedNode && sameNode(d, selectedNode);
            const isRoot = node.depth === 0;
            return (
              <div
                key={node.path}
                className={
                  "pv-node" +
                  (isSel ? " sel" : "") +
                  (isRoot ? " root" : "")
                }
                style={{ left: node.x, top: node.y, width: PV_NODE_W }}
              >
                <button
                  className="pv-node-body"
                  onClick={(e) => {
                    if (!dragRef.current.moved) onSelectNode(d);
                  }}
                >
                  <div className="pv-method">{nodeDisplayName(d)}</div>
                  <div className="pv-loc mono">{fmtFile(d.file)}{d.line ? ":" + d.line : ""}</div>
                  {d.confidence && d.confidence !== "EXTRACTED" && (
                    <span className="conf-badge" style={{ "--c": conf.color }}>{d.confidence}</span>
                  )}
                </button>
                {node.hasKids && (
                  <button
                    className={"pv-toggle" + (node.isExpanded ? " expanded" : "")}
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleExpand(node.path);
                    }}
                    title={node.isExpanded ? "Collapse" : "Expand"}
                  >
                    {node.isExpanded ? "▼" : "▶"}
                    {!node.isExpanded && (
                      <span className="pv-toggle-count">
                        {d.children ? d.children.length : 0}
                      </span>
                    )}
                  </button>
                )}
              </div>
            );
          })}

          {/* Exit strip — channels this entry point emits to (shared ChannelCard) */}
          {outboundChannels.length > 0 && (
            <div className="exit-strip" style={{ top: layout.maxY + 30, left: 0 }}>
              <div className="exit-point-label">EMITS TO</div>
              <div className="exit-strip-cards">
                {outboundChannels.map((oc) => (
                  <ChannelCard
                    key={oc.channel}
                    ch={oc}
                    wide
                    ownRepo={entryPoint.repo}
                    flowsFor={(c) => flowsIndex[c] || []}
                    onOpenRepo={onOpenRepo}
                    onOpenEntry={onOpenEntry}
                    onOpenFlow={onOpenFlow}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Zoom controls */}
      <div className="zoom-controls">
        <button
          onClick={() => { setAnimating(true); setViewport(v => ({ ...v, zoom: Math.min(3, v.zoom + 0.15) })); setTimeout(() => setAnimating(false), 400); }}
          title="Zoom in"
        >+</button>
        <span className="zoom-level">{Math.round(viewport.zoom * 100)}%</span>
        <button
          onClick={() => { setAnimating(true); setViewport(v => ({ ...v, zoom: Math.max(0.2, v.zoom - 0.15) })); setTimeout(() => setAnimating(false), 400); }}
          title="Zoom out"
        >−</button>
        <button onClick={resetView} title="Reset view">⤢</button>
      </div>
    </div>
  );
}

/* ---------------- Detail Panel ---------------- */
function DetailPanel({ node, entryPoint, onClose, pid }) {
  const [source, setSource] = useState(null);
  const [srcStatus, setSrcStatus] = useState("loading");
  const [srcError, setSrcError] = useState("");
  const hiRef = useRef(null);

  useEffect(() => {
    let alive = true;
    setSource(null); setSrcStatus("loading");
    if (!node.file) {
      if (alive) setSrcStatus("none");
      return () => { alive = false; };
    }
    fetchJSON(projPath(pid, "/source?file_path=" + encodeURIComponent(node.file)))
      .then((s) => { if (alive) { setSource(s); setSrcStatus("ready"); } })
      .catch((e) => { if (alive) { setSrcError(e.message); setSrcStatus("error"); } });
    return () => { alive = false; };
  }, [node.file]);

  useEffect(() => {
    if (srcStatus === "ready" && hiRef.current) {
      hiRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [srcStatus]);

  const lines = source && source.content ? source.content.split("\n") : [];
  const target = node.line || 0;
  const WINDOW = 80;
  let showLines = lines;
  let offsetL = 1;
  let truncated = false;
  if (lines.length > 240) {
    truncated = true;
    const start = Math.max(0, target - 1 - Math.floor(WINDOW / 2));
    showLines = lines.slice(start, start + WINDOW);
    offsetL = start + 1;
  }

  const rels = useMemo(
    () => findRelations(entryPoint.call_tree, node),
    [entryPoint, node]
  );

  const conf = confMeta(node.confidence);

  return (
    <aside className="detail-panel glass">
      <header className="dp-head">
        <div>
          <div className="dp-title">{node.method || node.class_name || "function"}</div>
          <div className="dp-loc mono">{node.file}{node.line ? ":" + node.line : ""}</div>
        </div>
        <button className="dp-close" onClick={onClose}>✕</button>
      </header>

      <div className="dp-body">
        {node.confidence && node.confidence !== "EXTRACTED" && (
          <span className="conf-badge" style={{ "--c": conf.color }}>{node.confidence}</span>
        )}

        {rels.parent && (
          <div className="rel">
            <span className="rel-label">Called by</span>
            <span className="rel-val">{rels.parent.method || rels.parent.class_name}</span>
          </div>
        )}
        {rels.calls && rels.calls.length > 0 && (
          <div className="rel">
            <span className="rel-label">Calls</span>
            <span className="rel-val">{rels.calls.length} method{rels.calls.length === 1 ? "" : "s"}</span>
          </div>
        )}

        <div className="src-wrap">
          <div className="src-head mono">
            {fmtFile(node.file)}{source && source.line_count ? " · " + source.line_count + " lines" : ""}
            {truncated && " · showing context window"}
          </div>
          {srcStatus === "loading" && <div className="src-loading">Loading source…</div>}
          {srcStatus === "error" && <div className="src-error">Could not load source: {srcError}</div>}
          {srcStatus === "none" && <div className="src-error">No source file — inferred framework/library call.</div>}
          {srcStatus === "ready" && (
            <pre className="code mono">
              {showLines.map((ln, i) => {
                const n = offsetL + i;
                const hi = n === target;
                return (
                  <div key={n} className={"code-line" + (hi ? " hl" : "")}>
                    <span className="ln">{n}</span>
                    <span className="lc" ref={hi ? hiRef : undefined}>{ln}</span>
                  </div>
                );
              })}
            </pre>
          )}
        </div>
      </div>
    </aside>
  );
}


/* ---------------- Flows Mode: Flow Index View ---------------- */
// Galaxy equivalent — shows all detected flows as cards in the starfield
function FlowIndexView({ graph, dims, onSelectFlow, flows: flowsProp }) {
  const flows = flowsProp || useMemo(() => detectFlows(graph), [graph]);
  const W = dims.w, H = dims.h;
  const TOPBAR_H = 72; // matches --topbar-h in styles.css
  // Center within the VISIBLE stage area (below the fixed topbar), not the full window
  const cx = W / 2, cy = (H - TOPBAR_H) / 2;
  const pz = usePanZoom(".flow-card");

  // Uniform card height: all cards share the tallest card's height
  const [uniformH, setUniformH] = useState(null);
  const cardRefs = useRef([]);
  useLayoutEffect(() => {
    if (flows.length === 0) return;
    // Measure natural heights of all cards (no explicit height set yet)
    const hs = cardRefs.current.map((el) => (el ? el.offsetHeight : 0));
    if (hs.length === 0) return;
    const maxH = Math.max(...hs);
    setUniformH((prev) => (prev === maxH ? prev : maxH));
  }, [flows]);

  // Position flow cards in a grid that FITS the visible stage. The whole grid is
  // scaled down (transform: scale) when it would overflow, so cards never clip.
  const layout = useMemo(() => {
    const n = flows.length;
    const cols = Math.min(n, n <= 4 ? 2 : 3);
    const cardW = 240;
    const gapX = 50, gapY = 36;
    const rows = Math.ceil(n / cols);
    // Uniform height across the whole grid (fall back to a reasonable estimate
    // before the first measurement lands)
    const cardH = uniformH || 196;
    const totalW = cols * cardW + (cols - 1) * gapX;
    const totalH = rows * cardH + (rows - 1) * gapY;
    // Shrink-to-fit with a small margin around the visible stage
    const margin = 24;
    const fit = Math.min(1, (W - margin * 2) / totalW, (cy * 2 - margin * 2) / totalH);
    const scaledW = totalW * fit;
    const scaledH = totalH * fit;

    const positions = [];
    let y = 0;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const idx = r * cols + c;
        if (idx >= n) break;
        positions.push({
          x: c * (cardW + gapX),
          y: y,
          w: cardW,
          h: uniformH || null, // null = keep natural (auto) height until measured
        });
      }
      y += cardH + gapY;
    }
    return {
      positions,
      // Wrapper placed so the scaled grid is centered in the visible area
      left: cx - scaledW / 2,
      top: cy - scaledH / 2,
      width: totalW,
      height: totalH,
      fit,
    };
  }, [flows, cx, cy, uniformH]);

  return (
    <div className="galaxy flow-index">
      <div className="view-top">
        <Breadcrumb items={[{ label: "Flows" }]} />
        <div className="view-hint">
          {flows.length} flows detected · {flows.filter(f => f.hasCrossRepo).length} cross-repo
        </div>
      </div>
      <div
        className="canvas pan-canvas"
        ref={pz.containerRef}
        style={{ height: H }}
        {...pz.handlers}
      >
        <div
          className={"canvas-world" + (pz.animating ? " animating" : "")}
          style={{
            transform: `translate(${pz.viewport.x}px, ${pz.viewport.y}px) scale(${pz.viewport.zoom})`,
            transformOrigin: "0 0",
          }}
        >
        <div
          className="flow-grid"
          style={{
            position: "absolute",
            left: layout.left,
            top: layout.top,
            width: layout.width,
            height: layout.height,
            transform: "scale(" + layout.fit + ")",
            transformOrigin: "top left",
          }}
        >
        {flows.map((f, i) => {
          const pos = layout.positions[i];
          return (
            <div
              key={f.id}
              ref={(el) => { cardRefs.current[i] = el; }}
              className={"flow-card" + (f.hasCrossRepo ? " cross-repo" : "")}
              style={{ left: pos.x, top: pos.y, width: pos.w, height: pos.h || undefined, animationDelay: (i * 45) + "ms" }}
              onClick={(e) => onSelectFlow(f, e)}
            >
              <div className="flow-card-glow" />
              <div className="flow-card-origin">
                <span className={"flow-origin-tag " + (f.originClass || "external")}>
                  {f.originTag || "EXTERNAL"}
                </span>
                <span className="flow-origin-label mono">{f.originLabel}</span>
              </div>
              <div className="flow-card-name">{f.name}</div>
              <div className="flow-card-stats">
                <span className="flow-stat">
                  <span className="flow-stat-num">{f.repoCount}</span>
                  <span className="flow-stat-label">repo{f.repoCount === 1 ? "" : "s"}</span>
                </span>
                <span className="flow-stat-sep">·</span>
                <span className="flow-stat">
                  <span className="flow-stat-num">{f.hopCount}</span>
                  <span className="flow-stat-label">hop{f.hopCount === 1 ? "" : "s"}</span>
                </span>
                {f.hasCrossRepo && (
                  <>
                    <span className="flow-stat-sep">·</span>
                    <span className="flow-stat cross-repo-badge">cross-repo</span>
                  </>
                )}
              </div>
              <div className="flow-card-repos">
                {f.repos.map((r, ri) => (
                  <span key={r} className="flow-repo-chip">
                    {r}{ri < f.repos.length - 1 ? " →" : ""}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
        </div>
        </div>
      </div>
      {pz.zoomControls}
    </div>
  );
}

/* ---------------- Flows Mode: Flow View ---------------- */
// Solar equivalent — shows repos in a single flow as a DAG with channel edges
function FlowView({ flow, graph, dims, onHome, onBack, onSelectRepoInFlow }) {
  const W = dims.w, H = dims.h;
  const pz = usePanZoom(".flow-repo-node, .flow-external-node");

  // Build a DAG of repo-level nodes + edges from the flow step tree
  const { repoNodes, flowEdges, externalInputs } = useMemo(() => {
    const edges = [];
    const externals = [];
    const seenRepos = {};
    const seenEdges = new Set();

    function walk(step, depth) {
      if (!seenRepos[step.repo]) {
        seenRepos[step.repo] = { depth, repos: [step] };
      } else {
        seenRepos[step.repo].repos.push(step);
      }
      step.children.forEach((child) => {
        const ekey = step.repo + ">>" + child.step.repo + "|" + child.channel;
        if (!seenEdges.has(ekey)) {
          seenEdges.add(ekey);
          edges.push({ from: step.repo, to: child.step.repo, channel: child.channel });
        }
        walk(child.step, depth + 1);
      });
    }

    if (flow.originType === "external") {
      externals.push({ channel: flow.originChannel, targetRepo: flow.step.repo, kind: "external", tag: flow.originTag || "EXTERNAL", cls: flow.originClass || "external" });
    }
    if (flow.originType === "rest") {
      externals.push({ channel: flow.originChannel, targetRepo: flow.step.repo, kind: "rest", verb: flow.originMethodType || "POST", tag: "REST", cls: "rest" });
    }

    walk(flow.step, 0);

    const repoOrder = Object.keys(seenRepos);
    const repoNodes = repoOrder.map((repo) => ({
      repo,
      depth: seenRepos[repo].depth,
      entryIds: seenRepos[repo].repos.map((s) => s.entryId),
      methods: seenRepos[repo].repos.map((s) => s.method),
    }));

    return { repoNodes, flowEdges: edges, externalInputs: externals };
  }, [flow]);

  // Layout: assign (x, y) positions using depth (x) + sibling offset (y)
  const layout = useMemo(() => {
    const hasExternal = externalInputs.length > 0;
    const maxDepth = Math.max(...repoNodes.map((r) => r.depth), 0);
    const numCols = maxDepth + 1 + (hasExternal ? 1 : 0);

    const paddingX = 120;
    const usableW = W - paddingX * 2;
    const colStep = numCols > 1 ? usableW / (numCols - 1) : 0;
    const cy = H / 2 - 30;

    // Group repos by depth to vertically offset siblings at the same depth
    const byDepth = {};
    repoNodes.forEach((rn) => {
      const d = rn.depth + (hasExternal ? 1 : 0); // shift for external col
      if (!byDepth[d]) byDepth[d] = [];
      byDepth[d].push(rn);
    });

    // Assign y positions: if multiple repos at same depth, stack them vertically
    const NODE_GAP_Y = 160; // vertical gap between stacked repos
    const positions = repoNodes.map((rn) => {
      const d = rn.depth + (hasExternal ? 1 : 0);
      const col = d;
      const x = paddingX + col * colStep;
      // If multiple repos at this depth, distribute around center
      const siblings = byDepth[d] || [rn];
      const idx = siblings.indexOf(rn);
      const total = siblings.length;
      const yOffset = (idx - (total - 1) / 2) * NODE_GAP_Y;
      return { ...rn, x, y: cy + yOffset, w: 170, h: 110 };
    });

    const externalPos = externalInputs.map((ei, i) => ({
      ...ei,
      x: paddingX,
      y: cy,
      w: 170,
      h: 110,
    }));

    return { positions, externalPos };
  }, [repoNodes, externalInputs, W, H]);

  const posMap = useMemo(() => {
    const m = {};
    layout.positions.forEach((p) => { m[p.repo] = p; });
    return m;
  }, [layout]);

  // Edge geometry: curved bezier from right edge of source to left edge of target
  // Skip edges (spanning >1 depth) get a strong vertical arc to avoid intermediate nodes
  const edgeGeom = (a, b, edgeIndex, totalEdges, isSkip) => {
    const NODE_HALF_W = 85;
    const start = { x: a.x + NODE_HALF_W, y: a.y };
    const end = { x: b.x - NODE_HALF_W, y: b.y };
    const dx = end.x - start.x;

    if (isSkip) {
      // Arc upward to clear intermediate repos
      const arcHeight = Math.min(160, Math.max(90, Math.abs(dx) * 0.28));
      const arcY = start.y - arcHeight; // negative = upward
      const cp1 = { x: start.x + dx * 0.25, y: arcY };
      const cp2 = { x: end.x - dx * 0.25, y: arcY };
      const path = `M ${start.x} ${start.y} C ${cp1.x} ${cp1.y} ${cp2.x} ${cp2.y} ${end.x} ${end.y}`;
      const mid = { x: start.x + dx / 2, y: arcY };
      return { mid, path };
    }

    // Normal adjacent edge: gentle S-curve
    const cp1 = { x: start.x + dx * 0.4, y: start.y };
    const cp2 = { x: end.x - dx * 0.4, y: end.y };
    const path = `M ${start.x} ${start.y} C ${cp1.x} ${cp1.y} ${cp2.x} ${cp2.y} ${end.x} ${end.y}`;
    const mid = { x: start.x + dx / 2, y: (start.y + end.y) / 2 };
    return { mid, path };
  };

  // Group edges by from→to pair so we can offset multiples
  const edgePairCount = useMemo(() => {
    const m = {};
    flowEdges.forEach((e) => {
      const key = e.from + ">>" + e.to;
      m[key] = (m[key] || 0) + 1;
    });
    return m;
  }, [flowEdges]);

  const edgePairIndex = useMemo(() => {
    const m = {};
    return (key) => {
      m[key] = (m[key] || 0);
      return m[key]++;
    };
  }, [flowEdges]);

  return (
    <div className="galaxy flow-view">
      <div className="view-top">
        <Breadcrumb items={[
          { label: "Flows", onClick: onHome },
          { label: flow.name },
        ]} />
        <div className="view-hint">
          origin: {flow.originNoun || (flow.originType === "rest" ? "REST endpoint" : "external event")}
        </div>
      </div>
      <div
        className="canvas pan-canvas"
        ref={pz.containerRef}
        style={{ height: H }}
        {...pz.handlers}
      >
        <div
          className={"canvas-world" + (pz.animating ? " animating" : "")}
          style={{
            transform: `translate(${pz.viewport.x}px, ${pz.viewport.y}px) scale(${pz.viewport.zoom})`,
            transformOrigin: "0 0",
          }}
        >
        <svg className="edges" width={W} height={H}>
          <defs>
            <marker id="flow-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="#00d4ff" opacity="0.9" />
            </marker>
          </defs>
          {/* External input dashed edges */}
          {externalInputs.map((ei, i) => {
            const target = posMap[ei.targetRepo];
            if (!target) return null;
            const start = { x: layout.externalPos[i].x, y: layout.externalPos[i].y };
            const end = { x: target.x, y: target.y };
            const mid = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
            return (
              <g key={"ext-" + i}>
                <path
                  d={`M ${start.x + 85} ${start.y} L ${end.x - 85} ${end.y}`}
                  fill="none"
                  stroke="#94a3b8"
                  strokeWidth="1.5"
                  strokeDasharray="5 5"
                  opacity="0.5"
                  markerEnd="url(#flow-arrow)"
                />
                <g className="edge-label-pill" transform={`translate(${mid.x}, ${mid.y})`}>
                  <rect className="edge-label-bg" x={-(ei.channel.length * 6.5 + 22) / 2} y={-10} width={ei.channel.length * 6.5 + 22} height={20} rx={10} />
                  <text className="edge-label" x={0} y={0} dominantBaseline="central" textAnchor="middle">{ei.channel}</text>
                </g>
              </g>
            );
          })}
          {/* Internal flow edges */}
          {flowEdges.map((e, i) => {
            const a = posMap[e.from], b = posMap[e.to];
            if (!a || !b) return null;
            // Skip edge = spans more than 1 depth level (jumps over a repo)
            const isSkip = Math.abs(a.depth - b.depth) > 1;
            const pairKey = e.from + ">>" + e.to;
            const total = edgePairCount[pairKey] || 1;
            const idx = edgePairIndex(pairKey);
            const g = edgeGeom(a, b, idx, total, isSkip);
            const pillW = e.channel.length * 6.5 + 22;
            return (
              <g key={"fe-" + i}>
                <path d={g.path} fill="none" stroke="#00d4ff" strokeWidth="2" opacity={isSkip ? "0.4" : "0.55"} markerEnd="url(#flow-arrow)" />
                <g className="edge-label-pill" transform={`translate(${g.mid.x}, ${g.mid.y})`}>
                  <rect className="edge-label-glow" x={-pillW / 2 - 4} y={-12} width={pillW + 8} height={24} rx={12} />
                  <rect className="edge-label-bg" x={-pillW / 2} y={-10} width={pillW} height={20} rx={10} />
                  <text className="edge-label" x={0} y={0} dominantBaseline="central" textAnchor="middle">{e.channel}</text>
                </g>
              </g>
            );
          })}
        </svg>

        {/* External / REST input nodes */}
        {externalInputs.map((ei, i) => (
          <div
            key={"ext-node-" + i}
            className={"flow-external-node " + (ei.cls ? ei.cls + "-origin" : (ei.kind === "rest" ? "rest-origin" : ""))}
            style={{ left: layout.externalPos[i].x - 80, top: layout.externalPos[i].y - 50 }}
          >
            <div className="flow-external-icon">{ei.kind === "rest" ? "⟶" : (ei.cls === "scheduled" ? "⏰" : "⌁")}</div>
            <div className="flow-external-label">{ei.kind === "rest" ? (ei.verb + " " + ei.channel) : ei.channel}</div>
            <div className="flow-external-sub">{ei.tag || (ei.kind === "rest" ? "REST" : "external")}</div>
          </div>
        ))}

        {/* Repo nodes */}
        {layout.positions.map((p) => (
          <div
            key={p.repo}
            className="flow-repo-node"
            style={{ left: p.x - 85, top: p.y - 55, width: 170 }}
            onClick={(e) => onSelectRepoInFlow(p.repo, p.entryIds[0], e)}
          >
            <div className="flow-repo-glow" />
            <div className="flow-repo-name">{p.repo}</div>
            <div className="flow-repo-methods">
              {p.methods.map((m, mi) => (
                <div key={mi} className="flow-repo-method">{m}</div>
              ))}
            </div>
          </div>
        ))}
        </div>
      </div>
      {pz.zoomControls}
    </div>
  );
}

/* ---------------- Flows Mode: Flow Trace View ---------------- */
// Path equivalent — shows a repo's trace within a specific flow
function FlowTraceView({ flow, repo, graph, dims, onHome, onBack, onSelectNode, selectedNode, chatOpen }) {
  // Find this repo's step(s) in the flow
  const steps = useMemo(() => {
    const found = [];

    function walk(step, parentChannel, parentRepo) {
      if (step.repo === repo) {
        found.push({
          step,
          entersVia: parentChannel || (flow.originType === "external" ? flow.originChannel : flow.originLabel),
          entersFrom: parentRepo || (flow.originType === "external" ? (flow.originNoun || "external") : flow.originLabel),
        });
      }
      step.children.forEach((child) => {
        walk(child.step, child.channel, step.repo);
      });
    }

    walk(flow.step, null, null);
    return found;
  }, [flow, repo]);

  // Find downstream services this repo hands off to
  const downstream = useMemo(() => {
    const out = [];
    function walk(step) {
      if (step.repo === repo) {
        step.children.forEach((child) => {
          out.push({
            channel: child.channel,
            targetRepo: child.step.repo,
            targetMethod: child.step.method,
          });
        });
      }
      step.children.forEach((child) => walk(child.step));
    }
    walk(flow.step);
    // Deduplicate by channel+repo
    const seen = new Set();
    return out.filter((d) => {
      const key = d.channel + ":" + d.targetRepo;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [flow, repo]);

  // Get the call tree for the active step
  const [activeStepIdx, setActiveStepIdx] = useState(0);
  const entryPoint = useMemo(() => {
    const step = steps[activeStepIdx];
    const ep = step ? (graph.entry_points || []).find((e) => e.id === step.step.entryId) : null;
    return ep;
  }, [steps, activeStepIdx, graph]);

  // Reset active step when entering a new repo
  useEffect(() => { setActiveStepIdx(0); }, [repo]);

  const tree = entryPoint ? entryPoint.call_tree : null;

  // Reuse PathView's layout logic for the call tree
  const [expanded, setExpanded] = useState(() => {
    const set = new Set(["0"]);
    if (tree && tree.children) {
      tree.children.forEach((_, i) => set.add("0/" + i));
    }
    return set;
  });

  useEffect(() => {
    const set = new Set(["0"]);
    if (tree && tree.children) {
      tree.children.forEach((_, i) => set.add("0/" + i));
    }
    setExpanded(set);
  }, [entryPoint && entryPoint.id]);

  const toggleExpand = (path) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const layout = useMemo(() => {
    if (!tree) return { nodes: [], maxX: 0, maxY: 0 };
    const placed = [];
    let leafIdx = 0;

    function walk(node, depth, path) {
      const isExpanded = expanded.has(path);
      const kids = node.children || [];
      const hasKids = kids.length > 0;

      if (isExpanded && hasKids) {
        const childIndices = [];
        kids.forEach((k, i) => {
          walk(k, depth + 1, path + "/" + i);
          childIndices.push(placed.length - 1);
        });
        const firstChild = placed[childIndices[0]];
        const lastChild = placed[childIndices[childIndices.length - 1]];
        const y = (firstChild.y + lastChild.y) / 2;
        const x = depth * PV_HSPACE;
        placed.push({ data: node, path, depth, x, y, hasKids, isExpanded, children: childIndices });
      } else {
        const y = leafIdx * (PV_NODE_H + PV_VGAP);
        const x = depth * PV_HSPACE;
        placed.push({ data: node, path, depth, x, y, hasKids, isExpanded: false, children: [] });
        leafIdx++;
      }
    }

    walk(tree, 0, "0");

    const maxX = Math.max(...placed.map((p) => p.x), 0) + PV_NODE_W;
    const maxY = Math.max(...placed.map((p) => p.y), 0) + PV_NODE_H;
    return { nodes: placed, maxX, maxY };
  }, [tree, expanded]);

  // ── Edges — parent right-center → child left-center ─────────
  // Same measured-center anchoring as PathView.
  const [nodeHeights, setNodeHeights] = useState([]);
  const edges = useMemo(() => {
    const result = [];
    layout.nodes.forEach((node, idx) => {
      if (node.isExpanded && node.children.length > 0) {
        node.children.forEach((ci) => {
          const child = layout.nodes[ci];
          const parentH = nodeHeights[idx] || PV_NODE_H;
          const childH = nodeHeights[ci] || PV_NODE_H;
          result.push({
            x1: node.x + PV_NODE_W,
            y1: node.y + parentH / 2,
            x2: child.x,
            y2: child.y + childH / 2,
          });
        });
      }
    });
    return result;
  }, [layout.nodes, nodeHeights]);

  // Infinite canvas pan/zoom (reuse PathView's logic)
  const containerRef = useRef(null);

  // Measure real node heights after each layout so edge anchors hit true centers.
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const nodes = Array.from(el.querySelectorAll(".pv-node"));
    if (nodes.length === layout.nodes.length) {
      setNodeHeights(nodes.map((n) => n.offsetHeight));
    }
  }, [layout.nodes]); // eslint-disable-line

  const [viewport, setViewport] = useState({ x: 100, y: 50, zoom: 1 });
  const [animating, setAnimating] = useState(false);
  const dragRef = useRef({ active: false, startX: 0, startY: 0, vpX: 0, vpY: 0, moved: false });

  const centerView = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    setAnimating(true);
    setViewport({
      x: (el.clientWidth - layout.maxX) / 2,
      y: (el.clientHeight - layout.maxY) / 2 - 40,
      zoom: 1,
    });
    setTimeout(() => setAnimating(false), 400);
  }, [layout.maxX, layout.maxY]);

  // Reset expand state + center when switching entry points
  useEffect(() => {
    const set = new Set(["0"]);
    if (tree && tree.children) {
      tree.children.forEach((_, i) => set.add("0/" + i));
    }
    setExpanded(set);
    // Center after a tick so the container ref + layout are ready
    requestAnimationFrame(() => centerView());
  }, [entryPoint && entryPoint.id]); // eslint-disable-line

  // When a node is selected, pan so it stays visible left of the detail panel / chat
  useEffect(() => {
    if (!selectedNode) return;
    const nv = computeRevealOffset(containerRef, layout, selectedNode, viewport);
    if (nv) {
      setAnimating(true);
      setViewport(nv);
      setTimeout(() => setAnimating(false), 400);
    }
  }, [selectedNode, chatOpen]); // eslint-disable-line

  const onMouseDown = (e) => {
    if (e.target.closest(".tree-node") || e.target.closest(".exit-point")) return;
    dragRef.current = { active: true, startX: e.clientX, startY: e.clientY, vpX: viewport.x, vpY: viewport.y, moved: false };
  };
  const onMouseMove = (e) => {
    if (!dragRef.current.active) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) dragRef.current.moved = true;
    setViewport((vp) => ({ ...vp, x: dragRef.current.vpX + dx, y: dragRef.current.vpY + dy }));
  };
  const onMouseUp = () => { dragRef.current.active = false; };
  const onWheel = (e) => {
    e.preventDefault();
    const rect = containerRef.current.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    setViewport((vp) => {
      const delta = -e.deltaY * 0.0015;
      const newZoom = Math.max(0.2, Math.min(3, vp.zoom * (1 + delta)));
      const zr = newZoom / vp.zoom;
      return { x: mx - (mx - vp.x) * zr, y: my - (my - vp.y) * zr, zoom: newZoom };
    });
  };
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handler = (e) => onWheel(e);
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, [viewport]); // eslint-disable-line

  if (!entryPoint || !tree) {
    return (
      <div className="view">
        <div className="view-top">
          <Breadcrumb items={[
            { label: "Flows", onClick: onHome },
            { label: flow.name, onClick: onBack },
            { label: repo },
          ]} />
        </div>
        <p className="muted" style={{ padding: 40 }}>No trace data for this repo in this flow.</p>
      </div>
    );
  }

  return (
    <div className="path-view flow-trace">
      <div className="view-top">
        <Breadcrumb items={[
          { label: "Flows", onClick: onHome },
          { label: flow.name, onClick: onBack },
          { label: repo },
        ]} />
        <div className="view-hint">
          <span className="ep-tag" style={{ "--c": "#00d4ff" }}>FLOW TRACE</span>
          <span className="mono">{repo}</span>
          in {flow.name}
        </div>
      </div>

      {/* Entry context bar — shows all entry paths, selectable when multiple */}
      {steps.length > 0 && (
        <div className="flow-trace-bar">
          {steps.map((s, si) => (
            <React.Fragment key={si}>
              {si > 0 && <span className="flow-trace-or">or</span>}
              <button
                className={"flow-trace-entry" + (si === activeStepIdx ? " active" : "")}
                onClick={() => setActiveStepIdx(si)}
              >
                <span className="flow-trace-arrow">◀</span>
                <span className="flow-trace-label">enters via</span>
                <span className="flow-trace-channel mono">{s.entersVia}</span>
                <span className="flow-trace-from">from {s.entersFrom}</span>
              </button>
            </React.Fragment>
          ))}
        </div>
      )}

      <div
        className="pv-canvas"
        ref={containerRef}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        <div
          className={"pv-world" + (animating ? " animating" : "")}
          style={{
            transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.zoom})`,
            transformOrigin: "0 0",
          }}
        >
          <svg
            className="pv-edges"
            width={layout.maxX + 100}
            height={layout.maxY + 200}
            style={{ position: "absolute", top: 0, left: 0, pointerEvents: "none", overflow: "visible" }}
          >
            {edges.map((edge, i) => {
              const midX = (edge.x1 + edge.x2) / 2;
              return (
                <path
                  key={i}
                  d={`M ${edge.x1} ${edge.y1} C ${midX} ${edge.y1} ${midX} ${edge.y2} ${edge.x2} ${edge.y2}`}
                  fill="none"
                  stroke="#00d4ff"
                  strokeWidth="1.5"
                  opacity="0.35"
                />
              );
            })}
          </svg>

          {/* Call tree nodes (reuses pv-node structure) */}
          {layout.nodes.map((node) => {
            const d = node.data;
            const conf = confMeta(d.confidence);
            const isSel = selectedNode && sameNode(d, selectedNode);
            return (
              <div
                key={node.path}
                className={"pv-node" + (isSel ? " sel" : "") + (node.depth === 0 ? " root" : "")}
                style={{ left: node.x, top: node.y, width: PV_NODE_W }}
              >
                <button
                  className="pv-node-body"
                  onClick={() => { if (!dragRef.current.moved) onSelectNode(d); }}
                >
                  <div className="pv-method">{nodeDisplayName(d)}</div>
                  <div className="pv-loc mono">{fmtFile(d.file)}{d.line ? ":" + d.line : ""}</div>
                  {d.confidence && d.confidence !== "EXTRACTED" && (
                    <span className="conf-badge" style={{ "--c": conf.color }}>{d.confidence}</span>
                  )}
                </button>
                {node.hasKids && (
                  <button
                    className={"pv-toggle" + (node.isExpanded ? " expanded" : "")}
                    onClick={(e) => { e.stopPropagation(); toggleExpand(node.path); }}
                    title={node.isExpanded ? "Collapse" : "Expand"}
                  >
                    {node.isExpanded ? "▼" : "▶"}
                    {!node.isExpanded && (
                      <span className="pv-toggle-count">{d.children ? d.children.length : 0}</span>
                    )}
                  </button>
                )}
              </div>
            );
          })}

          {/* Exit point: downstream handoffs */}
          {downstream.length > 0 && (
            <div
              className="exit-point flow-exit"
              style={{ top: layout.maxY + 30, left: 0, width: PV_NODE_W + 100 }}
            >
              <div className="exit-point-label">EMITS TO</div>
              {downstream.map((d, i) => (
                <div key={i} className="exit-point-flow">
                  <span className="exit-point-channel">{d.channel}</span>
                  <span className="exit-point-arrow">→</span>
                  <span className="exit-point-target">{d.targetRepo}</span>
                  <span className="exit-point-next">▶ {d.targetMethod}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="zoom-controls">
        <button onClick={() => { setAnimating(true); setViewport(v => ({ ...v, zoom: Math.min(3, v.zoom + 0.15) })); setTimeout(() => setAnimating(false), 400); }} title="Zoom in">+</button>
        <span className="zoom-level">{Math.round(viewport.zoom * 100)}%</span>
        <button onClick={() => { setAnimating(true); setViewport(v => ({ ...v, zoom: Math.max(0.2, v.zoom - 0.15) })); setTimeout(() => setAnimating(false), 400); }} title="Zoom out">−</button>
        <button onClick={() => centerView()} title="Reset view">⤢</button>
      </div>
    </div>
  );
}


/* ---------------- Global Chat Widget (unified, context-aware) ---------------- */
function GlobalChat({ graph, view, selectedNode, entryPoint, detailOpen, flows, pid, open, onOpenChange }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [model, setModel] = useState("");
  const [models, setModels] = useState([]);
  const [error, setError] = useState("");
  const scrollRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    let alive = true;
    fetchJSON("/api/ai/models")
      .then((m) => { if (alive) { setModels(m.models || []); setModel(m.models && m.models.length ? m.models[0] : ""); } })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, loading]);

  useEffect(() => {
    if (open && messages.length === 0 && inputRef.current) {
      inputRef.current.focus();
    }
  }, [open, messages.length]);

  // ── Context: translate the current view + selection into (a) API context and (b) a readable label ──
  const ctx = useMemo(() => {
    const level = view.name;

    // ── Topology mode ──
    if (level === "galaxy") {
      return { payload: { entry_point_id: "", node: {} }, label: "Architecture overview", scope: "system" };
    }
    if (level === "service") {
      const repo = view.repo || "";
      return {
        payload: { entry_point_id: "", node: {}, repo },
        label: repo || "Service",
        scope: "repo",
      };
    }
    if (level === "path") {
      const epId = view.entryId || "";
      if (selectedNode) {
        const nm = selectedNode.method || selectedNode.class_name || "function";
        const loc = selectedNode.file ? (selectedNode.file.split("/").pop() + (selectedNode.line ? ":" + selectedNode.line : "")) : "";
        return {
          payload: { entry_point_id: epId, node: selectedNode },
          label: nm + (loc ? "  ·  " + loc : ""),
          scope: "node",
        };
      }
      return {
        payload: { entry_point_id: epId, node: {} },
        label: epId,
        scope: "entry",
      };
    }

    // ── Flows mode ──
    if (level === "flowIndex") {
      const crossRepo = flows ? flows.filter((f) => f.hasCrossRepo).length : 0;
      return {
        payload: { entry_point_id: "", node: {} },
        label: flows ? `${flows.length} flows · ${crossRepo} cross-repo` : "All flows",
        scope: "flowIndex",
      };
    }
    if (level === "flow") {
      const flow = flows ? flows.find((f) => f.id === view.flowId) : null;
      if (flow) {
        return {
          payload: { entry_point_id: "", node: {}, flow_context: {
            name: flow.name,
            origin_type: flow.originType,
            origin_kind: flow.originKind || "",
            origin_tag: flow.originTag || "",
            origin_label: flow.originLabel,
            repos: flow.repos,
            repo_count: flow.repoCount,
            hop_count: flow.hopCount,
          } },
          label: `Flow: ${flow.name}`,
          sub: `${flow.repoCount} repos · ${flow.hopCount} hops`,
          scope: "flow",
        };
      }
      return { payload: { entry_point_id: "", node: {} }, label: "Flow", scope: "flow" };
    }
    if (level === "flowTrace") {
      const flow = flows ? flows.find((f) => f.id === view.flowId) : null;
      const repo = view.repo || "";
      if (selectedNode) {
        const nm = selectedNode.method || selectedNode.class_name || "function";
        const loc = selectedNode.file ? (selectedNode.file.split("/").pop() + (selectedNode.line ? ":" + selectedNode.line : "")) : "";
        return {
          payload: { entry_point_id: entryPoint ? entryPoint.id : "", node: selectedNode, flow_context: flow ? {
            name: flow.name, repos: flow.repos,
          } : null },
          label: nm + (loc ? "  ·  " + loc : ""),
          sub: `in ${flow ? flow.name : "flow"} · ${repo}`,
          scope: "node",
        };
      }
      return {
        payload: { entry_point_id: entryPoint ? entryPoint.id : "", node: {}, repo, flow_context: flow ? {
          name: flow.name, repos: flow.repos,
        } : null },
        label: `${repo} in ${flow ? flow.name : "flow"}`,
        sub: flow ? `${flow.repoCount} repos · ${flow.hopCount} hops` : "",
        scope: "flowTrace",
      };
    }

    return { payload: { entry_point_id: "", node: {} }, label: "Whole system", scope: "system" };
  }, [view, selectedNode, flows, entryPoint]);

  const send = async (text) => {
    const msg = text.trim();
    if (!msg || loading) return;

    const newHistory = [...messages, { role: "user", content: msg }];
    // Live assistant message: content streams in, tools fill in as the AI calls them
    const liveMsg = { role: "assistant", content: "", tools: [], streaming: true };
    setMessages([...newHistory, liveMsg]);
    setInput("");
    setError("");
    setLoading(true);

    const patchLive = (fn) => {
      setMessages((prev) => {
        const next = prev.slice();
        const idx = next.findIndex((m) => m.streaming);
        if (idx !== -1) next[idx] = fn(next[idx]);
        return next;
      });
    };

    try {
      const res = await fetch(projPath(pid, "/ai/chat/stream"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...ctx.payload,
          messages: newHistory,
          model,
        }),
      });

      if (!res.ok || !res.body) {
        const body = await res.text().catch(() => "");
        throw new Error(`Stream failed (${res.status}) ${body.slice(0, 200)}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // Split on SSE double-newline
        const events = buf.split("\n\n");
        buf = events.pop();
        for (const evRaw of events) {
          const line = evRaw.trim();
          if (!line.startsWith("data:")) continue;
          const dataStr = line.slice(5).trim();
          if (!dataStr || dataStr === "[DONE]") continue;
          let ev;
          try { ev = JSON.parse(dataStr); } catch { continue; }

          if (ev.type === "token" && ev.text) {
            patchLive((m) => ({ ...m, content: m.content + ev.text }));
          } else if (ev.type === "tool_start") {
            patchLive((m) => ({
              ...m,
              tools: [...m.tools, {
                name: ev.name,
                args: ev.args || {},
                status: "running",
                result: "",
              }],
            }));
          } else if (ev.type === "tool_result") {
            patchLive((m) => ({
              ...m,
              tools: m.tools.map((t) =>
                t.name === ev.name && t.status === "running"
                  ? { ...t, status: "done", result: ev.result || "" }
                  : t
              ),
            }));
          } else if (ev.type === "error") {
            setError(ev.message || "Stream error");
          } else if (ev.type === "done") {
            // finalize
            patchLive((m) => ({ ...m, streaming: false }));
          }
        }
      }
      // Stream ended without explicit done — finalize anyway
      patchLive((m) => ({ ...m, streaming: false }));
    } catch (e) {
      setError(e.message);
      patchLive((m) => ({ ...m, streaming: false }));
    } finally {
      setLoading(false);
    }
  };

  const newChat = () => {
    setMessages([]);
    setError("");
    setInput("");
    setLoading(false);
  };

  // Generate suggestions dynamically from the current context + actual graph data
  const suggestions = useMemo(() => {
    const repos = graph.repos || [];
    const channels = graph.cross_repo_links || [];
    const eps = graph.entry_points || [];
    const out = [];

    if (ctx.scope === "node") {
      out.push("Explain what this function does and why.");
      out.push("What could go wrong here? Identify failure modes and risks.");
      out.push("Who calls this and what depends on it?");
      if (entryPoint) out.push("How does this fit into the “" + (entryPoint.method || "entry point") + "” flow?");
      return out;
    }
    if (ctx.scope === "flowTrace") {
      const flow = flows ? flows.find((f) => f.id === view.flowId) : null;
      out.push("How does this service participate in the " + (flow ? flow.name : "current") + " flow?");
      out.push("What does this service receive and what does it emit downstream?");
      out.push("What happens if this service fails or times out?");
      return out;
    }
    if (ctx.scope === "flow") {
      const flow = flows ? flows.find((f) => f.id === view.flowId) : null;
      const fname = flow ? flow.name : "this flow";
      out.push("Trace " + fname + " end-to-end — what happens at each step?");
      out.push("What are the failure modes and blast radius of " + fname + "?");
      out.push("Which service in " + fname + " is the bottleneck or riskiest?");
      return out;
    }
    if (ctx.scope === "flowIndex") {
      out.push("What are the main cross-service flows in this system?");
      out.push("Which flows have the most hops and why?");
      out.push("Are there any circular dependencies between services?");
      return out;
    }
    if (ctx.scope === "entry") {
      const ep = eps.find((e) => e.id === view.entryId);
      out.push("Walk me through the " + (ep ? ep.method_name || ep.id : "this") + " call flow step by step.");
      out.push("Where does this entry point lead and what can fail?");
      out.push("How deep and complex is this call path?");
      return out;
    }
    if (ctx.scope === "repo") {
      const repo = view.repo;
      const repoEps = eps.filter((e) => e.repo === repo);
      out.push("What are the entry points of " + repo + "?");
      out.push("Which services does " + repo + " talk to, and over what channels?");
      out.push("How complex is " + repo + " compared to the others?");
      return out;
    }
    // system scope default
    out.push("What services are in this system?");
    if (channels.length > 0) out.push("Show me all message flows between services.");
    if (repos.length > 1) out.push("Which service is the most complex?");
    if (repos.length > 0) {
      const repoCounts = {};
      eps.forEach((ep) => { repoCounts[ep.repo] = (repoCounts[ep.repo] || 0) + 1; });
      const topRepo = Object.entries(repoCounts).sort((a, b) => b[1] - a[1])[0];
      if (topRepo) out.push("What happens if " + topRepo[0] + " goes down?");
    }
    return out;
  }, [ctx, graph, view.entryId, view.flowId, entryPoint, flows]);

  return (
    <div className={"global-chat" + (open ? " open" : "") + (detailOpen ? " detail-open" : "")}>
      {/* Collapsed: floating button */}
      {!open && (
        <button className="chat-fab" onClick={() => onOpenChange(true)} aria-label="Open chat">
          <span className="chat-fab-icon">✦</span>
          {messages.length > 0 && (
            <span className="chat-fab-badge">{messages.length}</span>
          )}
        </button>
      )}

      {/* Expanded: chat window */}
      {open && (
        <div className="chat-window glass">
          <div className="chat-window-header">
            <div className="chat-window-title">
              <span className="chat-window-spark">✦</span>
              <span>Constellation AI</span>
            </div>
            <div className="chat-window-controls">
              {messages.length > 0 && (
                <button className="chat-new-btn" onClick={newChat} title="Start a new conversation">+ New</button>
              )}
              {models.length > 0 && (
                <select
                  className="chat-window-model"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  disabled={loading}
                >
                  {models.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              )}
              <button className="chat-window-close" onClick={() => onOpenChange(false)}>✕</button>
            </div>
          </div>

          {/* Context chip — always visible, shows what the AI can see */}
          <div className="chat-ctx">
            <span className="chat-ctx-dot"></span>
            <span className="chat-ctx-label">Context</span>
            <span className="chat-ctx-value" title={ctx.sub ? ctx.label + " · " + ctx.sub : ctx.label}>{ctx.label}</span>
            {ctx.sub && <span className="chat-ctx-sub" title={ctx.sub}>{ctx.sub}</span>}
          </div>

          <div className="chat-window-body" ref={scrollRef}>
            {messages.length === 0 && !loading && (
              <div className="chat-welcome">
                <div className="chat-welcome-icon">✦</div>
                <p>Ask me anything about the architecture.</p>
                <p className="muted small">I see what you're looking at and answer in that context — code, flows, or the whole system.</p>
                <div className="chat-suggestions">
                  {suggestions.map((s, i) => (
                    <button key={i} className="chat-suggestion" onClick={() => send(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              <div key={i} className={"chat-msg " + msg.role + (msg.streaming ? " streaming" : "")}>
                <div className="chat-msg-role">{msg.role === "user" ? "You" : "AI" + (msg.streaming ? " 🔮" : "")}</div>
                {msg.tools && msg.tools.length > 0 && (
                  <div className="chat-tools">
                    {msg.tools.map((t, ti) => (
                      <details key={ti} className={"chat-tool " + t.status}>
                        <summary>
                          {t.status === "running"
                            ? <span className="tool-spin">◌</span>
                            : t.status === "done"
                              ? <span className="tool-ok">✓</span>
                              : <span className="tool-err">!</span>}
                          <span className="tool-name">{t.name}</span>
                          {t.args && Object.keys(t.args).length > 0 && (
                            <span className="tool-args">{JSON.stringify(t.args)}</span>
                          )}
                          {t.status === "running" && <span className="tool-running">running…</span>}
                        </summary>
                        <pre className="tool-result">{t.result}</pre>
                      </details>
                    ))}
                  </div>
                )}
                <div className="chat-msg-text markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content || (msg.streaming ? "" : "")) }}></div>
                {msg.streaming && msg.content.length === 0 && msg.tools.length === 0 && (
                  <div className="ai-loading">Analyzing<span className="dots"></span></div>
                )}
                {msg.streaming && msg.content.length > 0 && <span className="stream-caret"></span>}
              </div>
            ))}

            {loading && !messages.some((m) => m.streaming) && (
              <div className="chat-msg assistant">
                <div className="chat-msg-role">AI</div>
                <div className="ai-loading">Analyzing<span className="dots"></span></div>
              </div>
            )}

            {error && (
              <div className="ai-error-msg">{error}</div>
            )}
          </div>

          <div className="chat-window-input">
            <input
              ref={inputRef}
              type="text"
              placeholder={"Ask about " + (ctx.scope === "node" ? "this function" : ctx.scope === "repo" ? "this service" : ctx.scope === "entry" ? "this flow" : "the architecture") + "…"}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") send(input); }}
              disabled={loading}
            />
            <button className="chat-send" onClick={() => send(input)} disabled={!input.trim() || loading}>
              ↑
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------------- App ---------------- */
function App() {
  // ── project-level state ─────────────────────────────────────────
  const [projects, setProjects] = useState([]);
  const [projStatus, setProjStatus] = useState("loading"); // loading | ready | error
  const [projError, setProjError] = useState("");
  const [activeId, setActiveId] = useState(null);
  const [activeMeta, setActiveMeta] = useState(null);

  // ── graph state for the active project ──────────────────────────
  const [graph, setGraph] = useState(null);
  const [graphStatus, setGraphStatus] = useState("idle"); // idle | loading | ready | error
  const [graphError, setGraphError] = useState("");

  // ── ingestion modal ─────────────────────────────────────────────
  const [ingest, setIngest] = useState(null); // {mode, pid?, projectName?, pull?} | null

  // ── upstream change detection (per-project staleness) ────────────
  const [updatesByPid, setUpdatesByPid] = useState({}); // {pid: {repos, total, stale_count}}

  const fetchProjectUpdates = useCallback((pid) => {
    fetchJSON(projPath(pid, "/updates"))
      .then((u) => setUpdatesByPid((prev) => ({ ...prev, [pid]: u })))
      .catch(() => {});
  }, []);

  const loadProjects = useCallback(() => {
    setProjStatus("loading");
    fetchJSON("/api/projects")
      .then((d) => {
        const list = d.projects || [];
        setProjects(list);
        setProjStatus("ready");
        // Refresh staleness for every analysed project (cheap ls-remote per repo).
        list.forEach((p) => { if (p.status !== "analyzing") fetchProjectUpdates(p.id); });
      })
      .catch((e) => { setProjError(e.message); setProjStatus("error"); });
  }, [fetchProjectUpdates]);

  // Quiet in-place refresh: updates the project list + staleness WITHOUT flipping
  // to the loading screen, so background refreshes (e.g. after a rescan) never
  // unmount an open modal. Use loadProjects() for full navigations/initial mount.
  const refreshProjects = useCallback(() => {
    fetchJSON("/api/projects")
      .then((d) => {
        const list = d.projects || [];
        setProjects(list);
        setProjStatus((s) => (s === "error" ? "ready" : s));
        list.forEach((p) => { if (p.status !== "analyzing") fetchProjectUpdates(p.id); });
      })
      .catch(() => {});
  }, [fetchProjectUpdates]);

  useEffect(() => { loadProjects(); }, [loadProjects]);

  // ── view state (unchanged) ──────────────────────────────────────
  const [view, setView] = useState({ name: "galaxy" });
  const [mode, setMode] = useState("topology"); // "topology" | "flows"
  const [flows, setFlows] = useState([]);
  const [selectedNode, setSelectedNode] = useState(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [dims, setDims] = useState({ w: window.innerWidth, h: window.innerHeight });
  // Zoom-drill transition: {x,y} = node center in viewport px, phase in|out
  const [zoomFx, setZoomFx] = useState(null);
  const zoomTimer = useRef(null);

  // Load the active project's scoped graph.
  useEffect(() => {
    if (!activeId) { setGraph(null); setGraphStatus("idle"); return; }
    let alive = true;
    setGraphStatus("loading");
    setGraph(null);
    setGraphError("");
    fetchJSON(projPath(activeId, "/graph"))
      .then((g) => { if (alive) { setGraph(g); setFlows(detectFlows(g)); setGraphStatus("ready"); } })
      .catch((e) => { if (alive) { setGraphError(e.message); setGraphStatus("error"); } });
    return () => { alive = false; };
  }, [activeId]);

  useEffect(() => {
    const onResize = () => setDims({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const openProject = (p) => {
    setActiveMeta(p);
    setActiveId(p.id);
    setSelectedNode(null);
    setView({ name: "galaxy" });
    setMode("topology");
    fetchProjectUpdates(p.id);
  };

  const backToProjects = () => {
    setActiveId(null);
    setActiveMeta(null);
    setSelectedNode(null);
    setGraph(null);
    loadProjects();
  };

  const refreshActive = () => {
    if (!activeId) { refreshProjects(); return; }
    fetchJSON(projPath(activeId, "/graph"))
      .then((g) => { setGraph(g); setFlows(detectFlows(g)); setGraphStatus("ready"); })
      .catch(() => {});
    fetchJSON("/api/projects/" + encodeURIComponent(activeId))
      .then(setActiveMeta).catch(() => {});
    fetchProjectUpdates(activeId);
    refreshProjects();
  };

  const onIngestComplete = (proj) => {
    const was = ingest;
    setIngest(null);
    if (was && was.mode === "create" && proj && proj.id) {
      setActiveMeta(proj);
      setActiveId(proj.id);
      setSelectedNode(null);
      setView({ name: "galaxy" });
      setMode("topology");
      fetchProjectUpdates(proj.id);
      loadProjects();
    } else {
      refreshActive();
    }
  };

  // Kick off a rescan for any project (active or from the list).
  const startRescan = (pid, name, pull) =>
    setIngest({ mode: "rescan", pid, projectName: name || "project", pull });

  const startAddRepo = (p) =>
    setIngest({ mode: "add", pid: p.id, projectName: p.name || "" });

  const deleteProject = (pid) => {
    fetch("/api/projects/" + encodeURIComponent(pid), { method: "DELETE" })
      .then(() => { if (pid === activeId) backToProjects(); else refreshProjects(); })
      .catch(() => {});
  };

  const goGalaxy = () => { setSelectedNode(null); setView({ name: "galaxy" }); };
  const goFlowIndex = () => { setSelectedNode(null); setView({ name: "flowIndex" }); };
  const stageH = dims.h;

  const switchMode = (m) => {
    setMode(m);
    setSelectedNode(null);
    if (m === "topology") setView({ name: "galaxy" });
    else setView({ name: "flowIndex" });
  };

  // Zoom-drill transition: a ✦ logo spins+expands over the clicked node to cover the
  // viewport and fade out — one continuous CSS animation. Mount the next view underneath
  // at peak coverage (~2/3 through), then unmount the seal once the fade finishes.
  const drill = (x, y, nav) => {
    clearTimeout(zoomTimer.current);
    setZoomFx({ x, y });
    zoomTimer.current = setTimeout(() => {
      nav();
      clearTimeout(zoomTimer.current);
      zoomTimer.current = setTimeout(() => setZoomFx(null), 1100);
    }, 500);
  };

  // Compute a node's viewport center from a DOM ref currently being interacted with.
  const centerOf = (el) => {
    const r = (el || document.body).getBoundingClientRect();
    return [r.x + r.width / 2, r.y + r.height / 2];
  };

  // ── Projects landing (no project selected) ──────────────────────
  if (!activeId) {
    if (projStatus === "loading") return (<React.Fragment><Starfield /><LoadingScreen /></React.Fragment>);
    if (projStatus === "error") return (<React.Fragment><Starfield /><ErrorScreen message={projError} /></React.Fragment>);
    return (
      <React.Fragment>
        <Starfield />
        <ProjectsView
          projects={projects}
          loading={false}
          onOpen={openProject}
          onNew={() => setIngest({ mode: "create" })}
          onDelete={deleteProject}
          updatesByPid={updatesByPid}
          onAddRepo={startAddRepo}
          onRescan={(p, pull) => startRescan(p.id, p.name, pull)}
        />
        {ingest && (
          <IngestionModal
            mode={ingest.mode}
            projectName={ingest.projectName}
            pid={ingest.pid}
            pull={ingest.pull}
            onComplete={onIngestComplete}
            onScanDone={refreshProjects}
            onClose={() => setIngest(null)}
          />
        )}
      </React.Fragment>
    );
  }

  // ── Active project shell ────────────────────────────────────────
  if (graphStatus === "loading") return (<React.Fragment><Starfield /><LoadingScreen /></React.Fragment>);
  if (graphStatus === "error") return (<React.Fragment><Starfield /><ErrorScreen message={graphError} /></React.Fragment>);
  if (!graph) return null;

  return (
    <div className="app">
      <Starfield />
      <Header
        graph={graph}
        mode={mode}
        onModeChange={switchMode}
        projectName={(activeMeta && activeMeta.name) || "Project"}
        onHome={backToProjects}
        stale={!!(updatesByPid[activeId] && updatesByPid[activeId].stale_count > 0)}
      />
      <main className="stage">
        {/* ── Topology mode (existing) ── */}
        {mode === "topology" && view.name === "galaxy" && (
          <div className="view" key="galaxy">
            <GalaxyView
              graph={graph}
              dims={{ w: dims.w, h: stageH }}
              onSelectRepo={(repo, e) => {
                const [x, y] = centerOf(e && e.currentTarget);
                drill(x, y, () => { setSelectedNode(null); setView({ name: "service", repo }); });
              }}
            />
          </div>
        )}
        {mode === "topology" && view.name === "service" && (
          <div className="view" key={"service-" + view.repo}>
            <ServiceView
              graph={graph}
              repo={view.repo}
              flows={flows}
              onHome={goGalaxy}
              onSelectEntry={(id, e) => {
                const [x, y] = centerOf(e && e.currentTarget);
                drill(x, y, () => { setSelectedNode(null); setView({ name: "path", entryId: id }); });
              }}
              onSelectFlow={(flowId) => {
                setSelectedNode(null);
                setMode("flows");
                setView({ name: "flow", flowId });
              }}
              onOpenRepo={(repo, e) => {
                const [x, y] = centerOf(e && e.currentTarget);
                drill(x, y, () => { setSelectedNode(null); setView({ name: "service", repo }); });
              }}
            />
          </div>
        )}
        {mode === "topology" && view.name === "path" && (() => {
          const ep = graph.entry_points.find((e) => e.id === view.entryId);
          if (!ep) return <div className="view"><p className="muted" style={{ padding: 40 }}>Entry point not found.</p></div>;
          return (
            <div className="view" key={"path-" + view.entryId}>
              <PathView
                entryPoint={ep}
                graph={graph}
                flows={flows}
                onHome={goGalaxy}
                onBack={() => setView({ name: "service", repo: ep.repo })}
                selectedNode={selectedNode}
                onSelectNode={(n) => setSelectedNode(n)}
                chatOpen={chatOpen}
                onOpenRepo={(repo, e) => {
                  const [x, y] = centerOf(e && e.currentTarget);
                  drill(x, y, () => { setSelectedNode(null); setView({ name: "service", repo }); });
                }}
                onOpenEntry={(id, e) => {
                  const [x, y] = centerOf(e && e.currentTarget);
                  drill(x, y, () => { setSelectedNode(null); setView({ name: "path", entryId: id }); });
                }}
                onOpenFlow={(flowId) => {
                  setSelectedNode(null);
                  setMode("flows");
                  setView({ name: "flow", flowId });
                }}
              />
            </div>
          );
        })()}

        {/* ── Flows mode (new) ── */}
        {mode === "flows" && view.name === "flowIndex" && (
          <div className="view" key="flowIndex">
            <FlowIndexView
              graph={graph}
              flows={flows}
              dims={{ w: dims.w, h: stageH }}
              onSelectFlow={(f, e) => {
                const [x, y] = centerOf(e && e.currentTarget);
                drill(x, y, () => { setSelectedNode(null); setView({ name: "flow", flowId: f.id }); });
              }}
            />
          </div>
        )}
        {mode === "flows" && view.name === "flow" && (() => {
          const flow = flows.find((f) => f.id === view.flowId);
          if (!flow) return <div className="view"><p className="muted" style={{ padding: 40 }}>Flow not found.</p></div>;
          return (
            <div className="view" key={"flow-" + view.flowId}>
              <FlowView
                flow={flow}
                graph={graph}
                dims={{ w: dims.w, h: stageH }}
                onHome={goFlowIndex}
                onBack={goFlowIndex}
                onSelectRepoInFlow={(repo, entryId, e) => {
                  const [x, y] = centerOf(e && e.currentTarget);
                  drill(x, y, () => { setSelectedNode(null); setView({ name: "flowTrace", flowId: flow.id, repo }); });
                }}
              />
            </div>
          );
        })()}
        {mode === "flows" && view.name === "flowTrace" && (() => {
          const flow = flows.find((f) => f.id === view.flowId);
          if (!flow) return <div className="view"><p className="muted" style={{ padding: 40 }}>Flow not found.</p></div>;
          return (
            <div className="view" key={"flowTrace-" + view.flowId + "-" + view.repo}>
              <FlowTraceView
                flow={flow}
                repo={view.repo}
                graph={graph}
                dims={{ w: dims.w, h: stageH }}
                onHome={goFlowIndex}
                onBack={() => setView({ name: "flow", flowId: flow.id })}
                selectedNode={selectedNode}
                onSelectNode={(n) => setSelectedNode(n)}
                chatOpen={chatOpen}
              />
            </div>
          );
        })()}
      </main>

      {selectedNode && (view.name === "path" || view.name === "flowTrace") && (() => {
        const entryId = view.name === "path" ? view.entryId : null;
        const ep = entryId ? graph.entry_points.find((e) => e.id === entryId) : null;
        // For flowTrace, find the entry point from the flow step
        if (view.name === "flowTrace") {
          const flow = flows.find((f) => f.id === view.flowId);
          const step = flow ? findStepByRepo(flow.step, view.repo) : null;
          const flowEp = step ? graph.entry_points.find((e) => e.id === step.entryId) : null;
          if (flowEp) {
            return (
              <DetailPanel
                node={selectedNode}
                entryPoint={flowEp}
                onClose={() => setSelectedNode(null)}
                pid={activeId}
              />
            );
          }
        }
        if (!ep) return null;
        return (
          <DetailPanel
            node={selectedNode}
            entryPoint={ep}
            onClose={() => setSelectedNode(null)}
            pid={activeId}
          />
        );
      })()}

      <GlobalChat
        graph={graph}
        view={view}
        selectedNode={selectedNode}
        flows={flows}
        pid={activeId}
        open={chatOpen}
        onOpenChange={setChatOpen}
        entryPoint={(() => {
          if (view.name === "path") return graph.entry_points.find((e) => e.id === view.entryId);
          if (view.name === "flowTrace") {
            const flow = flows.find((f) => f.id === view.flowId);
            const step = flow ? findStepByRepo(flow.step, view.repo) : null;
            return step ? graph.entry_points.find((e) => e.id === step.entryId) : null;
          }
          return null;
        })()}
        detailOpen={!!selectedNode && (view.name === "path" || view.name === "flowTrace")}
      />

      {ingest && (
        <IngestionModal
          mode={ingest.mode}
          pid={ingest.pid}
          projectName={ingest.projectName}
          pull={ingest.pull}
          onComplete={onIngestComplete}
          onScanDone={refreshActive}
          onClose={() => setIngest(null)}
        />
      )}

      {zoomFx && <>
        <svg className="zoom-spark" style={{ left: zoomFx.x, top: zoomFx.y }} viewBox="0 0 100 100" width="120" height="120" aria-hidden="true">
          <defs>
            <linearGradient id="sparkGrad" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="#2fa8cc" />
              <stop offset="50%" stopColor="#0f8fb8" />
              <stop offset="100%" stopColor="#0a6b8c" />
            </linearGradient>
          </defs>
          <path
            d="M 50,5
               C 50,35 65,50 95,50
               C 65,50 50,65 50,95
               C 50,65 35,50 5,50
               C 35,50 50,35 50,5 Z"
            fill="url(#sparkGrad)"
            stroke="none"
          />
        </svg>
        <div className="zoom-orb" style={{ left: zoomFx.x, top: zoomFx.y }} aria-hidden="true" />
      </>}
    </div>
  );
}

// Helper to find the first step matching a repo in a flow step tree
function findStepByRepo(step, repo) {
  if (step.repo === repo) return step;
  for (const child of step.children) {
    const found = findStepByRepo(child.step, repo);
    if (found) return found;
  }
  return null;
}

/* ---------------- Projects (multi-project landing + ingestion) ---------------- */

// Build a project-scoped API path. Every graph-dependent endpoint lives under
// /api/projects/<pid>/...; /api/ai/models stays global (not project-specific).
const projPath = (pid, rest) => "/api/projects/" + encodeURIComponent(pid) + rest;

function fmtRelative(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (isNaN(diff)) return "";
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  if (diff < 86400 * 7) return Math.floor(diff / 86400) + "d ago";
  return d.toLocaleDateString();
}

function StatusBadge({ status, stale }) {
  const map = {
    ready: { label: "Ready", cls: "ok" },
    analyzing: { label: "Analyzing…", cls: "busy" },
    error: { label: "Error", cls: "err" },
  };
  let m = map[status] || { label: status || "—", cls: "" };
  // A ready project whose remote HEAD has moved reads "Out of date".
  if (stale && status === "ready") m = { label: "Out of date", cls: "stale" };
  return <span className={"status-badge " + m.cls}>{m.label}</span>;
}

function ProjectCard({ index, p, updates, onOpen, onAddRepo, onRescan, onDelete }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const stats = p.stats || {};
  const repos = p.repos || [];
  const stale = !!(updates && updates.stale_count > 0);
  // "Update" only makes sense for repos that have a remote to pull.
  const hasRemote = repos.some((r) => !(r.source || "").startsWith("local:"));
  const busy = p.status === "analyzing";
  const close = () => setMenuOpen(false);
  const trash = (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="3 6 5 6 21 6"></polyline>
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>
      <path d="M10 11v6M14 11v6"></path>
      <path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"></path>
    </svg>
  );
  return (
    <div
      className={"project-card glass" + (busy ? " busy" : "")}
      style={{ animationDelay: (index || 0) * 70 + "ms" }}
      onClick={() => !busy && onOpen(p)}
    >
      <div className="pc-top">
        <StatusBadge status={p.status} stale={stale} />
      </div>
      <div className="pc-name">{p.name}</div>
      <div className="pc-meta muted">
        {repos.length} repos · {stats.entry_points || 0} entry points · {stats.cross_repo_links || 0} links
      </div>
      <div className="pc-repos">
        {repos.slice(0, 4).map((r) => (
          <span className="repo-chip" key={r.name} title={r.source}>{r.name}</span>
        ))}
        {repos.length > 4 && <span className="repo-chip more">+{repos.length - 4}</span>}
      </div>
      <div className="pc-foot">
        <span className="muted small">Updated {fmtRelative(p.updated_at)}</span>
        <button
          className="pc-manage-btn"
          title="Manage project"
          onClick={(e) => { e.stopPropagation(); setMenuOpen((o) => !o); }}
        >
          Manage ⋯
        </button>
      </div>
      {menuOpen && (
        <>
          <div className="menu-backdrop" onClick={(e) => { e.stopPropagation(); close(); }} />
          <div className="manage-menu" onClick={(e) => e.stopPropagation()}>
            <button
              className="manage-item"
              title={stale ? "Pull latest for the stale repo(s), then re-analyse" : "All repos are up to date"}
              disabled={busy || !hasRemote || !stale}
              onClick={() => { close(); onRescan(p, true); }}
            >
              ↑ Update{stale ? " · " + updates.stale_count + " stale" : ""}
            </button>
            <button
              className="manage-item"
              title="Re-extract the graph from the current source (no download)"
              disabled={busy}
              onClick={() => { close(); onRescan(p, false); }}
            >
              ↻ Rescan
            </button>
            <button
              className="manage-item"
              title="Add another repository to this project"
              disabled={busy}
              onClick={() => { close(); onAddRepo(p); }}
            >
              + Add repo
            </button>
            <div className="manage-sep" />
            <button
              className="manage-item danger"
              title="Delete project"
              disabled={busy}
              onClick={() => {
                close();
                if (confirm("Delete project '" + p.name + "'? This removes its graph and cloned repos.")) onDelete(p.id);
              }}
            >
              {trash} Delete
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function ProjectsView({ projects, loading, onOpen, onNew, onDelete, updatesByPid, onAddRepo, onRescan }) {
  return (
    <div className="projects-view">
      <div className="projects-inner">
        <div className="projects-head">
          <div className="projects-title-block">
            <div className="projects-title-row">
              <span className="projects-logo" aria-hidden="true">✦</span>
              <h1 className="projects-title">Constellation</h1>
            </div>
            <p className="projects-sub muted">
              Map any microservice architecture from source. Pick a project to enter its galaxy.
            </p>
          </div>
          <button className="btn-primary" onClick={onNew}>+ New project</button>
        </div>

        {loading ? (
          <div className="screen-center"><div className="orbit-loader"><span></span></div></div>
        ) : projects.length === 0 ? (
          <div className="empty-state glass">
            <div className="empty-mark">✦</div>
            <h2>No projects yet</h2>
            <p className="muted">Create your first project by importing one or more Git repositories.</p>
            <button className="btn-primary big" onClick={onNew}>Create a project</button>
          </div>
        ) : (
          <div className="project-grid">
            {projects.map((p, i) => (
              <ProjectCard
                key={p.id}
                index={i}
                p={p}
                updates={updatesByPid ? updatesByPid[p.id] : null}
                onOpen={onOpen}
                onAddRepo={onAddRepo}
                onRescan={onRescan}
                onDelete={onDelete}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// Derives progress-bar state purely from the ingested log stream.
// Phases run clone → scan → graph → link → done; determinate counts come
// from per-repo engine lines, indeterminate when no repo total is known
// (rescan) or the phase has no countable steps.
const INGEST_PHASE_ORDER = { clone: 0, scan: 1, graph: 2, link: 3, done: 4 };
const SCANNING_RE = /^\[scan\] .+: scanning /;
const CLONE_DONE_RE = /^\[clone\] .+ ready at |^\[clone\] Using local repo /;

function computeIngestProgress(logs, repoTotal) {
  let phase = "info";
  for (let i = logs.length - 1; i >= 0; i--) {
    const p = logs[i].phase;
    if (INGEST_PHASE_ORDER[p] != null) { phase = p; break; }
  }
  let determinate = false;
  let done = 0;
  const cap = (n) => (repoTotal != null ? Math.min(n, repoTotal) : n);
  let label = logs.length ? "Working…" : "Starting…";
  if (phase === "clone") {
    done = cap(logs.filter((l) => l.phase === "clone" && CLONE_DONE_RE.test(l.message)).length);
    determinate = repoTotal != null;
    label = determinate ? "Cloning " + done + "/" + repoTotal + " repos" : "Syncing repositories…";
  } else if (phase === "scan") {
    done = cap(logs.filter((l) => l.phase === "scan" && SCANNING_RE.test(l.message)).length);
    determinate = repoTotal != null;
    label = determinate ? "Scanning " + done + "/" + repoTotal + " repos" : "Scanning repositories…";
  } else if (phase === "graph") {
    label = "Building call trees…";
  } else if (phase === "link") {
    label = "Finding cross-repo links…";
  } else if (phase === "done") {
    determinate = true;
    done = repoTotal != null ? repoTotal : 1;
    label = "Complete";
  }
  const total = determinate ? Math.max(done, repoTotal != null ? repoTotal : 1) : 0;
  const pct = determinate ? Math.min(100, Math.round((done / total) * 100)) : null;
  return { determinate, pct, label };
}

// Reads the SSE ingestion stream from /api/projects (create) or
// /api/projects/<pid>/repos (add). Mirrors the GlobalChat SSE reader.
function IngestionModal({ mode, pid, projectName, pull, onComplete, onClose, onScanDone }) {
  const isCreate = mode === "create";
  const isRescan = mode === "rescan";
  const [name, setName] = useState(projectName || "");
  const [urls, setUrls] = useState([""]);
  const [logs, setLogs] = useState([]);
  const [status, setStatus] = useState("idle"); // idle | running | done | error
  const [error, setError] = useState("");
  const logEndRef = useRef(null);
  const didAutoRun = useRef(false);

  useEffect(() => {
    if (logEndRef.current) logEndRef.current.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  // Rescan has no form — kick it off automatically the first time the modal mounts.
  useEffect(() => {
    if (isRescan && !didAutoRun.current) {
      didAutoRun.current = true;
      submit();
    }
  }, [isRescan]);

  const validUrls = urls.map((u) => u.trim()).filter(Boolean);
  const setUrlAt = (i, v) => setUrls((prev) => prev.map((u, idx) => (idx === i ? v : u)));
  const addRow = () => setUrls((prev) => [...prev, ""]);
  const removeRow = (i) => setUrls((prev) => prev.filter((_, idx) => idx !== i));

  const submit = async () => {
    setError("");
    setLogs([]);
    setStatus("running");
    const endpoint = isCreate
      ? "/api/projects"
      : isRescan
        ? projPath(pid, "/rescan" + (pull ? "?pull=true" : ""))
        : projPath(pid, "/repos");
    const body = isCreate
      ? { name: (name || "").trim() || "Untitled Project", repos: validUrls }
      : isRescan
        ? {}
        : { repos: validUrls };
    let sawDone = false;
    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok || !res.body) {
        const t = await res.text().catch(() => "");
        throw new Error("Failed (" + res.status + ") " + t.slice(0, 200));
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const events = buf.split("\n\n");
        buf = events.pop();
        for (const raw of events) {
          const line = raw.trim();
          if (!line.startsWith("data:")) continue;
          const dataStr = line.slice(5).trim();
          if (!dataStr) continue;
          let ev;
          try { ev = JSON.parse(dataStr); } catch { continue; }
          if (ev.type === "log") {
            setLogs((prev) => [...prev, { phase: ev.phase, message: ev.message }]);
          } else if (ev.type === "done") {
            sawDone = true;
            setStatus("done");
            setLogs((prev) => [...prev, { phase: "done", message: "Analysis complete." }]);
            const proj = ev.project;
            if (isRescan) {
              // Refresh the underlying view but keep this modal open — the user
              // dismisses it explicitly once they've reviewed the scan log.
              onScanDone && onScanDone(proj);
            } else {
              setTimeout(() => onComplete && onComplete(proj), 750);
            }
            return;
          } else if (ev.type === "error") {
            setStatus("error");
            setError(ev.message || "Ingestion failed");
          }
        }
      }
      if (!sawDone) setStatus("error");
    } catch (e) {
      setStatus("error");
      setError(e.message);
    }
  };

  const busy = status === "running";

  const progress = computeIngestProgress(logs, isRescan ? null : validUrls.length);

  return (
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose && onClose(); }}>
      <div className="modal-card glass">
        <div className="modal-head">
          <h2>{isCreate
            ? "New project"
            : isRescan
              ? (pull ? "Pull & rescan " + (projectName || "project") : "Rescan " + (projectName || "project"))
              : "Add repositories to " + (projectName || "project")}</h2>
          <button className="modal-close" onClick={() => onClose && onClose()} disabled={busy} title="Close">✕</button>
        </div>

        <div className="modal-body">
          {isCreate && (
            <label className="field">
              <span className="field-label">Project name</span>
              <input
                className="text-input"
                type="text"
                placeholder="e.g. Order Platform"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={busy}
                autoFocus
              />
            </label>
          )}

          {!isRescan && (
            <label className="field">
              <span className="field-label">Git repository URLs ({validUrls.length})</span>
              <div className="url-list">
                {urls.map((u, i) => (
                  <div className="url-row" key={i}>
                    <input
                      className="text-input mono"
                      type="text"
                      placeholder="https://github.com/org/repo.git  or  local:path/to/repo"
                      value={u}
                      onChange={(e) => setUrlAt(i, e.target.value)}
                      disabled={busy}
                    />
                    {urls.length > 1 && (
                      <button className="url-remove" onClick={() => removeRow(i)} disabled={busy} title="Remove">✕</button>
                    )}
                  </div>
                ))}
              </div>
              <button className="btn-link" onClick={addRow} disabled={busy}>+ add another repo</button>
            </label>
          )}

          {!isRescan && (
            <p className="muted small">
              Repos are cloned (shallow) and analysed together so cross-service links are detected.
              Use <code>local:path</code> to add an existing folder on disk (git-backed ones are still tracked for updates).
              {!isCreate && " The project is re-analysed as a whole when you add a repo."}
            </p>
          )}

          {isRescan && (
            <p className="muted small">
              {pull
                ? "Stale repositories are re-cloned to their latest commit, then the whole project is re-analysed."
                : "Re-extracts the graph from the current source on disk — no download. Use this after the engine itself changes."}
            </p>
          )}

          {(status === "running" || status === "done" || status === "error") && (
            <div className="ingest-log">
              <div className="ingest-log-head">
                {status === "running" && <span className="badge busy">Working…</span>}
                {status === "done" && <span className="badge ok">Done</span>}
                {status === "error" && <span className="badge err">Failed</span>}
              </div>
              {status !== "error" && (
                <div className="ingest-progress">
                  <div className="ingest-progress-track">
                    <div
                      className={
                        "ingest-progress-fill" +
                        (progress.determinate ? "" : " indeterminate") +
                        (status === "done" ? " done" : "")
                      }
                      style={progress.determinate ? { width: progress.pct + "%" } : undefined}
                    />
                  </div>
                  <div className="ingest-progress-meta">
                    <span className="ingest-progress-label">{progress.label}</span>
                    {progress.determinate && <span className="ingest-progress-pct">{progress.pct}%</span>}
                  </div>
                </div>
              )}
              <div className="log-stream mono">
                {logs.length === 0 && <div className="muted small">Starting…</div>}
                {logs.map((l, i) => (
                  <div className={"log-line phase-" + (l.phase || "info")} key={i}>{l.message}</div>
                ))}
                <div ref={logEndRef} />
              </div>
            </div>
          )}

          {error && <div className="ingest-error">{error}</div>}
        </div>

        <div className="modal-foot">
          {isRescan ? (
            <button
              className="btn-primary"
              onClick={() => onClose && onClose()}
              disabled={busy}
              title={busy ? "Scan in progress…" : "Close"}
            >
              {busy ? "Scanning…" : status === "error" ? "Close" : "Done · Close"}
            </button>
          ) : (
            <>
              <button className="btn-ghost" onClick={() => onClose && onClose()} disabled={busy}>Cancel</button>
              <button
                className="btn-primary"
                onClick={submit}
                disabled={busy || validUrls.length === 0 || (isCreate && !(name || "").trim())}
              >
                {busy ? "Importing…" : isCreate ? "Create & import" : "Add & re-analyse"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// App is exported for the entry point in main.jsx
export default App;
