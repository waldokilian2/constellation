/* ============================================================
   CONSTELLATION — Codebase Mapper frontend
   React 18 + Vite (ES module build)
   ============================================================ */

import React, { useState, useEffect, useRef, useMemo, useLayoutEffect, useCallback } from "react";
import ChangePlannerView from "./changePlanner.jsx";
import ConversationMenu from "./ConversationMenu.jsx";
import MarkdownContent from "./Markdown.jsx";
import ReasoningBlock from "./ReasoningBlock.jsx";
import ToolSteps from "./ToolSteps.jsx";
import { useConversationChat } from "./useConversationChat.js";
import "./styles.css";

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

const CONFIDENCE = {
  EXTRACTED: { color: "#34d399" },
  INFERRED:  { color: "#fbbf24" },
  AMBIGUOUS: { color: "#f87171" },
  TRUNCATED: { color: "#a78bfa" },
};
const confMeta = (c) => CONFIDENCE[c] || { color: "#94a3b8" };

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
function Header({ graph, mode, onModeChange, onHome, stale, crumbs }) {
  const gen = graph && graph.generated_at
    ? new Date(graph.generated_at).toLocaleString()
    : "";
  const statusLabel = stale ? "Stale" : "Up to date";
  const statusCls = stale ? "stale" : "ok";
  return (
    <header className="topbar glass">
      <div className="hdr-left">
        <div className="brand">
          <button className="brand-mark-btn" onClick={onHome} title="Back to projects">
            <span className="brand-mark">✦</span>
          </button>
          <div>
            <div className="brand-name">CONSTELLATION</div>
          </div>
        </div>
        <HeaderBreadcrumb crumbs={crumbs} />
      </div>
      {onModeChange && (
        <div className="mode-toggle">
          <button
            className={"mode-btn" + (mode === "topology" ? " active" : "")}
            onClick={() => onModeChange("topology")}
          >Topology</button>
          <button
            className={"mode-btn" + (mode === "flows" ? " active" : "")}
            onClick={() => onModeChange("flows")}
          >Flows</button>
          <button
          className={"mode-btn" + (mode === "dead" ? " active" : "")}
          onClick={() => onModeChange("dead")}
        >Dead code</button>
        <button
          className={"mode-btn" + (mode === "planner" ? " active" : "")}
          onClick={() => onModeChange("planner")}
        >Planner</button>
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

/* ---------------- Header breadcrumb ---------------- */
// Single navigation for the whole app, rendered at the start of the header.
// Shows at most the last 2 pages of the current trail; any deeper ancestors
// collapse into a "⋯" button whose dropdown lets you jump straight up to them.
// Each visible crumb is clickable (steps up one level) unless it's the current page.
function HeaderBreadcrumb({ crumbs }) {
  const [moreOpen, setMoreOpen] = useState(false);
  if (!crumbs || crumbs.length === 0) return null;

  const showMore = crumbs.length > 2;
  const hidden = showMore ? crumbs.slice(0, crumbs.length - 2) : [];
  const visible = showMore ? crumbs.slice(-2) : crumbs;

  return (
    <nav className="hdr-breadcrumb" aria-label="Breadcrumb">
      {showMore && (
        <div className="hdr-more">
          <button
            className="hdr-more-btn"
            onClick={() => setMoreOpen((o) => !o)}
            aria-expanded={moreOpen}
            aria-label="Show earlier pages"
            title="Show earlier pages"
          >⋯</button>
          {moreOpen && (
            <>
              <div className="hdr-menu-backdrop" onClick={() => setMoreOpen(false)} />
              <div className="hdr-crumb-menu" role="menu">
                {hidden.map((c, i) => (
                  <button
                    key={i}
                    role="menuitem"
                    className="hdr-crumb-menu-item"
                    onClick={() => { setMoreOpen(false); c.onClick && c.onClick(); }}
                  >
                    <span className="hdr-crumb-menu-arrow">‹</span>{c.label}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}
      {showMore && <span className="hdr-crumb-sep">›</span>}
      {visible.map((c, i) => (
        <span className="hdr-crumb-wrap" key={"v" + i}>
          {i > 0 && <span className="hdr-crumb-sep">›</span>}
          {c.onClick
            ? <button className="hdr-crumb link" onClick={c.onClick}>{c.label}</button>
            : c.current
              ? <span className="hdr-crumb current">{c.label}</span>
              : <span className="hdr-crumb">{c.label}</span>}
        </span>
      ))}
    </nav>
  );
}

// Map the current view + mode to the full navigation trail (project root first).
// The last crumb is the current page; earlier ones are clickable "up" targets.
function buildCrumbs(view, mode, graph, flows, projectName, nav) {
  // The project root IS the galaxy view, so the project-name crumb doubles as
  // the galaxy entry point: current when the galaxy is shown, a link back to it
  // otherwise. No separate "Galaxy" crumb is appended (avoids the root/galaxy
  // duplication, e.g. "Projects › Java EE › Galaxy").
  const projectCrumb = (current) => ({
    label: projectName || "Project",
    ...(current ? { current: true } : { onClick: nav.goGalaxy }),
  });
  const root = [
    { label: "Projects", onClick: nav.goProjects },
    projectCrumb(false),
  ];
  const methodLabel = (ep) => ep.method || ep.id.split(":").pop();

  if (mode === "topology") {
    if (view.name === "galaxy") {
      return [{ label: "Projects", onClick: nav.goProjects }, projectCrumb(true)];
    }
    if (view.name === "gaps") {
      return [...root, { label: "Gaps", current: true }];
    }
    if (view.name === "solar") {
      return [...root, { label: view.repo, current: true }];
    }
    if (view.name === "path") {
      const ep = graph && (graph.entry_points || []).find((e) => e.id === view.entryId);
      if (ep) {
        return [
          ...root,
          { label: ep.repo, onClick: () => nav.goSolar(ep.repo) },
          { label: methodLabel(ep), current: true },
        ];
      }
    }
  } else if (mode === "dead") {
    if (view.name === "dead") {
      return [...root, { label: "Dead code", current: true }];
    }
    if (view.name === "path") {
      const ep = graph && (graph.entry_points || []).find((e) => e.id === view.entryId);
      if (ep) {
        return [
          ...root,
          { label: "Dead code", onClick: nav.goDead },
          { label: ep.repo }, // context — no repo page in dead-code mode
          { label: methodLabel(ep), current: true },
        ];
      }
    }
  } else if (mode === "flows") {
    const flow = (flows || []).find((f) => f.id === view.flowId);
    if (view.name === "flowIndex") {
      return [...root, { label: "Flows", current: true }];
    }
    if (view.name === "flow") {
      return [
        ...root,
        { label: "Flows", onClick: nav.goFlowIndex },
        { label: flow ? flow.name : "Flow", current: true },
      ];
    }
    if (view.name === "flowTrace") {
      return [
        ...root,
        { label: "Flows", onClick: nav.goFlowIndex },
        { label: flow ? flow.name : "Flow", onClick: flow ? () => nav.goFlow(flow.id) : undefined },
        { label: view.repo, current: true },
      ];
    }
  }
  if (mode === "planner") {
    return [...root, { label: "AI Change Planner", current: true }];
  }
  return root;
}

/* ---------------- Legend ---------------- */
function Legend({ types = [] }) {
  const shown = Object.keys(TYPE_META).filter((k) => types.includes(k));
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
      <div className="legend-sep">Links</div>
      {["async", "sync", "both"].map((k) => (
        <div className="legend-item" key={k}>
          <span className="legend-line" style={{ background: EDGE_KINDS[k].color }}></span>
          {EDGE_KINDS[k].label}
        </div>
      ))}
      <div className="legend-hint">Click a repo to zoom in</div>
    </div>
  );
}

/* ---------------- Flow Detection Engine ---------------- */
// Computes end-to-end flows from graph.json — no engine changes needed.
// A flow is a chain: origin (REST or external event) → [publishes → channel → consumer → publishes → ...]
// Each step is { repo, entryId, method, type, channel, isExternal, publishesTo: [channelNames], next: [stepRefs] }

const PUBLISH_KEYWORDS = ["convertandsend", "send", "publish", "emit"];

// Human-readable origin descriptors for non-REST flow origins.
// Keyed by the entry-point type string the engine serializes (EntryPointType).
// The tag/label colors reuse TYPE_META so flow cards match the rest of the UI.
const ORIGIN_KINDS = {
  "scheduled-task":   { tag: "SCHEDULED", cls: "scheduled", noun: "scheduled job" },
  "event-listener":   { tag: "EVENT",     cls: "event",     noun: "event listener" },
  websocket:          { tag: "WEBSOCKET", cls: "websocket", noun: "websocket" },
  "kafka-consumer":   { tag: "KAFKA",     cls: "kafka",     noun: "Kafka topic" },
  "rabbitmq-consumer":{ tag: "RABBITMQ",  cls: "rabbitmq",  noun: "RabbitMQ queue" },
  "jms-consumer":     { tag: "JMS",       cls: "jms",       noun: "JMS queue" },
  "sqs-consumer":     { tag: "SQS",       cls: "sqs",       noun: "SQS queue" },
  // Extra framework origins.
  main:               { tag: "MAIN",      cls: "main",      noun: "application bootstrap" },
  lifecycle:          { tag: "LIFECYCLE", cls: "lifecycle", noun: "lifecycle hook" },
  servlet:            { tag: "SERVLET",   cls: "servlet",   noun: "servlet endpoint" },
  "soap-service":     { tag: "SOAP",      cls: "soap",      noun: "SOAP operation" },
  graphql:            { tag: "GRAPHQL",   cls: "graphql",   noun: "GraphQL resolver" },
  "grpc-service":     { tag: "GRPC",      cls: "grpc",      noun: "gRPC service method" },
  "cloud-function":   { tag: "FUNCTION",  cls: "function",  noun: "cloud function" },
};

// Describe a flow's origin: rest vs. a specific external trigger type.
// Falls back to a generic "EXTERNAL" for unknown/uncategorized types.
function originDescriptor(entry, isRest) {
  if (isRest) return { kind: "rest", tag: "REST", cls: "rest", noun: "REST endpoint" };
  const meta = ORIGIN_KINDS[entry.type] || { tag: "EXTERNAL", cls: "external", noun: "external event" };
  return { kind: entry.type || "external", tag: meta.tag, cls: meta.cls, noun: meta.noun };
}

function detectFlows(graph) {
  const entries = graph.entry_points || [];
  const links = graph.cross_repo_links || [];

  // Index entry points by id
  const entryById = {};
  entries.forEach((e) => { entryById[e.id] = e; });

  // Index producers by link kind: message/broker vs sync HTTP. Producer id
  // format: "repo:ClassName.method:publishMethod".
  const msgProducersByRepo = {};  // repo -> [{ channel, producerId, verb }]
  const httpProducersByRepo = {}; // repo -> [{ channel, producerId, verb }]
  links.forEach((link) => {
    const isHttp = link.kind === "http";
    const bucket = isHttp ? httpProducersByRepo : msgProducersByRepo;
    (link.producers || []).forEach((prodId) => {
      const repo = repoFromId(prodId);
      (bucket[repo] = bucket[repo] || []).push({
        channel: link.channel,
        producerId: prodId,
        verb: isHttp ? (link.verb || "") : "",
      });
    });
  });

  // Index consumers by channel. Broker/event entries consume message channels;
  // REST endpoints consume HTTP paths — kept in SEPARATE maps so a topic named
  // like a path can never collide with it.
  const msgConsumersByChannel = {};  // channel -> [entryId]
  const restConsumersByChannel = {}; // path -> [entryId]
  entries.forEach((e) => {
    const ch = e.channel || "";
    if (!ch) return;
    const bucket = e.type === "rest-endpoint" ? restConsumersByChannel : msgConsumersByChannel;
    (bucket[ch] = bucket[ch] || []).push(e.id);
  });

  // Index: which channels are produced internally (so we can identify external origins)
  const internalChannels = new Set(links.map((l) => l.channel));

  // Which channels an entry's call tree reaches, from one producer bucket
  // (message producers or http-call producers).
  function publishesFrom(entryPoint, bucket) {
    const channels = new Set();
    const repo = entryPoint.repo;
    const repoProds = bucket[repo] || [];

    // Collect every class.method reachable from the entry (root + call tree),
    // so producers invoked through beans/services chain up too.
    const reachableMethods = new Set();
    const rootMethod = [entryPoint.class_name, entryPoint.method].filter(Boolean).join(".");
    if (rootMethod) reachableMethods.add(rootMethod);
    const tree = entryPoint.call_tree;
    if (tree && typeof tree === "object") {
      const stack = [tree];
      while (stack.length) {
        const node = stack.pop();
        if (!node) continue;
        if (typeof node.method === "string" && node.method) {
          // Resolved nodes name the class; unresolved use receiver.method — keep both forms.
          reachableMethods.add(node.method);
          const mName = node.method.split(".").pop();
          if (node.class_name && mName) reachableMethods.add(node.class_name + "." + mName);
        }
        if (Array.isArray(node.children)) stack.push(...node.children);
      }
    }

    repoProds.forEach((rp) => {
      // "ClassName.method" part of the producer id
      const matchTarget = rp.producerId.split(":")[1] || "";
      // Match the producer method exactly, or any call-tree node that
      // resolves to that class.method (walked transitively by the engine).
      if (reachableMethods.has(matchTarget)) {
        channels.add(rp.channel);
      }
    });
    return Array.from(channels);
  }

  // Build flow steps recursively
  function buildSteps(entry, visited) {
    const entryId = entry.id;
    if (visited.has(entryId)) return null; // cycle guard
    const nextVisited = new Set(visited);
    nextVisited.add(entryId);

    const consumers = []; // [{ channel, kind, verb, entryId }]

    // Message hops: broker/event consumers of the published channels
    publishesFrom(entry, msgProducersByRepo).forEach((ch) => {
      const consumerIds = msgConsumersByChannel[ch] || [];
      consumerIds.forEach((cid) => {
        if (cid === entryId) return;
        const ce = entryById[cid];
        if (!ce) return;
        // Don't recurse into same repo (intra-repo calls)
        if (ce.repo === entry.repo) return;
        consumers.push({ channel: ch, kind: "message", verb: "", entryId: cid });
      });
    });

    // Sync HTTP hops: REST endpoints as targets of http-call producers
    publishesFrom(entry, httpProducersByRepo).forEach((ch) => {
      const consumerIds = restConsumersByChannel[ch] || [];
      consumerIds.forEach((cid) => {
        if (cid === entryId) return;
        const ce = entryById[cid];
        if (!ce) return;
        if (ce.repo === entry.repo) return;
        const prod = (httpProducersByRepo[entry.repo] || []).find((p) => p.channel === ch);
        // The producer's response_type names what comes back — sync calls are
        // round-trips, and the label should say so.
        let responseType = "";
        if (prod) {
          const pobj = (graph.producers || []).find((p) => p.id === prod.producerId);
          responseType = (pobj && pobj.response_type) || "";
        }
        consumers.push({ channel: ch, kind: "http", verb: (prod && prod.verb) || "", responseType, entryId: cid });
      });
    });

    // Recursively build child steps
    const children = consumers.map((c) => {
      const childEntry = entryById[c.entryId];
      const childStep = buildSteps(childEntry, nextVisited);
      if (!childStep) return null;
      return { channel: c.channel, kind: c.kind, verb: c.verb, responseType: c.responseType || "", step: childStep };
    }).filter(Boolean);

    return {
      repo: entry.repo,
      entryId: entry.id,
      method: entry.method || entry.id.split(":").pop(),
      type: entry.type,
      channel: entry.channel || "",
      publishesTo: Array.from(new Set([
        ...publishesFrom(entry, msgProducersByRepo),
        ...publishesFrom(entry, httpProducersByRepo),
      ])),
      children, // [{ channel, kind, verb, step }]
    };
  }

  // Collect all repos in a step tree
  function reposInStep(step, set) {
    set.add(step.repo);
    step.children.forEach((c) => reposInStep(c.step, set));
  }

  // Count max depth of a step tree
  function stepDepth(step) {
    if (step.children.length === 0) return 1;
    return 1 + Math.max(...step.children.map((c) => stepDepth(c.step)));
  }

  // Does the flow contain any sync HTTP hop?
  function hasSyncHop(step) {
    if (!step) return false;
    return step.children.some((c) => c.kind === "http" || hasSyncHop(c.step));
  }

  // Find origins: REST endpoints + external event channels
  const seenOrigins = new Set();
  const flows = [];

  entries.forEach((entry) => {
    const isRest = entry.type === "rest-endpoint";
    const isExternal = !isRest && !internalChannels.has(entry.channel || "");

    // Skip internal consumers that have a producer in another repo — they're mid-flow, not origins
    if (!isRest && !isExternal) return;

    // Deduplicate by entry id (each entry point is one origin)
    if (seenOrigins.has(entry.id)) return;
    seenOrigins.add(entry.id);

    const step = buildSteps(entry, new Set());
    const repos = new Set();
    reposInStep(step, repos);
    const depth = stepDepth(step);
    const hasCrossRepo = repos.size > 1;

    // Generate flow name + a human-readable origin descriptor.
    // originKind = the engine entry type ("scheduled-task", "kafka-consumer", ...)
    // so external origins aren't all lumped under a vague "EXTERNAL".
    const desc = originDescriptor(entry, isRest);
    let name, originLabel;
    if (isRest) {
      name = entry.method || entry.id.split(":").pop();
      // Convert camelCase to Title Case
      name = name.replace(/([A-Z])/g, " $1").replace(/^./, (s) => s.toUpperCase()).trim();
      originLabel = ((entry.method_type || "POST") + " ") + (entry.channel || "");
    } else if (desc.kind === "scheduled-task") {
      // Cron / fixed-rate / EJB-timer jobs: name by the method, label the trigger
      // explicitly. Only prefix "cron " when the channel is a real cron expression
      // (5+ whitespace-separated fields) — EJB @Schedule channels aren't cron.
      name = entry.method || entry.id.split(":").pop();
      name = name.replace(/([A-Z])/g, " $1").replace(/^./, (s) => s.toUpperCase()).trim();
      const schedCh = entry.channel || "";
      const cronLike = /\s/.test(schedCh) && schedCh.split(/\s+/).length >= 5;
      originLabel = cronLike ? "cron " + schedCh
        : (/^\d+$/.test(schedCh) ? "every " + schedCh + " ms"
          : (schedCh.indexOf("@Schedule") === 0 ? "EJB timer" : (schedCh || "scheduled")));
    } else {
      name = entry.channel || entry.method || "External Event";
      originLabel = entry.channel || "";
    }

    flows.push({
      id: "flow:" + entry.id,
      name,
      originLabel,
      originType: isRest ? "rest" : "external",
      originKind: desc.kind,
      originTag: desc.tag,
      originClass: desc.cls,
      originNoun: desc.noun,
      originChannel: entry.channel || "",
      originMethodType: entry.method_type || "",
      step,
      repos: Array.from(repos),
      repoCount: repos.size,
      hopCount: depth - 1,
      hasCrossRepo,
      hasSync: hasSyncHop(step),
    });
  });

  return flows;
}

/* ---------------- Orphan / cycle detection ---------------- */
// Mirror of engine/graph_tools.py find_orphans & find_cycles — keep in sync.
// Both derive the same facts from the same in-memory graph, so the galaxy ring
// + Gaps view (client-side) and the REST/AI tool-loop (engine) read the same
// data. Message channels only; http-call producers are excluded on both sides.
const MSG_CONSUMER_TYPES = new Set([
  "kafka-consumer", "rabbitmq-consumer", "jms-consumer", "sqs-consumer", "event-listener",
]);
const CHANNEL_SENTINELS = new Set(["", "unknown", "unknown-event"]);
const cleanChannel = (ch) => (typeof ch === "string" ? ch.trim() : "");
const isValidChannel = (ch) => !!ch && !CHANNEL_SENTINELS.has(ch);

function detectOrphans(graph) {
  const eps = graph.entry_points || [];
  const producers = graph.producers || [];

  const consumed = new Set();
  for (const ep of eps) {
    if (!MSG_CONSUMER_TYPES.has(ep.type)) continue;
    const ch = cleanChannel(ep.channel);
    if (isValidChannel(ch)) consumed.add(ch);
  }

  const produced = new Set();
  const orphan_producers = [];
  for (const p of producers) {
    if (p.type === "http-call") continue;
    const ch = cleanChannel(p.channel);
    if (!isValidChannel(ch)) continue;
    produced.add(ch);
    if (!consumed.has(ch)) {
      orphan_producers.push({
        id: p.id || "", repo: p.repo || repoFromId(p.id), channel: ch,
        method: p.method || "", type: p.type || "", file: p.file || "", line: p.line || 0,
      });
    }
  }

  const orphan_consumers = [];
  for (const ep of eps) {
    if (!MSG_CONSUMER_TYPES.has(ep.type)) continue;
    const ch = cleanChannel(ep.channel);
    if (!isValidChannel(ch)) continue;
    if (!produced.has(ch)) {
      orphan_consumers.push({
        id: ep.id || "", repo: ep.repo || "", channel: ch,
        method: ep.method || "", type: ep.type || "", file: ep.file || "", line: ep.line || 0,
      });
    }
  }

  const orphan_channels = Array.from(new Set([
    ...orphan_producers.map((p) => p.channel),
    ...orphan_consumers.map((c) => c.channel),
  ])).sort();

  return {
    orphan_producers, orphan_consumers,
    summary: {
      orphan_producers: orphan_producers.length,
      orphan_consumers: orphan_consumers.length,
      orphan_channels,
    },
  };
}

function detectCycles(graph) {
  const links = graph.cross_repo_links || [];
  // adj: Map<repo, Map<repo, Set<channel>>>
  const adj = new Map();
  const nbrsOf = (a) => { if (!adj.has(a)) adj.set(a, new Map()); return adj.get(a); };
  for (const link of links) {
    const chans = [link.channel || ""];
    const prodRepos = Array.from(new Set((link.producers || []).map(repoFromId)));
    const consRepos = Array.from(new Set((link.consumers || []).map(repoFromId)));
    for (const pr of prodRepos) {
      for (const cr of consRepos) {
        if (pr === cr) continue; // self-loop is not a cross-repo cycle
        const nbrs = nbrsOf(pr);
        if (!nbrs.has(cr)) nbrs.set(cr, new Set());
        for (const ch of chans) nbrs.get(cr).add(ch);
      }
    }
  }

  const nodeSet = new Set(adj.keys());
  for (const nbrs of adj.values()) for (const n of nbrs.keys()) nodeSet.add(n);
  const nodes = Array.from(nodeSet).sort();
  const index = new Map(nodes.map((r, i) => [r, i]));

  const cycles = [];
  const seen = new Set();

  const channelsAlong = (path) => {
    const s = new Set();
    for (let i = 0; i < path.length - 1; i++) {
      const nbrs = adj.get(path[i]);
      if (nbrs && nbrs.has(path[i + 1])) for (const ch of nbrs.get(path[i + 1])) s.add(ch);
    }
    return Array.from(s).sort();
  };

  // DFS anchored at each node's lowest sorted index — each elementary cycle
  // surfaces exactly once (from its lowest-indexed member).
  const dfs = (start, cur, path, onPath) => {
    const nbrs = adj.get(cur);
    if (!nbrs) return;
    for (const nxt of nbrs.keys()) {
      if (index.get(nxt) < index.get(start)) continue;
      if (nxt === start) {
        const closed = [...path, start];
        const sig = closed.join(">");
        if (!seen.has(sig)) {
          seen.add(sig);
          cycles.push({ repos: closed, length: closed.length - 1, channels: channelsAlong(closed) });
        }
      } else if (!onPath.has(nxt)) {
        onPath.add(nxt);
        dfs(start, nxt, [...path, nxt], onPath);
        onPath.delete(nxt);
      }
    }
  };

  for (const start of nodes) dfs(start, start, [start], new Set([start]));

  const repos_in_cycles = Array.from(new Set(cycles.flatMap((c) => c.repos))).sort();
  return { cycles, summary: { cycle_count: cycles.length, repos_in_cycles } };
}

/* ---------------- Dead-code detection ---------------- */
// Mirror of engine/graph_tools.py find_dead_code — keep in sync. Unreachable
// methods come from engine-precomputed graph fields (full call-graph
// reachability); thin handlers + isolated repos are derived here.
function detectDeadCode(graph) {
  const unreachable = graph.unreachable_methods || [];
  const methodsTotal = graph.methods_total || 0;

  const thin_handlers = (graph.entry_points || [])
    .filter((ep) => {
      const m = ep.metrics || {};
      const t = m.thin;
      // "thin" is engine-computed (genuine no-op); fall back to node count for old graphs.
      return t === undefined ? (m.total_nodes || 0) <= 1 : !!t;
    })
    .map((ep) => ({
      id: ep.id || "", repo: ep.repo || "", type: ep.type || "",
      channel: ep.channel || "", method: ep.method || "",
      file: ep.file || "", line: ep.line || 0,
    }));

  const linked = new Set();
  (graph.cross_repo_links || []).forEach((l) => {
    (l.producers || []).forEach((id) => linked.add(repoFromId(id)));
    (l.consumers || []).forEach((id) => linked.add(repoFromId(id)));
  });
  const isolated_repos = (graph.repos || []).filter((r) => !linked.has(r));

  return {
    unreachable_methods: unreachable,
    thin_handlers,
    isolated_repos,
    methods_total: methodsTotal,
    method_index_available: "unreachable_methods" in graph,
  };
}

/* ---------------- Galaxy View ---------------- */
function GalaxyView({ graph, dims, onSelectRepo, onOpenGaps }) {
  const repos = graph.repos || [];
  const entryPoints = graph.entry_points || [];
  const links = graph.cross_repo_links || [];
  const pz = usePanZoom(".repo-wrap, .legend, .filter-chip");

  // Hovered direction edge → bundled message details shown in a popup
  const [hoverEdge, setHoverEdge] = useState(null); // { items, from, to, mid:{x,y} }
  // Hovered repo with orphans → "why" popup
  const [hoverRepo, setHoverRepo] = useState(null); // { repo, x, y, prod:[], cons:[] }

  // Repos that own ≥1 orphan producer/consumer — drives the amber at-a-glance ring.
  const orphanByRepo = useMemo(() => {
    const o = detectOrphans(graph);
    const m = {};
    const add = (repo, kind, channel) => {
      if (!m[repo]) m[repo] = { prod: [], cons: [] };
      if (!m[repo][kind].includes(channel)) m[repo][kind].push(channel);
    };
    o.orphan_producers.forEach((p) => add(p.repo, "prod", p.channel));
    o.orphan_consumers.forEach((c) => add(c.repo, "cons", c.channel));
    return m;
  }, [graph]);
  const gapCount = Object.keys(orphanByRepo).length;

  const epCount = useMemo(() => {
    const m = {};
    entryPoints.forEach((ep) => { m[ep.repo] = (m[ep.repo] || 0) + 1; });
    return m;
  }, [graph]);

  // Which entry point types does each repo use?
  const repoTypes = useMemo(() => {
    const m = {};
    entryPoints.forEach((ep) => {
      if (!m[ep.repo]) m[ep.repo] = [];
      if (!m[ep.repo].includes(ep.type)) m[ep.repo].push(ep.type);
    });
    return m;
  }, [graph]);

  // Which entry point types does the whole project use (drives the legend)?
  const usedTypes = useMemo(() => {
    const s = new Set();
    entryPoints.forEach((ep) => s.add(ep.type));
    return Array.from(s);
  }, [graph]);

  const W = dims.w;
  const H = dims.h;
  const cx = W / 2, cy = H / 2;
  const radius = Math.max(120, Math.min(W, H) * 0.34);

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
    // eslint-disable-next-line
  }, [graph, W, H]);

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
    <div className="galaxy">
      <div className="view-top">
        <div className="view-hint">
          {repos.length} repos · {entryPoints.length} entry points · {(graph.producers || []).length} producers
        </div>
        {gapCount > 0 && (
          <button className="gaps-pill" onClick={onOpenGaps} title="Unconnected channels & dependency cycles">
            <span className="gaps-pill-dot" aria-hidden="true">i</span>
            {gapCount} repo{gapCount === 1 ? "" : "s"} with gaps
          </button>
        )}
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
          const types = repoTypes[p.name] || [];
          const orbitR = p.r + 18;
          const gaps = orphanByRepo[p.name]; // {prod:[], cons:[]} | undefined
          return (
            <div
              className="repo-wrap"
              key={p.name}
              style={{ left: p.x, top: p.y }}
              onClick={(e) => onSelectRepo(p.name, e)}
              onMouseEnter={() => gaps && setHoverRepo({ repo: p.name, x: p.x, y: p.y, prod: gaps.prod, cons: gaps.cons })}
              onMouseLeave={() => setHoverRepo(null)}
            >
              <button className={"repo-node" + (gaps ? " orphan" : "")} style={{ width: p.r * 2, height: p.r * 2 }}>
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
              <div className="repo-label" style={{ top: p.r + 26 }}>{p.name}</div>
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
        {hoverRepo && (() => {
          const px = pz.viewport.x + hoverRepo.x * pz.viewport.zoom;
          const py = Math.max(150, pz.viewport.y + hoverRepo.y * pz.viewport.zoom);
          return (
            <div className="edge-popup repo-orphan-popup" style={{ left: px, top: py }}>
              <div className="edge-popup-title">
                <span className="mono">{hoverRepo.repo}</span>
                <span className="repo-orphan-tag">gaps</span>
              </div>
              {hoverRepo.prod.length > 0 && (
                <div className="repo-orphan-line">
                  <span className="repo-orphan-kind prod">produces, no consumer</span>
                  <span className="mono repo-orphan-chans">{hoverRepo.prod.join(", ")}</span>
                </div>
              )}
              {hoverRepo.cons.length > 0 && (
                <div className="repo-orphan-line">
                  <span className="repo-orphan-kind cons">consumes, no producer</span>
                  <span className="mono repo-orphan-chans">{hoverRepo.cons.join(", ")}</span>
                </div>
              )}
            </div>
          );
        })()}
      </div>
      <Legend types={usedTypes} />
      {pz.zoomControls}
    </div>
  );
}

/* ---------------- Gaps / Orphans View ---------------- */
// Lists orphan producers, orphan consumers, and repo dependency cycles.
// Data comes from detectOrphans/detectCycles (client-side mirrors of the
// engine tools); entries link into the existing Path view (consumers) or a
// source viewer (producers).
/* ---------------- Info tip (hover/focus explanation) ---------------- */
// Small ⓘ next to a heading; reveals a styled bubble on hover or keyboard focus.
function InfoTip({ text }) {
  // Stop propagation so clicking the tip inside a collapsible header doesn't
  // also toggle the section.
  return (
    <span className="info-tip" onClick={(e) => e.stopPropagation()}>
      <span className="info-tip-icon" tabIndex={0} role="button" aria-label="What this means">i</span>
      <span className="info-tip-bubble">{text}</span>
    </span>
  );
}

/* ---------------- Collapsible section (Gaps + Dead-code) ---------------- */
function CollapsibleSection({ title, count, help, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  const toggle = () => setOpen((o) => !o);
  return (
    <section className="gaps-section">
      <header
        className="gaps-section-head"
        onClick={toggle}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
        }}
      >
        <span className="gaps-section-title">
          <span className={"gaps-chevron" + (open ? " open" : "")} aria-hidden="true">▸</span>
          {title}{help && <InfoTip text={help} />}
        </span>
        <span className="gaps-section-count">{count}</span>
      </header>
      {open && <div className="gaps-section-body">{children}</div>}
    </section>
  );
}

function GapsView({ graph, onOpenEntry, onOpenSource }) {
  const orphans = useMemo(() => detectOrphans(graph), [graph]);
  const cyc = useMemo(() => detectCycles(graph), [graph]);
  const totalGaps = orphans.summary.orphan_producers + orphans.summary.orphan_consumers;

  const section = (title, count, help, body) => (
    <CollapsibleSection key={title} title={title} count={count} help={help}>{body}</CollapsibleSection>
  );
  const empty = (msg) => <div className="gaps-empty">{msg}</div>;

  return (
    <div className="gaps">
      <div className="view-top">
        <div className="view-hint">
          {totalGaps} unconnected channel{totalGaps === 1 ? "" : "s"} ·{" "}
          {cyc.summary.cycle_count} cycle{cyc.summary.cycle_count === 1 ? "" : "s"}
        </div>
      </div>
      <div className="gaps-scroll">
        {section("Orphan producers", orphans.summary.orphan_producers,
          "Emits to a message channel no consumer in this project listens on — possibly a dead contract, a misnamed queue/topic, or a service not yet added.",
          orphans.orphan_producers.length === 0
            ? empty("Every message producer has a consumer.")
            : orphans.orphan_producers.map((p) => (
              <button className="gaps-card" key={p.id} onClick={() => onOpenSource(p.file, p.line)}>
                <div className="gaps-card-top">
                  <span className="gaps-channel mono">{p.channel}</span>
                  <span className="gaps-repo">{p.repo}</span>
                </div>
                <div className="gaps-card-sub mono">
                  {p.method}{p.file ? " · " + fmtFile(p.file) + (p.line ? ":" + p.line : "") : ""}
                </div>
                <span className="gaps-card-tag prod">no consumer</span>
              </button>
            ))
        )}

        {section("Orphan consumers", orphans.summary.orphan_consumers,
          "Listens on a message channel no producer in this project emits to — possibly a dead listener or a typo in the queue/topic name.",
          orphans.orphan_consumers.length === 0
            ? empty("Every message consumer has a producer.")
            : orphans.orphan_consumers.map((c) => (
              <button className="gaps-card" key={c.id} onClick={() => onOpenEntry(c.id)}>
                <div className="gaps-card-top">
                  <span className="gaps-channel mono">{c.channel}</span>
                  <span className="gaps-repo">{c.repo}</span>
                </div>
                <div className="gaps-card-sub mono">
                  {c.method}{c.file ? " · " + fmtFile(c.file) + (c.line ? ":" + c.line : "") : ""}
                </div>
                <span className="gaps-card-tag cons">no producer</span>
              </button>
            ))
        )}

        {section("Dependency cycles", cyc.summary.cycle_count,
          "Circular repo dependencies through channel edges (e.g. A → B → A) — an architectural smell where services can't be deployed or changed independently.",
          cyc.cycles.length === 0
            ? empty("No circular repo dependencies.")
            : cyc.cycles.map((cy, i) => (
              <div className="gaps-card cycle" key={i}>
                <div className="gaps-cycle-chain mono">{cy.repos.join(" → ")}</div>
                <div className="gaps-cycle-chans mono">{cy.channels.join(", ")}</div>
              </div>
            ))
        )}
      </div>
    </div>
  );
}

/* ---------------- Source modal (reuses the /source endpoint) ---------------- */
function SourceModal({ pid, file, line, onClose }) {
  const [source, setSource] = useState(null);
  const [status, setStatus] = useState("loading");
  const [err, setErr] = useState("");
  const hiRef = useRef(null);

  useEffect(() => {
    let alive = true;
    setStatus("loading");
    setSource(null);
    if (!file) { if (alive) setStatus("none"); return () => { alive = false; }; }
    fetchJSON(projPath(pid, "/source?file_path=" + encodeURIComponent(file)))
      .then((s) => { if (alive) { setSource(s); setStatus("ready"); } })
      .catch((e) => { if (alive) { setErr(e.message); setStatus("error"); } });
    return () => { alive = false; };
  }, [pid, file]);

  useEffect(() => {
    if (status === "ready" && hiRef.current) {
      hiRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [status]);

  // Same context-window rendering DetailPanel uses for source.
  const lines = source && source.content ? source.content.split("\n") : [];
  const target = line || 0;
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

  return (
    <div className="source-modal-overlay" onClick={onClose}>
      <div className="source-modal glass" onClick={(e) => e.stopPropagation()}>
        <header className="dp-head">
          <div>
            <div className="dp-title mono">{fmtFile(file)}</div>
            <div className="dp-loc mono">{file}{line ? ":" + line : ""}</div>
          </div>
          <button className="dp-close" onClick={onClose}>✕</button>
        </header>
        <div className="dp-body">
          <div className="src-wrap">
            <div className="src-head mono">
              {source && source.line_count ? source.line_count + " lines" : ""}
              {truncated ? " · showing context window" : ""}
            </div>
            {status === "loading" && <div className="src-loading">Loading source…</div>}
            {status === "error" && <div className="src-error">Could not load source: {err}</div>}
            {status === "none" && <div className="src-error">No source file recorded for this producer.</div>}
            {status === "ready" && (
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
      </div>
    </div>
  );
}

/* ---------------- Dead Code View ---------------- */
// A top-level mode (next to Topology / Flows). Lists unreachable methods,
// thin handlers, and isolated repos. Reuses the Gaps-view aesthetic.
/* ---------------- Broken satellites (dead-code background motif) ---------------- */
// Dead stars + debris drifting through the dead-code view — the metaphor for
// abandoned code in a constellation. Monochrome star glyphs (not emoji) so CSS
// color + glow apply fully and they read as broken/dying. Purely decorative.
const SAT_GLYPHS = ["✦", "✧", "🛰️", "✷", "✸", "✹", "✺", "⛓️‍💥", "◆", "◇", "🛑", "✱"];
// Distress reds/oranges (dying stars) dominate, with a couple cold tones (dead dwarfs) for variety.
const SAT_COLORS = ["#f87171", "#ef4444", "#fb923c", "#f5a524", "#d80202", "#682201"];
function BrokenSatellites({ count = 18 }) {
  const sats = useMemo(
    () => Array.from({ length: count }, () => ({
      left: Math.random() * 100,
      top: Math.random() * 96,
      delay: Math.random() * 20,
      flick: 9 + Math.random() * 12,           // slower twinkle — each flash lingers
      drift: 12 + Math.random() * 30,
      size: 8 + Math.random() * 22,
      rot: -40 + Math.random() * 80,           // base rotation (deg)
      dx: -30 + Math.random() * 60,            // drift delta x (px)
      dy: -22 + Math.random() * 40,            // drift delta y (px)
      peak: 0.35 + Math.random() * 0.45,       // per-debris brightness peak
      glyph: SAT_GLYPHS[Math.floor(Math.random() * SAT_GLYPHS.length)],
      color: SAT_COLORS[Math.floor(Math.random() * SAT_COLORS.length)],
    })),
    [count]
  );
  return (
    <div className="broken-sats" aria-hidden="true">
      {sats.map((s, i) => (
        <span
          className="broken-sat"
          key={i}
          style={{
            left: s.left + "%", top: s.top + "%", fontSize: s.size + "px",
            animationDelay: s.delay + "s",
            animationDuration: s.flick + "s, " + s.drift + "s",
            "--rot": s.rot + "deg",
            "--dx": s.dx + "px",
            "--dy": s.dy + "px",
            "--peak": s.peak,
            "--c": s.color,
          }}
        >
          {s.glyph}
        </span>
      ))}
    </div>
  );
}

function DeadCodeView({ graph, onOpenEntry, onOpenSource }) {
  const dc = useMemo(() => detectDeadCode(graph), [graph]);
  const reachable = Math.max(0, dc.methods_total - dc.unreachable_methods.length);

  const section = (title, count, help, body) => (
    <CollapsibleSection key={title} title={title} count={count} help={help}>{body}</CollapsibleSection>
  );
  const empty = (msg) => <div className="gaps-empty">{msg}</div>;

  return (
    <div className="gaps dead">
      <div className="dead-bg" aria-hidden="true" />
      <BrokenSatellites />
      <div className="view-top">
        <div className="view-hint">
          {dc.method_index_available
            ? dc.unreachable_methods.length + " unreachable of " + dc.methods_total +
              " methods · " + reachable + " reachable"
            : "thin handlers & isolated repos"}
          {" · " + dc.thin_handlers.length + " thin handler" + (dc.thin_handlers.length === 1 ? "" : "s")}
        </div>
      </div>
      <div className="gaps-scroll">
        {!dc.method_index_available && (
          <div className="gaps-empty dead-note">
            Unreachable-method detection needs a graph from a recent engine run
            (with method indexing). Regenerate this project's graph to enable it —
            thin handlers and isolated repos are still shown below.
          </div>
        )}

        {section("Unreachable methods", dc.unreachable_methods.length,
          "Methods defined in the codebase that no entry point can reach through resolved calls. They may be genuinely dead — or only invoked via reflection, DI, or framework wiring the static analyzer can't see. Treat as candidates, not certainties.",
          dc.unreachable_methods.length === 0
            ? empty(dc.method_index_available ? "Every method is reachable from an entry point." : "—")
            : dc.unreachable_methods.map((m) => (
              <button className="gaps-card" key={m.id}
                onClick={() => onOpenSource(m.file, m.line)}>
                <div className="gaps-card-top">
                  <span className="gaps-channel mono dead-channel">{m.class_name}.{m.method}</span>
                  <span className="gaps-repo">{m.repo}</span>
                </div>
                <div className="gaps-card-sub mono">
                  {m.file ? fmtFile(m.file) + (m.line ? ":" + m.line : "") : "no location"}
                </div>
                <span className="gaps-card-tag prod">unreachable</span>
              </button>
            ))
        )}

        {section("Thin handlers", dc.thin_handlers.length,
          "Entry points whose call tree resolved no calls at all — e.g. a REST endpoint or listener that returns without invoking anything, or whose targets couldn't be resolved. Often a stub, a trivial passthrough, or wiring the analyzer missed.",
          dc.thin_handlers.length === 0
            ? empty("No entry point has an empty call tree.")
            : dc.thin_handlers.map((h) => (
              <button className="gaps-card" key={h.id}
                onClick={() => onOpenEntry(h.id)}>
                <div className="gaps-card-top">
                  <span className="gaps-channel mono dead-channel">{h.method}</span>
                  <span className="gaps-repo">{h.repo}</span>
                </div>
                <div className="gaps-card-sub mono">
                  {h.type}{h.channel ? " · " + h.channel : ""}{h.file ? " · " + fmtFile(h.file) : ""}
                </div>
                <span className="gaps-card-tag cons">no resolved calls</span>
              </button>
            ))
        )}

        {section("Isolated repos", dc.isolated_repos.length,
          "Repositories with no cross-repo message or HTTP links to any other repo in this project — fully disconnected from the constellation.",
          dc.isolated_repos.length === 0
            ? empty("Every repo has at least one cross-repo link.")
            : dc.isolated_repos.map((r) => (
              <div className="gaps-card cycle" key={r}>
                <div className="gaps-cycle-chain mono">{r}</div>
                <div className="gaps-cycle-chans">no cross-repo links</div>
              </div>
            ))
        )}
      </div>
    </div>
  );
}

/* ---------------- Solar System View ---------------- */
function SolarSystemView({ graph, repo, dims, onSelectEntry, flows, onOpenFlow }) {
  const eps = useMemo(
    () => (graph.entry_points || []).filter((e) => e.repo === repo),
    [graph, repo]
  );
  const channels = useMemo(() => {
    const list = buildRepoChannels(repo, graph);
    list.forEach((c) => {
      c.inMethods.forEach((o) => { o.flows = flowsForUnit(flows, graph, repo, c, o, "in"); });
      c.outMethods.forEach((o) => { o.flows = flowsForUnit(flows, graph, repo, c, o, "out"); });
    });
    return list;
  }, [repo, graph, flows]);
  const [hidden, setHidden] = useState({});
  const pz = usePanZoom(".star, .star-label, .channels-panel");

  // The channels panel is docked to the right edge (340px + 12px gap); the
  // star field lays out in the remaining width so nothing hides under it.
  const PANEL_W = 352;
  const W = dims.w - PANEL_W, H = dims.h;
  const cx = W / 2, cy = H / 2;
  const typesPresent = Array.from(new Set(eps.map((e) => e.type)));

  const stars = useMemo(() => {
    const maxNodes = Math.max(1, ...eps.map((e) => (e.metrics && e.metrics.total_nodes) || 1));
    return eps.map((ep, i) => {
      const angle = i * 2.39996323;
      const rr = Math.sqrt(i + 0.6) * Math.min(W, H) * 0.085;
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
  }, [eps, W, H]);

  const visible = stars.filter((s) => !hidden[s.ep.type]);

  return (
    <div className="solar">
      <div className="view-top">
        <div className="view-hint">{eps.length} entry points · {channels.length} channels</div>
      </div>

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
        ref={pz.containerRef}
        style={{ right: PANEL_W }}
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
          <button
            key={s.ep.id}
            className="star"
            style={{ left: s.x, top: s.y, width: s.size, height: s.size, "--c": s.color, "--glow": s.glow }}
            title={s.ep.id}
            onClick={(e) => onSelectEntry(s.ep.id, e)}
          >
            <span className="star-core" style={{ width: s.size, height: s.size }}></span>
          </button>
        ))}
        {visible.map((s) => (
          <div key={"l" + s.ep.id} className="star-label" style={{ left: s.x, top: s.y + s.size / 2 + 8 }}>
            <span className="star-label-name">{s.ep.method || s.ep.id.split(":").pop()}</span>
          </div>
        ))}
        </div>
      </div>

      <ChannelsPanel channels={channels} repo={repo} onOpenFlow={onOpenFlow} />
      {pz.zoomControls}
    </div>
  );
}

/* ---------------- Channels panel (solar view) ---------------- */
// Entry-point types that CONSUME from a message channel (vs expose an endpoint
// like REST). Their `channel` field is the topic/queue they listen on.
const CONSUMER_TYPES = new Set([
  "kafka-consumer", "rabbitmq-consumer", "jms-consumer", "sqs-consumer",
  "event-listener", "websocket",
]);
// Producer types that PUBLISH to a message channel (vs a sync HTTP call).
const PRODUCER_TYPES = new Set([
  "rabbitmq-producer", "kafka-producer", "jms-producer", "event-publisher",
  "pulsar-producer", "nats-producer",
]);

// Channel wiring for ONE repo: every channel it consumes (IN), emits (OUT), or
// both (BOTH), plus its sync HTTP calls. Peers come from cross_repo_links
// (producers → channel → consumers). One card per channel — a channel this repo
// both consumes and emits appears ONCE as BOTH, never twice.
function buildRepoChannels(repo, graph) {
  const eps = (graph.entry_points || []).filter((e) => e.repo === repo);
  const prods = (graph.producers || []).filter((p) => p.repo === repo);
  const links = graph.cross_repo_links || [];
  const byKey = new Map(); // key "kind|channel" -> card

  const card = (key, kind) => {
    let c = byKey.get(key);
    if (!c) {
      c = {
        kind,                     // "msg" | "http"
        channel: key.slice(key.indexOf("|") + 1),
        direction: "in",          // in | out | both
        verb: "",                 // http method (GET/POST/...)
        inMethods: [],            // {m: consumer method, t: message payload type}
        outMethods: [],           // {m: producer method, t: payload type} (http = t:"")
        inPeers: [],              // repos producing INTO this channel
        outPeers: [],             // repos consuming FROM this channel
      };
      byKey.set(key, c);
    }
    return c;
  };

  // Repo names on the other side of links for a channel, excluding this repo.
  const peerRepos = (channel, side) => {
    const repos = new Set();
    links.forEach((l) => {
      if (l.channel !== channel) return;
      (l[side] || []).forEach((id) => {
        const r = repoFromId(id);
        if (r && r !== repo) repos.add(r);
      });
    });
    return Array.from(repos).sort();
  };

  // IN — message consumers in this repo
  eps.forEach((e) => {
    if (!CONSUMER_TYPES.has(e.type) || !e.channel) return;
    const c = card("msg|" + e.channel, "msg");
    c.inMethods.push({ m: e.method || e.id.split(":").pop(), t: e.message_type || "" });
    peerRepos(e.channel, "producers").forEach((r) => c.inPeers.push(r));
  });

  // OUT — message producers in this repo
  prods.forEach((p) => {
    if (!PRODUCER_TYPES.has(p.type) || !p.channel) return;
    const c = card("msg|" + p.channel, "msg");
    c.outMethods.push({ m: p.method, t: p.message_type || "" });
    peerRepos(p.channel, "consumers").forEach((r) => c.outPeers.push(r));
  });

  // HTTP — sync outbound calls (verb + path as the "channel", return type as the payload analog)
  prods.forEach((p) => {
    if (p.type !== "http-call" || !p.channel) return;
    const c = card("http|" + p.channel, "http");
    c.verb = p.message_type || "";
    c.outMethods.push({ m: p.method, t: p.response_type || "" });
    peerRepos(p.channel, "consumers").forEach((r) => c.outPeers.push(r));
  });

  const cards = Array.from(byKey.values());
  cards.forEach((c) => {
    // Normalize OUT methods to {m,t} pairs, then dedupe both sides by method+type
    c.outMethods = c.outMethods.map((o) => (typeof o === "string" ? { m: o, t: "" } : o));
    const seenIn = new Set();
    c.inMethods = c.inMethods.filter((o) => {
      const k = o.m + "|" + o.t;
      if (seenIn.has(k)) return false;
      seenIn.add(k);
      return true;
    });
    const seenOut = new Set();
    c.outMethods = c.outMethods.filter((o) => {
      const k = o.m + "|" + o.t;
      if (seenOut.has(k)) return false;
      seenOut.add(k);
      return true;
    });
    c.inPeers = Array.from(new Set(c.inPeers));
    c.outPeers = Array.from(new Set(c.outPeers));
    if (c.inMethods.length > 0 && c.outMethods.length > 0) c.direction = "both";
    else if (c.inMethods.length === 0) c.direction = "out";
  });

  // Sort: message channels (IN → BOTH → OUT) then HTTP calls
  const dirOrder = { in: 0, both: 1, out: 2 };
  return cards.sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "http" ? 1 : -1;
    return (dirOrder[a.direction] - dirOrder[b.direction]) || a.channel.localeCompare(b.channel);
  });
}

// One self-contained sentence per method:
// "emits OrderEvent through OrderEventProducer.publishCreated"
const methodSentence = (verb, o) => verb + (o.t ? " " + o.t + " through " + o.m : " " + o.m);

// ── Flow matching for channel units ─────────────────────────────
// Flatten a flow's step tree to the step list (repo-level participations).
function flowSteps(step, out = []) {
  if (!step) return out;
  out.push(step);
  (step.children || []).forEach((ch) => flowSteps(ch.step, out));
  return out;
}

// Does the call tree reach a node whose method matches `method`? Mirrors the
// flow detector's reachability forms: `node.method` (resolved "Class.method"
// or receiver.method) and `Class.shortMethod` from class_name + short name.
function treeReaches(tree, method) {
  if (!tree) return false;
  const stack = [tree];
  while (stack.length) {
    const n = stack.pop();
    if (n.method === method) return true;
    const mName = (n.method || "").split(".").pop();
    if (n.class_name && mName && n.class_name + "." + mName === method) return true;
    (n.children || []).forEach((c) => stack.push(c));
  }
  return false;
}

// Flows a channel unit participates in. IN: this repo's flow step entered via
// this channel through this handler. OUT: this repo's flow step publishes this
// channel AND its entry's call tree reaches the producer method.
function flowsForUnit(flows, graph, repo, card, o, side) {
  const entryById = new Map((graph.entry_points || []).map((e) => [e.id, e]));
  return flows.filter((f) =>
    flowSteps(f.step).some((s) => {
      if (s.repo !== repo) return false;
      if (side === "in") {
        return s.channel === card.channel && s.method === o.m;
      }
      if (!(s.publishesTo || []).includes(card.channel)) return false;
      const ep = entryById.get(s.entryId);
      return !!ep && treeReaches(ep.call_tree, o.m);
    })
  );
}

function ChannelCard({ c, onOpenFlow }) {
  const flowChips = (o) =>
    o.flows && o.flows.length > 0 ? (
      <div className="cc-flows">
        {o.flows.map((f) => (
          <button
            key={f.id}
            className="cc-flow-chip"
            title={"Open flow \u201C" + f.name + "\u201D"}
            onClick={(e) => { e.stopPropagation(); onOpenFlow(f.id); }}
          >
            ↗ {f.name}
          </button>
        ))}
      </div>
    ) : null;
  return (
    <div className={"channel-card dir-" + c.direction + (c.kind === "http" ? " http" : "")}>
      <div className="cc-top">
        <span className="cc-badge">
          {c.kind === "http" ? "REQUEST" : c.direction === "both" ? "IN+OUT" : c.direction.toUpperCase()}
        </span>
        <span className="cc-name mono">
          {c.kind === "http" && c.verb ? c.verb + " " : ""}{c.channel}
        </span>
        <span className={"cc-kind " + c.kind} title={c.kind === "http" ? "Sync HTTP call" : "Message channel"}>
          {c.kind === "http" ? "⚡" : "◆"}
        </span>
      </div>
      {c.inMethods.length > 0 && (
        <div className="cc-dir in">
          {c.inMethods.map((o, i) => (
            <div className="cc-unit" key={i}>
              <div className="cc-sentence mono">{methodSentence("consumes", o)}</div>
              <div className="cc-peerline">
                {c.inPeers.length > 0
                  ? <span className="cc-peer in">from {c.inPeers.join(", ")}</span>
                  : <span className="cc-peer none">no producer found</span>}
              </div>
              {flowChips(o)}
            </div>
          ))}
        </div>
      )}
      {c.outMethods.length > 0 && (
        <div className="cc-dir out">
          {c.outMethods.map((o, i) => (
            <div className="cc-unit" key={i}>
              <div className="cc-sentence mono">{methodSentence(c.kind === "http" ? "requests" : "emits", o)}</div>
              <div className="cc-peerline">
                {c.outPeers.length > 0
                  ? <span className="cc-peer out">to {c.outPeers.join(", ")}</span>
                  : <span className="cc-peer none">no consumer found</span>}
              </div>
              {flowChips(o)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ChannelsPanel({ channels, repo, onOpenFlow }) {
  // Split by direction into clear sections. A channel this repo both consumes
  // and emits is a bridge — it gets its own section, never a duplicate card.
  const consumes = channels.filter((c) => c.direction === "in");
  const bridges = channels.filter((c) => c.direction === "both");
  const emits = channels.filter((c) => c.direction === "out");
  const section = (title, items) =>
    items.length === 0 ? null : (
      <div key={title}>
        <div className="cp-section">
          <span className="cp-section-title">{title}</span>
          <span className="cp-section-count">{items.length}</span>
        </div>
        {items.map((c) => <ChannelCard key={c.kind + "|" + c.channel} c={c} onOpenFlow={onOpenFlow} />)}
      </div>
    );

  return (
    <aside className="channels-panel glass">
      <h3 className="cp-head">
        <span className="cp-head-dot" aria-hidden="true" />
        <span className="cp-repo" title={repo}>{repo}</span>
      </h3>
      {channels.length === 0 ? (
        <p className="muted small cp-empty">
          No channels detected — this service has no inbound or outbound integration points.
        </p>
      ) : (
        <div className="cp-list">
          {section("Consumes", consumes)}
          {section("Bridges", bridges)}
          {section("Sends", emits)}
        </div>
      )}
    </aside>
  );
}

/* ---------------- Path View ---------------- */

const PV_NODE_W = 240;
const PV_TOGGLE_W = 32;  // toggle bar on the right of nodes with children
const PV_NODE_H = 110;  // estimated height including toggle footer
const PV_HSPACE = 340;  // horizontal distance between depth levels
const PV_VGAP = 50;     // vertical gap between sibling nodes

function PathView({ entryPoint, graph, selectedNode, onSelectNode, chatOpen }) {
  const tree = entryPoint.call_tree;

  // Outbound channels for this entry point
  const outboundChannels = useMemo(() => {
    if (!graph) return [];
    const producers = graph.producers || [];
    const links = graph.cross_repo_links || [];
    const myMethod = entryPoint.method || entryPoint.id.split(":").pop();
    const myClass = entryPoint.class_name || "";
    const matches = producers.filter((p) => {
      if (p.repo !== entryPoint.repo) return false;
      return p.method === myMethod
        || p.method === (myClass + "." + myMethod)
        || p.method.endsWith("." + myMethod);
    });
    return matches.map((p) => {
      const link = links.find((l) => l.channel === p.channel);
      const consumerRepos = link ? Array.from(new Set(
        (link.consumers || []).map((c) => c.split(":")[0]).filter(Boolean)
      )) : [];
      return { channel: p.channel, consumerRepos };
    });
  }, [graph, entryPoint]);

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
                  <div className="pv-method">{d.method || d.class_name || "unknown"}</div>
                  <div className="pv-loc mono">{fmtFile(d.file)}{d.line ? ":" + d.line : ""}</div>
                  {d.confidence && (
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

          {/* Exit point */}
          {outboundChannels.length > 0 && (
            <div
              className="exit-point"
              style={{
                top: layout.maxY + 30,
                left: 0,
                width: PV_NODE_W + 100,
              }}
            >
              <div className="exit-point-label">EMITS TO</div>
              {outboundChannels.map((oc, i) => (
                <div className="exit-point-flow" key={i}>
                  <span className="exit-point-channel">{oc.channel}</span>
                  <span className="exit-point-arrow">→</span>
                  <span className="exit-point-target">
                    {oc.consumerRepos.length > 0
                      ? oc.consumerRepos.join(", ")
                      : "no consumer"}
                  </span>
                </div>
              ))}
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
        {node.confidence && (
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
// Galaxy equivalent — shows all detected flows as cards in the starfield.
// Standard scroll layout: cards flow into a responsive CSS grid and the canvas
// scrolls normally when there are more flows than fit on screen. No pan/zoom —
// with many flows the shrink-to-fit + zoom model became unusable.
function FlowIndexView({ graph, dims, onSelectFlow }) {
  const flows = useMemo(() => detectFlows(graph), [graph]);
  const H = dims.h;

  // Uniform card height: all cards share the tallest card's height so rows
  // stay aligned (same behaviour as the old grid, kept for the scroll layout).
  const [uniformH, setUniformH] = useState(null);
  const cardRefs = useRef([]);
  useLayoutEffect(() => {
    if (flows.length === 0) return;
    const hs = cardRefs.current.map((el) => (el ? el.offsetHeight : 0));
    if (hs.length === 0) return;
    const maxH = Math.max(...hs);
    setUniformH((prev) => (prev === maxH ? prev : maxH));
  }, [flows]);

  return (
    <div className="galaxy flow-index">
      <div className="view-top">
        <div className="view-hint">
          {flows.length} flows detected · {flows.filter(f => f.hasCrossRepo).length} cross-repo
        </div>
      </div>
      <div className="canvas flow-scroll" style={{ height: H }}>
        <div className="flow-grid flow-grid-static">
          {flows.map((f, i) => (
            <div
              key={f.id}
              ref={(el) => { cardRefs.current[i] = el; }}
              className={"flow-card" + (f.hasCrossRepo ? " cross-repo" : "")}
              style={{ height: uniformH || undefined, animationDelay: (i * 45) + "ms" }}
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
                {f.hasSync && (
                  <>
                    <span className="flow-stat-sep">·</span>
                    <span className="flow-stat sync-badge">⚡ sync</span>
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
          ))}
        </div>
      </div>
    </div>
  );
}

/* ---------------- Flows Mode: Flow View ---------------- */
// Solar equivalent — shows repos in a single flow as a DAG with channel edges
function FlowView({ flow, graph, dims, onSelectRepoInFlow }) {
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
        const ekey = step.repo + ">>" + child.step.repo + "|" + child.channel + "|" + (child.kind || "message");
        if (!seenEdges.has(ekey)) {
          seenEdges.add(ekey);
          const isSync = child.kind === "http";
          edges.push({
            from: step.repo,
            to: child.step.repo,
            channel: child.channel,
            kind: child.kind || "message",
            verb: child.verb || "",
            responseType: child.responseType || "",
          });
          // Sync calls are round-trips: emit the response edge pointing back,
          // so the reader sees the request AND what comes back.
          if (isSync && child.responseType) {
            const rekey = child.step.repo + ">>" + step.repo + "|" + child.channel + "|http-response";
            if (!seenEdges.has(rekey)) {
              seenEdges.add(rekey);
              edges.push({
                from: child.step.repo,
                to: step.repo,
                channel: child.channel,
                kind: "http-response",
                verb: "",
                responseType: child.responseType,
              });
            }
          }
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

  // Edge geometry: curved bezier between the inner-facing edges of the pair
  // (an edge always attaches at the side of each node that faces the other,
  // so a request + response pair share the same attachment points). Skip edges
  // (spanning >1 depth) get a strong vertical arc to avoid intermediate nodes.
  const edgeGeom = (a, b, edgeIndex, totalEdges, isSkip) => {
    const NODE_HALF_W = 85;
    const forward = b.x >= a.x;
    const start = { x: a.x + (forward ? NODE_HALF_W : -NODE_HALF_W), y: a.y };
    const end = { x: b.x - (forward ? NODE_HALF_W : -NODE_HALF_W), y: b.y };
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

    // Parallel/opposite edges (request + response round-trips) bow apart
    // vertically around the axis; single edges stay on it.
    const sep = totalEdges > 1 ? (edgeIndex - (totalEdges - 1) / 2) * 2 : 0;
    const bend = sep * Math.min(72, Math.max(30, Math.abs(dx) * 0.22));
    const cp1 = { x: start.x + dx * 0.35, y: start.y + bend };
    const cp2 = { x: end.x - dx * 0.35, y: end.y + bend };
    const path = `M ${start.x} ${start.y} C ${cp1.x} ${cp1.y} ${cp2.x} ${cp2.y} ${end.x} ${end.y}`;
    const mid = { x: start.x + dx / 2, y: (start.y + end.y) / 2 + bend * 0.75 };
    return { mid, path };
  };

  // Group edges by unordered repo pair so opposite (request/response) edges
  // share a group and separate vertically.
  const edgePairCount = useMemo(() => {
    const m = {};
    flowEdges.forEach((e) => {
      const key = e.from < e.to ? e.from + ">>" + e.to : e.to + ">>" + e.from;
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
        <div className="view-hint">
          {flow.repoCount} repos · {flow.hopCount} hop{flow.hopCount === 1 ? "" : "s"} ·
          {" "}origin: {flow.originNoun || (flow.originType === "rest" ? "REST endpoint" : "external event")}
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
            <marker id="flow-arrow-sync" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="#00e0a8" opacity="0.95" />
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
            const pairKey = e.from < e.to ? e.from + ">>" + e.to : e.to + ">>" + e.from;
            const total = edgePairCount[pairKey] || 1;
            const idx = edgePairIndex(pairKey);
            const g = edgeGeom(a, b, idx, total, isSkip);
            const isSync = e.kind === "http";
            const isResp = e.kind === "http-response";
            // Request edge: verb + path. Response edge: what comes back.
            const label = isResp
              ? "← " + (e.responseType || "response")
              : isSync
                ? (e.verb ? e.verb + " " : "") + e.channel
                : e.channel;
            const pillW = label.length * 6.5 + 22;
            return (
              <g key={"fe-" + i}>
                <path d={g.path} fill="none" stroke={isSync || isResp ? "#00e0a8" : "#00d4ff"} strokeWidth={isResp ? 1.6 : isSync ? 2.2 : 2} strokeDasharray={isResp ? "5 4" : undefined} opacity={isSkip ? "0.4" : "0.55"} markerEnd={isSync || isResp ? "url(#flow-arrow-sync)" : "url(#flow-arrow)"} />
                <g className={"edge-label-pill" + (isSync || isResp ? " sync" : "")} transform={`translate(${g.mid.x}, ${g.mid.y})`}>
                  <rect className="edge-label-glow" x={-pillW / 2 - 4} y={-12} width={pillW + 8} height={24} rx={12} />
                  <rect className="edge-label-bg" x={-pillW / 2} y={-10} width={pillW} height={20} rx={10} />
                  <text className={"edge-label" + (isSync || isResp ? " sync" : "")} x={0} y={0} dominantBaseline="central" textAnchor="middle">{label}</text>
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
function FlowTraceView({ flow, repo, graph, dims, onSelectNode, selectedNode, chatOpen }) {
  // Find this repo's step(s) in the flow
  const steps = useMemo(() => {
    const found = [];

    function walk(step, parentChannel, parentKind, parentVerb, parentRepo) {
      if (step.repo === repo) {
        const via = parentKind === "http" && parentVerb
          ? parentVerb + " " + parentChannel
          : parentChannel;
        found.push({
          step,
          entersVia: via || (flow.originType === "external" ? flow.originChannel : flow.originLabel),
          entersFrom: parentRepo || (flow.originType === "external" ? (flow.originNoun || "external") : flow.originLabel),
        });
      }
      step.children.forEach((child) => {
        walk(child.step, child.channel, child.kind, child.verb || "", step.repo);
      });
    }

    walk(flow.step, null, null, null, null);
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
            kind: child.kind || "message",
            verb: child.verb || "",
            responseType: child.responseType || "",
            targetRepo: child.step.repo,
            targetMethod: child.step.method,
          });
        });
      }
      step.children.forEach((child) => walk(child.step));
    }
    walk(flow.step);
    // Deduplicate by channel+repo+kind
    const seen = new Set();
    return out.filter((d) => {
      const key = d.channel + ":" + d.targetRepo + ":" + d.kind;
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
        <p className="muted" style={{ padding: 40 }}>No trace data for this repo in this flow.</p>
      </div>
    );
  }

  return (
    <div className="path-view flow-trace">
      <div className="view-top">
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
                  <div className="pv-method">{d.method || d.class_name || "unknown"}</div>
                  <div className="pv-loc mono">{fmtFile(d.file)}{d.line ? ":" + d.line : ""}</div>
                  {d.confidence && (
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
              <div className="exit-point-label">
                {downstream.some((d) => d.kind === "http") ? "SENDS TO" : "EMITS TO"}
              </div>
              {downstream.map((d, i) => (
                <div key={i} className="exit-point-flow">
                  <span className="exit-point-channel">{d.kind === "http"
                    ? (d.verb ? d.verb + " " : "") + d.channel + (d.responseType ? " → " + d.responseType : "")
                    : d.channel}</span>
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
function GlobalChat({ graph, view, selectedNode, entryPoint, detailOpen, sidePanel, flows, pid, open, onOpenChange }) {
  const [input, setInput] = useState("");

  // ── Context: translate the current view + selection into (a) API context and (b) a readable label ──
  const ctx = useMemo(() => {
    const level = view.name;

    // ── Topology mode ──
    if (level === "galaxy") {
      return { payload: { entry_point_id: "", node: {} }, label: "Architecture overview", scope: "system" };
    }
    if (level === "solar") {
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

  // ── Unified conversation chat hook ──
  const {
    messages, loading, model, models, error,
    send, newConversation, loadConversation, deleteConversation,
    setModel, setError,
    scrollRef, inputRef,
    conversationId, convList, refreshConvList,
  } = useConversationChat({ pid, ctxPayload: ctx.payload, planner: false });

  const [showHistory, setShowHistory] = useState(false);
  const openHistory = () => { refreshConvList(); setShowHistory(true); };

  useEffect(() => {
    if (open && messages.length === 0 && inputRef.current) {
      inputRef.current.focus();
    }
  }, [open, messages.length]);

  // ── Send wrapper: clear input, pass text ──
  const sendMsg = (text) => {
    if (!text.trim() || loading) return;
    send(text.trim());
    setInput("");
  };

  // ── New conversation wrapper: reset input + delegate to the hook ──
  const newChat = () => {
    newConversation();
    setInput("");
    setError("");
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
    <div className={"global-chat" + (open ? " open" : "") + (detailOpen ? " detail-open" : "") + (sidePanel ? " side-panel" : "")}>
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
              <button className="chat-history-btn" onClick={openHistory} title="Past conversations" disabled={loading}>
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" /><path d="M12 7v5l3 2" />
                </svg>
              </button>
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

          {showHistory && (
            <ConversationMenu
              conversations={convList}
              activeId={conversationId}
              onSelect={(cid) => { loadConversation(cid); }}
              onDelete={(cid) => deleteConversation(cid)}
              onClose={() => setShowHistory(false)}
            />
          )}

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
                    <button key={i} className="chat-suggestion" onClick={() => sendMsg(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              <React.Fragment key={i}>
                {/* Text bubble — user always; assistant when it has real text or is streaming w/o tools yet */}
                {(msg.role === "user" || (msg.content && msg.content.trim()) || msg.reasoning || (msg.streaming && (!msg.tools || !msg.tools.length))) && (
                  <div className={"chat-msg " + msg.role + (msg.streaming ? " streaming" : "")}>
                    <div className="chat-msg-role">{msg.role === "user" ? "You" : "AI" + (msg.streaming ? " 🔮" : "")}</div>
                    <div className="chat-msg-text markdown">
                      <ReasoningBlock text={msg.reasoning} live={msg.streaming} />
                      <MarkdownContent src={msg.content || ""} live={msg.streaming} />
                    </div>
                    {msg.streaming && !msg.content && !msg.reasoning && <div className="ai-loading">Analyzing<span className="dots"></span></div>}
                    {msg.streaming && msg.content && <span className="stream-caret"></span>}
                  </div>
                )}
                {/* Tool chips as a standalone step (control tools like task_complete are hidden) */}
                {msg.tools && msg.tools.some((t) => t.name !== "task_complete") && (
                  <ToolSteps tools={msg.tools} />
                )}
              </React.Fragment>
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
              onKeyDown={(e) => { if (e.key === "Enter") sendMsg(input); }}
              disabled={loading}
            />
            <button className="chat-send" onClick={() => sendMsg(input)} disabled={!input.trim() || loading}>
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
  const [sourceModal, setSourceModal] = useState(null); // {file, line} | null — producer source viewer
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
  const goGaps = () => { setSelectedNode(null); setView({ name: "gaps" }); };
  const goDead = () => { setSelectedNode(null); setView({ name: "dead" }); };
  const goSolar = (repo) => { setSelectedNode(null); setView({ name: "solar", repo }); };
  const goFlow = (flowId) => { setSelectedNode(null); setView({ name: "flow", flowId }); };
  const stageH = dims.h;

  // Single navigation trail rendered in the header (see buildCrumbs above).
  const crumbs = useMemo(
    () => buildCrumbs(view, mode, graph, flows, (activeMeta && activeMeta.name) || "", {
      goProjects: backToProjects,
      goGalaxy, goGaps, goDead, goSolar, goFlowIndex, goFlow,
    }),
    [view, mode, graph, flows, activeMeta] // eslint-disable-line
  );

  const switchMode = (m) => {
    setMode(m);
    setSelectedNode(null);
    if (m === "topology") setView({ name: "galaxy" });
    else if (m === "dead") setView({ name: "dead" });
    else if (m === "planner") setView({ name: "planner" });
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
        onHome={backToProjects}
        stale={!!(updatesByPid[activeId] && updatesByPid[activeId].stale_count > 0)}
        crumbs={crumbs}
      />
      <main className="stage">
        {/* ── Topology mode (existing) ── */}
        {mode === "topology" && view.name === "galaxy" && (
          <div className="view" key="galaxy">
            <GalaxyView
              graph={graph}
              dims={{ w: dims.w, h: stageH }}
              onOpenGaps={() => { setSelectedNode(null); setView({ name: "gaps" }); }}
              onSelectRepo={(repo, e) => {
                const [x, y] = centerOf(e && e.currentTarget);
                drill(x, y, () => { setSelectedNode(null); setView({ name: "solar", repo }); });
              }}
            />
          </div>
        )}
        {mode === "topology" && view.name === "gaps" && (
          <div className="view" key="gaps">
            <GapsView
              graph={graph}
              pid={activeId}
              onOpenEntry={(id) => { setSelectedNode(null); setView({ name: "path", entryId: id }); }}
              onOpenSource={(file, line) => setSourceModal({ file, line })}
            />
          </div>
        )}
        {mode === "topology" && view.name === "solar" && (
          <div className="view" key={"solar-" + view.repo}>
            <SolarSystemView
              graph={graph}
              repo={view.repo}
              dims={{ w: dims.w, h: stageH }}
              flows={flows}
              onOpenFlow={(flowId) => {
                setSelectedNode(null);
                setMode("flows");
                setView({ name: "flow", flowId });
              }}
              onSelectEntry={(id, e) => {
                const [x, y] = centerOf(e && e.currentTarget);
                drill(x, y, () => { setSelectedNode(null); setView({ name: "path", entryId: id }); });
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
                selectedNode={selectedNode}
                onSelectNode={(n) => setSelectedNode(n)}
                chatOpen={chatOpen}
              />
            </div>
          );
        })()}
        {mode === "dead" && view.name === "path" && (() => {
          // Drill-in from the Dead-code view: same PathView, but back returns
          // to the Dead-code list instead of the topology solar system.
          const ep = graph.entry_points.find((e) => e.id === view.entryId);
          if (!ep) return <div className="view"><p className="muted" style={{ padding: 40 }}>Entry point not found.</p></div>;
          return (
            <div className="view" key={"dead-path-" + view.entryId}>
              <PathView
                entryPoint={ep}
                graph={graph}
                selectedNode={selectedNode}
                onSelectNode={(n) => setSelectedNode(n)}
                chatOpen={chatOpen}
              />
            </div>
          );
        })()}

        {/* ── Dead code mode ── */}
        {mode === "dead" && view.name === "dead" && (
          <div className="view" key="dead">
            <DeadCodeView
              graph={graph}
              onOpenEntry={(id) => { setSelectedNode(null); setView({ name: "path", entryId: id }); }}
              onOpenSource={(file, line) => setSourceModal({ file, line })}
            />
          </div>
        )}

        {/* ── Flows mode (new) ── */}
        {mode === "flows" && view.name === "flowIndex" && (
          <div className="view" key="flowIndex">
            <FlowIndexView
              graph={graph}
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
                selectedNode={selectedNode}
                onSelectNode={(n) => setSelectedNode(n)}
                chatOpen={chatOpen}
              />
            </div>
          );
        })()}

        {/* ── AI Change Planner ── */}
        {mode === "planner" && (
          <div className="view" key="planner">
            <ChangePlannerView graph={graph} pid={activeId} />
          </div>
        )}
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

      {mode !== "planner" && (
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
        sidePanel={view.name === "solar"}
      />
      )}

      {sourceModal && (
        <SourceModal
          pid={activeId}
          file={sourceModal.file}
          line={sourceModal.line}
          onClose={() => setSourceModal(null)}
        />
      )}

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
    determinate = repoTotal != null && repoTotal > 0;
    label = determinate ? "Cloning " + done + "/" + repoTotal + " repos" : "Syncing repositories…";
  } else if (phase === "scan") {
    done = cap(logs.filter((l) => l.phase === "scan" && SCANNING_RE.test(l.message)).length);
    determinate = repoTotal != null && repoTotal > 0;
    label = determinate ? "Scanning " + done + "/" + repoTotal + " repos" : "Scanning repositories…";
  } else if (phase === "graph") {
    label = "Building call trees…";
  } else if (phase === "link") {
    label = "Finding cross-repo links…";
  } else if (phase === "done") {
    determinate = true;
    done = repoTotal != null && repoTotal > 0 ? repoTotal : 1;
    label = "Complete";
  }
  const total = determinate ? Math.max(done, repoTotal != null && repoTotal > 0 ? repoTotal : 1) : 0;
  const pct = determinate && total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null;
  return { determinate, pct, label };
}

// Reads the SSE ingestion stream from /api/projects (create) or
// /api/projects/<pid>/repos (add). Mirrors the GlobalChat SSE reader.
const GIT_HOST_LABELS = { github: "GitHub", gitlab: "GitLab", bitbucket: "Bitbucket", "azure-devops": "Azure DevOps" };

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

  // Universal git-host import (create mode only): paste an org/workspace
  // link, pick repos with checkboxes + search, then create with those URLs.
  const [importMode, setImportMode] = useState("urls"); // "urls" | "remote"
  const [remoteLink, setRemoteLink] = useState("");
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [remoteError, setRemoteError] = useState("");
  const [remoteData, setRemoteData] = useState(null); // {provider, owner, repos}
  const [selected, setSelected] = useState({}); // full_name -> true
  const [search, setSearch] = useState("");

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

  // ── Remote git-host import state helpers ──
  const totalCount = remoteData ? remoteData.repos.length : 0;
  const selectedCount = Object.keys(selected).length;
  const allSelected = totalCount > 0 && selectedCount === totalCount;
  const visibleRepos = remoteData
    ? remoteData.repos.filter((r) => {
        const q = search.trim().toLowerCase();
        if (!q) return true;
        return ((r.name || "") + " " + (r.full_name || "") + " " + (r.description || "")).toLowerCase().includes(q);
      })
    : [];
  const repoUrls = importMode === "remote"
    ? remoteData ? remoteData.repos.filter((r) => selected[r.full_name]).map((r) => r.clone_url) : []
    : validUrls;

  const loadRemote = async () => {
    const link = remoteLink.trim();
    if (!link) return;
    setRemoteError("");
    setRemoteBusy(true);
    setRemoteData(null);
    setSelected({});
    try {
      const res = await fetch("/api/remotes/repos?link=" + encodeURIComponent(link));
      if (!res.ok) {
        const t = await res.json().catch(() => null);
        throw new Error((t && t.detail) || "Failed to load repositories (HTTP " + res.status + ")");
      }
      const data = await res.json();
      setRemoteData(data);
      const all = {};
      (data.repos || []).forEach((r) => { all[r.full_name] = true; });
      setSelected(all);
      if (!(name || "").trim()) setName(data.owner);
    } catch (e) {
      setRemoteError(e.message);
    } finally {
      setRemoteBusy(false);
    }
  };

  const toggleRepo = (fullName) =>
    setSelected((prev) => {
      const next = { ...prev };
      if (next[fullName]) delete next[fullName];
      else next[fullName] = true;
      return next;
    });

  const toggleAll = () => {
    if (allSelected) setSelected({});
    else {
      const all = {};
      (remoteData ? remoteData.repos : []).forEach((r) => { all[r.full_name] = true; });
      setSelected(all);
    }
  };

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
      ? { name: (name || "").trim() || "Untitled Project", repos: repoUrls }
      : isRescan
        ? {}
        : { repos: repoUrls };
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

  // Progress denominator = the repos actually being imported (selected repos
  // in remote mode, not the hidden URL textarea).
  const progress = computeIngestProgress(logs, isRescan ? null : repoUrls.length);

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

          {isCreate && (
            <div className="import-mode-tabs">
              <button
                type="button"
                className={"import-tab" + (importMode === "urls" ? " active" : "")}
                onClick={() => setImportMode("urls")}
                disabled={busy}
              >
                Git URLs
              </button>
              <button
                type="button"
                className={"import-tab" + (importMode === "remote" ? " active" : "")}
                onClick={() => setImportMode("remote")}
                disabled={busy}
              >
                Import from a git host
              </button>
            </div>
          )}

          {!isRescan && importMode === "urls" && (
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

          {isCreate && importMode === "remote" && (
            <label className="field">
              <span className="field-label">Project link (GitHub · GitLab · Bitbucket · Azure DevOps)</span>
              <div className="url-row">
                <input
                  className="text-input mono remote-link"
                  type="text"
                  placeholder="https://github.com/acme"
                  value={remoteLink}
                  onChange={(e) => setRemoteLink(e.target.value)}
                  disabled={busy || remoteBusy}
                />
                <button className="btn-ghost" onClick={loadRemote} disabled={busy || remoteBusy || !remoteLink.trim()}>
                  {remoteBusy ? "Loading…" : "Load repos"}
                </button>
              </div>
            </label>
          )}

          {remoteError && <div className="ingest-error">{remoteError}</div>}

          {isCreate && importMode === "remote" && remoteData && (
            <div className="remote-picker">
              <div className="remote-picker-head">
                <span className="remote-provider">{GIT_HOST_LABELS[remoteData.provider] || remoteData.provider}</span>
                <span className="remote-owner">{remoteData.owner}</span>
                <span className="remote-count">{totalCount} repo{totalCount === 1 ? "" : "s"}</span>
              </div>
              <div className="remote-toolbar">
                <input
                  className="text-input remote-search"
                  type="text"
                  placeholder="Search repos…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                <label className="remote-select-all">
                  <input type="checkbox" checked={allSelected} onChange={toggleAll} />
                  <span>Select all ({totalCount})</span>
                </label>
                <span className="remote-selected">{selectedCount} of {totalCount} selected</span>
              </div>
              <div className="remote-list">
                {visibleRepos.length === 0 && <div className="muted small remote-empty">No repos match "{search}".</div>}
                {visibleRepos.map((r) => (
                  <label className={"remote-row" + (selected[r.full_name] ? " checked" : "")} key={r.full_name}>
                    <input type="checkbox" checked={!!selected[r.full_name]} onChange={() => toggleRepo(r.full_name)} />
                    <span className="remote-row-name">{r.name}</span>
                    <span className="remote-row-full">{r.full_name}</span>
                    {r.description && <span className="remote-row-desc">{r.description}</span>}
                  </label>
                ))}
              </div>
            </div>
          )}

          {!isRescan && (
            <p className="muted small">
              {importMode === "remote"
                ? "Only the checked repositories are cloned (shallow) and analysed together so cross-service links are detected."
                : "Repos are cloned (shallow) and analysed together so cross-service links are detected."}
              {importMode === "urls" && (
                <> Use <code>local:path</code> to add an existing folder on disk (git-backed ones are still tracked for updates).</>
              )}
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

          {error && <div className="ingest-error">{error}</div>}
        </div>

        {(status === "running" || status === "done" || status === "error") && (
          <div className="ingest-log modal-ingest">
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
                disabled={busy || repoUrls.length === 0 || (isCreate && !(name || "").trim())}
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
