/* ============================================================
   CONSTELLATION — Codebase Mapper frontend
   React 18 + Babel standalone, no build step.
   ============================================================ */

const { useState, useEffect, useRef, useMemo, useLayoutEffect, useCallback } = React;

/* ---------------- helpers ---------------- */
const repoFromId = (id) => (typeof id === "string" ? id.split(":")[0] : "");

const TYPE_META = {
  "rest-endpoint":     { color: "#ff4d6d", label: "REST",      glow: "rgba(255,77,109,.55)" },
  "kafka-consumer":    { color: "#ffd60a", label: "Kafka",     glow: "rgba(255,214,10,.55)" },
  "rabbitmq-consumer": { color: "#ff8c42", label: "RabbitMQ",  glow: "rgba(255,140,66,.55)" },
  "event-listener":    { color: "#4895ef", label: "Event",     glow: "rgba(72,149,239,.55)" },
  "scheduled-task":    { color: "#34d399", label: "Scheduled", glow: "rgba(52,211,153,.55)" },
  "websocket":         { color: "#a855f7", label: "WebSocket", glow: "rgba(168,85,247,.55)" },
};
const typeMeta = (t) => TYPE_META[t] || { color: "#94a3b8", label: (t || "Unknown"), glow: "rgba(148,163,184,.5)" };

const CONFIDENCE = {
  EXTRACTED: { color: "#34d399" },
  INFERRED:  { color: "#fbbf24" },
  AMBIGUOUS: { color: "#f87171" },
  TRUNCATED: { color: "#a78bfa" },
};
const confMeta = (c) => CONFIDENCE[c] || { color: "#94a3b8" };

function renderMarkdown(src) {
  if (!src) return "";
  if (window.marked) {
    return sanitizeHTML(window.marked.parse(src, { breaks: true }));
  }
  return escapeHTML(src);
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
function Header({ graph, mode, onModeChange, projectName, onHome }) {
  const gen = graph && graph.generated_at
    ? new Date(graph.generated_at).toLocaleString()
    : "";
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
                <button className="crumb link" onClick={onHome}>Projects</button>
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
          >Topology</button>
          <button
            className={"mode-btn" + (mode === "flows" ? " active" : "")}
            onClick={() => onModeChange("flows")}
          >Flows</button>
        </div>
      )}
      <div className="meta">
        {graph && graph.engine_version && <span className="meta-pill">engine v{graph.engine_version}</span>}
        {gen && <span className="meta-pill">{gen}</span>}
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
function Legend() {
  return (
    <div className="legend glass">
      <div className="legend-title">Entry point types</div>
      {Object.keys(TYPE_META).map((k) => (
        <div className="legend-item" key={k}>
          <span className="legend-dot" style={{ background: TYPE_META[k].color, color: TYPE_META[k].color }}></span>
          {TYPE_META[k].label}
        </div>
      ))}
      <div className="legend-item">
        <span className="legend-line" style={{ background: "#00e0a8" }}></span>
        Sync HTTP call
      </div>
      <div className="legend-hint">Click a repo to zoom in</div>
    </div>
  );
}

/* ---------------- Flow Detection Engine ---------------- */
// Computes end-to-end flows from graph.json — no engine changes needed.
// A flow is a chain: origin (REST or external event) → [publishes → channel → consumer → publishes → ...]
// Each step is { repo, entryId, method, type, channel, isExternal, publishesTo: [channelNames], next: [stepRefs] }

const PUBLISH_KEYWORDS = ["convertandsend", "send", "publish", "emit"];

function detectFlows(graph) {
  const entries = graph.entry_points || [];
  const links = graph.cross_repo_links || [];

  // Index entry points by id
  const entryById = {};
  entries.forEach((e) => { entryById[e.id] = e; });

  // Index: which channels does each producer method publish to? (from cross_repo_links)
  // producer id format: "repo:ClassName.method:publishMethod"
  const channelByProducerRepo = {}; // repo -> [{ channel, producerMethod }]
  links.forEach((link) => {
    (link.producers || []).forEach((prodId) => {
      const repo = repoFromId(prodId);
      if (!channelByProducerRepo[repo]) channelByProducerRepo[repo] = [];
      channelByProducerRepo[repo].push({ channel: link.channel, producerId: prodId });
    });
  });

  // Index: which entry points consume a given channel?
  const consumersByChannel = {}; // channel -> [entryId]
  entries.forEach((e) => {
    if (e.type !== "rest-endpoint") {
      const ch = e.channel || "";
      if (ch) {
        if (!consumersByChannel[ch]) consumersByChannel[ch] = [];
        consumersByChannel[ch].push(e.id);
      }
    }
  });

  // Index: which channels are produced internally (so we can identify external origins)
  const internalChannels = new Set(links.map((l) => l.channel));

  // Check if a call tree contains a publish to a channel
  function publishesChannels(entryPoint) {
    const channels = new Set();
    const repo = entryPoint.repo;
    // Producer id format: "repo:ClassName.method:publishMethod"
    const repoProds = channelByProducerRepo[repo] || [];

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

    const channels = publishesChannels(entry);
    const consumers = []; // [{ channel, entryId }]

    channels.forEach((ch) => {
      const consumerIds = consumersByChannel[ch] || [];
      consumerIds.forEach((cid) => {
        if (cid === entryId) return;
        const ce = entryById[cid];
        if (!ce) return;
        // Don't recurse into same repo (intra-repo calls)
        if (ce.repo === entry.repo) return;
        consumers.push({ channel: ch, entryId: cid });
      });
    });

    // Recursively build child steps
    const children = consumers.map((c) => {
      const childEntry = entryById[c.entryId];
      const childStep = buildSteps(childEntry, nextVisited);
      if (!childStep) return null;
      return { channel: c.channel, step: childStep };
    }).filter(Boolean);

    return {
      repo: entry.repo,
      entryId: entry.id,
      method: entry.method || entry.id.split(":").pop(),
      type: entry.type,
      channel: entry.channel || "",
      publishesTo: channels,
      children, // [{ channel, step }]
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

    // Generate flow name
    let name, originLabel;
    if (isRest) {
      name = entry.method || entry.id.split(":").pop();
      // Convert camelCase to Title Case
      name = name.replace(/([A-Z])/g, " $1").replace(/^./, (s) => s.toUpperCase()).trim();
      originLabel = ((entry.method_type || "POST") + " ") + (entry.channel || "");
    } else {
      name = entry.channel || entry.method || "External Event";
      originLabel = entry.channel || "";
    }

    flows.push({
      id: "flow:" + entry.id,
      name,
      originLabel,
      originType: isRest ? "rest" : "external",
      originChannel: entry.channel || "",
      originMethodType: entry.method_type || "",
      step,
      repos: Array.from(repos),
      repoCount: repos.size,
      hopCount: depth - 1,
      hasCrossRepo,
    });
  });

  return flows;
}

/* ---------------- Galaxy View ---------------- */
function GalaxyView({ graph, dims, onSelectRepo }) {
  const repos = graph.repos || [];
  const entryPoints = graph.entry_points || [];
  const links = graph.cross_repo_links || [];
  const pz = usePanZoom(".repo-wrap, .legend, .filter-chip");

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

  const edges = useMemo(() => {
    const out = [];
    const seen = new Set();
    const pairCount = {};
    links.forEach((link) => {
      const pRepos = Array.from(new Set((link.producers || []).map(repoFromId)));
      const cRepos = Array.from(new Set((link.consumers || []).map(repoFromId)));
      pRepos.forEach((pr) => cRepos.forEach((cr) => {
        if (pr === cr) return;
        const key = pr + ">>" + cr + "|" + link.channel;
        if (seen.has(key)) return;
        seen.add(key);
        const pairKey = pr + ">>" + cr;
        pairCount[pairKey] = (pairCount[pairKey] || 0) + 1;
        out.push({
          from: pr, to: cr, channel: link.channel,
          kind: link.kind || "message",
          verb: link.verb || "",
          pairKey,
        });
      }));
    });
    // Index each edge within its from→to pair so multiples can fan out
    const pairIndex = {};
    out.forEach((e) => {
      pairIndex[e.pairKey] = pairIndex[e.pairKey] || 0;
      e.pairIndex = pairIndex[e.pairKey]++;
      e.pairCount = pairCount[e.pairKey];
    });
    return out;
  }, [graph]);

  const edgeGeom = (a, b, opts = {}) => {
    const GAP = 3; // uniform clearance at both orb edges
    const EDGE_SEP = 60; // perpendicular fan distance between same-pair edges
    const dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.hypot(dx, dy) || 1;
    const ux = dx / d, uy = dy / d;
    const start = { x: a.x + ux * (a.r + GAP), y: a.y + uy * (a.r + GAP) };
    const end = { x: b.x - ux * (b.r + GAP), y: b.y - uy * (b.r + GAP) };
    const bend = Math.min(130, d * 0.26);
    let c = { x: (start.x + end.x) / 2 - uy * bend, y: (start.y + end.y) / 2 + ux * bend };
    // Fan same-pair edges apart along the perpendicular of the a→b axis.
    // Shift the WHOLE curve (start/end/control) so parallel edges never cross.
    const total = opts.pairCount || 1, index = opts.pairIndex || 0;
    const off = (index - (total - 1) / 2) * EDGE_SEP;
    if (off !== 0) {
      const ox = -uy * off, oy = ux * off;
      start.x += ox; start.y += oy;
      end.x += ox; end.y += oy;
      c.x += ox; c.y += oy;
    }
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
        <Breadcrumb items={[{ label: "Galaxy" }]} />
        <div className="view-hint">
          {repos.length} repos · {entryPoints.length} entry points · {(graph.producers || []).length} producers
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
            <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="#00d4ff" opacity="0.9"></path>
            </marker>
          </defs>
          {edges.map((e, i) => {
            const a = posMap[e.from], b = posMap[e.to];
            if (!a || !b) return null;
            const g = edgeGeom(a, b, { pairIndex: e.pairIndex, pairCount: e.pairCount });
            const isHttp = e.kind === "http";
            const label = isHttp && e.verb ? (e.verb + " " + e.channel) : e.channel;
            const pillW = label.length * 6.5 + 22;
            const pillH = 20;
            return (
              <g className={"edge" + (isHttp ? " edge-http" : "")} key={i}>
                <path d={g.path} fill="none" stroke={isHttp ? "#00e0a8" : "#00d4ff"}
                      strokeWidth={isHttp ? 2.2 : 1.6}
                      opacity={isHttp ? 0.95 : 0.5} markerEnd="url(#arrow)"></path>
                <g className="edge-label-pill" transform={"translate(" + g.mid.x + "," + g.mid.y + ")"}>
                  <rect className={isHttp ? "edge-label-glow http" : "edge-label-glow"} x={-pillW / 2 - 4} y={-pillH / 2 - 4} width={pillW + 8} height={pillH + 8} rx={(pillH + 8) / 2}></rect>
                  <rect className={isHttp ? "edge-label-bg http" : "edge-label-bg"} x={-pillW / 2} y={-pillH / 2} width={pillW} height={pillH} rx={pillH / 2}></rect>
                  <text className="edge-label" x={0} y={0} dominantBaseline="central" textAnchor="middle">{label}</text>
                </g>
              </g>
            );
          })}
        </svg>
        {positions.map((p) => {
          const types = repoTypes[p.name] || [];
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
              <div className="repo-label" style={{ top: p.r + 26 }}>{p.name}</div>
            </div>
          );
        })}
        </div>
      </div>
      <Legend />
      {pz.zoomControls}
    </div>
  );
}

/* ---------------- Solar System View ---------------- */
function SolarSystemView({ graph, repo, dims, onHome, onBack, onSelectEntry }) {
  const eps = useMemo(
    () => (graph.entry_points || []).filter((e) => e.repo === repo),
    [graph, repo]
  );
  const producers = useMemo(
    () => (graph.producers || []).filter((p) => p.repo === repo),
    [graph, repo]
  );
  const [hidden, setHidden] = useState({});
  const pz = usePanZoom(".star, .star-label, .producers-panel");

  const W = dims.w, H = dims.h;
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
        <Breadcrumb items={[
          { label: "Galaxy", onClick: onHome },
          { label: repo },
        ]} />
        <div className="view-hint">{eps.length} entry points · {producers.length} producers</div>
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

      <ProducersPanel producers={producers} graph={graph} />
      {pz.zoomControls}
    </div>
  );
}

function ProducersPanel({ producers, graph }) {
  // Group producers by channel and find consumers from cross_repo_links
  const channels = useMemo(() => {
    const links = (graph || {}).cross_repo_links || [];
    const ep_repos = (graph || {}).entry_points || [];
    const map = {};
    producers.forEach((p) => {
      if (!map[p.channel]) map[p.channel] = [];
      map[p.channel].push(p);
    });
    return Object.entries(map).map(([channel, prods]) => {
      const link = links.find((l) => l.channel === channel);
      const consumerRepos = link ? Array.from(new Set(
        (link.consumers || [])
          .map((c) => c.split(":")[0])
          .filter((r) => r)
      )) : [];
      return { channel, producers: prods, consumerRepos };
    });
  }, [producers, graph]);

  return (
    <aside className="producers-panel glass">
      <h3>Outbound <span className="muted">({producers.length})</span></h3>
      {producers.length === 0 && <p className="muted small">No outbound producers detected.</p>}
      <ul className="producer-list">
        {channels.map(({ channel, producers: prods, consumerRepos }) => (
          <li className="producer-item" key={channel}>
            <div className="producer-channel">
              <span className="producer-channel-name">{channel}</span>
              <span className="producer-arrow">→</span>
              <span className="producer-flow-targets">
                {consumerRepos.length > 0
                  ? consumerRepos.join(", ")
                  : "no consumer found"}
              </span>
            </div>
            <div className="producer-flow">
              {prods.map((p) => (
                <div key={p.id} className="producer-flow-method">
                  {p.method}
                </div>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </aside>
  );
}

/* ---------------- Path View ---------------- */

const PV_NODE_W = 240;
const PV_TOGGLE_W = 32;  // toggle bar on the right of nodes with children
const PV_NODE_H = 110;  // estimated height including toggle footer
const PV_HSPACE = 340;  // horizontal distance between depth levels
const PV_VGAP = 50;     // vertical gap between sibling nodes

function PathView({ entryPoint, graph, onHome, onBack, selectedNode, onSelectNode, chatOpen }) {
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

    // Build edges — parent right-center → child left-center
    const edges = [];
    placed.forEach((node) => {
      if (node.isExpanded && node.children.length > 0) {
        node.children.forEach((ci) => {
          const child = placed[ci];
          edges.push({
            x1: node.x + PV_NODE_W + (node.hasKids ? PV_TOGGLE_W : 0),
            y1: node.y + PV_NODE_H / 2,
            x2: child.x,
            y2: child.y + PV_NODE_H / 2,
          });
        });
      }
    });

    const maxX = Math.max(...placed.map((p) => p.x)) + PV_NODE_W;
    const maxY = Math.max(...placed.map((p) => p.y)) + PV_NODE_H;

    return { nodes: placed, edges, maxX, maxY };
  }, [tree, expanded]);

  // ── Infinite canvas viewport ────────────────────────────────
  const containerRef = useRef(null);
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
            {layout.edges.map((edge, i) => {
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
// Galaxy equivalent — shows all detected flows as cards in the starfield
function FlowIndexView({ graph, dims, onSelectFlow }) {
  const flows = useMemo(() => detectFlows(graph), [graph]);
  const W = dims.w, H = dims.h;
  const cx = W / 2, cy = H / 2;
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

  // Position flow cards in a grid-like arrangement
  const positions = useMemo(() => {
    const n = flows.length;
    const cols = Math.min(n, n <= 4 ? 2 : 3);
    const cardW = 240;
    const gapX = 50, gapY = 36;
    const totalW = cols * cardW + (cols - 1) * gapX;
    // Uniform height across the whole grid (fall back to a reasonable estimate
    // before the first measurement lands)
    const cardH = uniformH || 196;
    const rows = Math.ceil(n / cols);
    const totalH = rows * cardH + (rows - 1) * gapY;
    const positions = [];
    let y = cy - totalH / 2;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const idx = r * cols + c;
        if (idx >= n) break;
        positions.push({
          x: cx - totalW / 2 + c * (cardW + gapX),
          y: y,
          w: cardW,
          h: uniformH || null, // null = keep natural (auto) height until measured
        });
      }
      y += cardH + gapY;
    }
    return positions;
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
        {flows.map((f, i) => {
          const pos = positions[i];
          return (
            <div
              key={f.id}
              ref={(el) => { cardRefs.current[i] = el; }}
              className={"flow-card" + (f.hasCrossRepo ? " cross-repo" : "")}
              style={{ left: pos.x, top: pos.y, width: pos.w, height: pos.h || undefined }}
              onClick={(e) => onSelectFlow(f, e)}
            >
              <div className="flow-card-glow" />
              <div className="flow-card-origin">
                {f.originType === "rest" ? (
                  <span className="flow-origin-tag rest">REST</span>
                ) : (
                  <span className="flow-origin-tag external">EXTERNAL</span>
                )}
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
      externals.push({ channel: flow.originChannel, targetRepo: flow.step.repo, kind: "external" });
    }
    if (flow.originType === "rest") {
      externals.push({ channel: flow.originChannel, targetRepo: flow.step.repo, kind: "rest", verb: flow.originMethodType || "POST" });
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
          {flow.repoCount} repos · {flow.hopCount} hop{flow.hopCount === 1 ? "" : "s"} ·
          {" "}origin: {flow.originType === "rest" ? "REST endpoint" : "external event"}
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
            className={ei.kind === "rest" ? "flow-external-node rest-origin" : "flow-external-node"}
            style={{ left: layout.externalPos[i].x - 80, top: layout.externalPos[i].y - 50 }}
          >
            <div className="flow-external-icon">{ei.kind === "rest" ? "⟶" : "⌁"}</div>
            <div className="flow-external-label">{ei.kind === "rest" ? (ei.verb + " " + ei.channel) : ei.channel}</div>
            <div className="flow-external-sub">{ei.kind === "rest" ? "REST" : "external"}</div>
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
          entersFrom: parentRepo || (flow.originType === "external" ? "external" : flow.originLabel),
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
    if (!tree) return { nodes: [], edges: [], maxX: 0, maxY: 0 };
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

    const edges = [];
    placed.forEach((node) => {
      if (node.isExpanded && node.children.length > 0) {
        node.children.forEach((ci) => {
          const child = placed[ci];
          edges.push({
            x1: node.x + PV_NODE_W + (node.hasKids ? PV_TOGGLE_W : 0),
            y1: node.y + PV_NODE_H / 2,
            x2: child.x,
            y2: child.y + PV_NODE_H / 2,
          });
        });
      }
    });

    const maxX = Math.max(...placed.map((p) => p.x), 0) + PV_NODE_W;
    const maxY = Math.max(...placed.map((p) => p.y), 0) + PV_NODE_H;
    return { nodes: placed, edges, maxX, maxY };
  }, [tree, expanded]);

  // Infinite canvas pan/zoom (reuse PathView's logic)
  const containerRef = useRef(null);
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
            {layout.edges.map((edge, i) => {
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
                drill(x, y, () => { setSelectedNode(null); setView({ name: "solar", repo }); });
              }}
            />
          </div>
        )}
        {mode === "topology" && view.name === "solar" && (
          <div className="view" key={"solar-" + view.repo}>
            <SolarSystemView
              graph={graph}
              repo={view.repo}
              dims={{ w: dims.w, h: stageH }}
              onHome={goGalaxy}
              onBack={goGalaxy}
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
                onHome={goGalaxy}
                onBack={() => setView({ name: "solar", repo: ep.repo })}
                selectedNode={selectedNode}
                onSelectNode={(n) => setSelectedNode(n)}
                chatOpen={chatOpen}
              />
            </div>
          );
        })()}

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

function ProjectCard({ p, updates, onOpen, onAddRepo, onRescan, onDelete }) {
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
            {projects.map((p) => (
              <ProjectCard
                key={p.id}
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
                      placeholder="https://github.com/org/repo.git"
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

const rootEl = document.getElementById("root");
ReactDOM.createRoot(rootEl).render(<App />);
