/* ============================================================
   CONSTELLATION — derived graph analytics (pure, no state)
   ------------------------------------------------------------
   View-level summaries computed from the raw graph.json object.
   All functions are pure: graph in, plain data out. This is the
   single source of truth for service stats, roles, landscape
   summary, per-entry outbound channels, and flow detection —
   shared by every view so the same facts render identically
   everywhere (no more per-view re-derivation drift).
   ============================================================ */

// Entry types that act as synchronous API surfaces (request/response).
const API_TYPES = new Set([
  "rest-endpoint", "servlet", "soap-service", "graphql", "grpc-service", "websocket",
]);

// Entry types that act as asynchronous / event-driven triggers.
const EVENT_TYPES = new Set([
  "kafka-consumer", "rabbitmq-consumer", "jms-consumer", "sqs-consumer",
  "event-listener", "scheduled-task", "lifecycle", "main", "cloud-function",
]);

// Service role vocabulary — derived from the entry-point type mix.
export const ROLE_META = {
  gateway: { label: "API",      color: "#ff4d6d", glow: "rgba(255,77,109,.45)" },
  worker:  { label: "Worker",   color: "#ffd60a", glow: "rgba(255,214,10,.45)" },
  hybrid:  { label: "Hybrid",   color: "#a78bfa", glow: "rgba(167,139,250,.45)" },
  utility: { label: "Utility",  color: "#64748b", glow: "rgba(100,116,139,.45)" },
};

// ── Role derivation ──────────────────────────────────────────────
// A service whose entry points are ~all REST/servlet/etc. is an API
// gateway; all consumers/schedulers → event worker; a mix → hybrid.
export function deriveRole(typeCounts) {
  let api = 0, event = 0, total = 0;
  Object.entries(typeCounts || {}).forEach(([t, c]) => {
    total += c;
    if (API_TYPES.has(t)) api += c;
    else if (EVENT_TYPES.has(t)) event += c;
  });
  if (!total) return "utility";
  const apiShare = api / total;
  if (apiShare >= 0.7) return "gateway";
  if (event / total >= 0.7) return "worker";
  return "hybrid";
}

// ── Per-service stats ────────────────────────────────────────────
// Aggregates entry points, producers, complexity, channel in/out
// (with partner repos + methods), partner count, and role.
export function buildServiceStats(graph) {
  const repos = graph.repos || [];
  const entryPoints = graph.entry_points || [];
  const producers = graph.producers || [];
  const links = graph.cross_repo_links || [];
  const prodById = new Map(producers.map((p) => [p.id, p]));
  const epById = new Map(entryPoints.map((e) => [e.id, e]));

  const stats = {};
  repos.forEach((r) => {
    stats[r] = {
      name: r,
      epCount: 0,
      prodCount: 0,
      totalNodes: 0,
      maxDepth: 0,
      fileSet: new Set(),
      types: {},
      inbound: [],
      outbound: [],
      partnerSet: new Set(),
      flows: [],
    };
  });

  entryPoints.forEach((ep) => {
    const s = stats[ep.repo];
    if (!s) return;
    s.epCount += 1;
    s.types[ep.type] = (s.types[ep.type] || 0) + 1;
    const m = ep.metrics || {};
    s.totalNodes += m.total_nodes || 1;
    s.maxDepth = Math.max(s.maxDepth, m.depth || 0);
    if (ep.file) s.fileSet.add(ep.file);
  });

  producers.forEach((p) => {
    const s = stats[p.repo];
    if (!s) return;
    s.prodCount += 1;
    if (p.file) s.fileSet.add(p.file);
  });

  // Channel resolution: for every link, split producers/consumers by repo.
  links.forEach((link) => {
    const channel = link.channel || "";
    const kind = link.kind || "message";
    const verb = link.verb || "";
    const prods = (link.producers || []).map((id) => prodById.get(id)).filter(Boolean)
      .map((p) => ({ id: p.id, repo: p.repo, method: p.method, message_type: p.message_type || "" }));
    const cons = (link.consumers || []).map((id) => epById.get(id)).filter(Boolean)
      .map((e) => ({ id: e.id, repo: e.repo, method: e.method, message_type: e.message_type || "" }));
    const prodRepos = [...new Set(prods.map((p) => p.repo))];
    const consRepos = [...new Set(cons.map((c) => c.repo))];

    prodRepos.forEach((repo) => {
      const s = stats[repo];
      if (!s) return;
      s.outbound.push({ channel, kind, verb, fromRepos: [repo], toRepos: consRepos, producers: prods, consumers: cons });
      consRepos.forEach((c) => s.partnerSet.add(c));
    });
    consRepos.forEach((repo) => {
      const s = stats[repo];
      if (!s) return;
      s.inbound.push({ channel, kind, verb, fromRepos: prodRepos, toRepos: [repo], producers: prods, consumers: cons });
      prodRepos.forEach((c) => s.partnerSet.add(c));
    });
  });

  repos.forEach((r) => {
    const s = stats[r];
    s.filesCount = s.fileSet.size;
    delete s.fileSet;
    s.partnerCount = s.partnerSet.size;
    s.partners = [...s.partnerSet];
    delete s.partnerSet;
    s.channelCount = s.inbound.length + s.outbound.length;
    s.role = deriveRole(s.types);
  });

  return stats;
}

// ── Landscape summary ────────────────────────────────────────────
// Headline numbers + structural insight (hubs / isolated / sinks)
// for the galaxy view's story strip.
export function landscapeSummary(graph, stats, flows) {
  const repos = graph.repos || [];
  const links = graph.cross_repo_links || [];
  const entryPoints = graph.entry_points || [];
  const svc = repos.map((r) => stats[r]).filter(Boolean);

  // Hub = partner count >= 2 (with larger systems, the top third).
  const sortedPartners = svc.map((s) => s.partnerCount).sort((a, b) => b - a);
  const hubThreshold = sortedPartners.length >= 6
    ? Math.max(2, sortedPartners[Math.max(0, Math.floor(sortedPartners.length * 0.3))])
    : 2;
  const hubs = svc.filter((s) => s.partnerCount >= hubThreshold)
    .sort((a, b) => b.partnerCount - a.partnerCount);
  const orphans = svc.filter((s) => s.partnerCount === 0);
  const sinks = svc.filter((s) => s.inbound.length > 0 && s.outbound.length === 0);

  const syncCount = links.filter((l) => l.kind === "http").length;
  const asyncCount = links.length - syncCount;
  const crossRepoFlows = (flows || []).filter((f) => f.hasCrossRepo).length;

  const parts = [];
  if (hubs.length) {
    const top = hubs[0];
    parts.push(`Most connected: ${top.name} (${top.partnerCount} partner${top.partnerCount === 1 ? "" : "s"})`);
  }
  if (orphans.length) parts.push(`${orphans.length} isolated service${orphans.length === 1 ? "" : "s"}`);
  if (sinks.length) parts.push(`${sinks.length} sink${sinks.length === 1 ? "" : "s"}: ${sinks.map((s) => s.name).join(", ")}`);
  if (crossRepoFlows && flows.length) parts.push(`${crossRepoFlows} of ${flows.length} flows cross services`);

  return {
    repoCount: repos.length,
    flowCount: (flows || []).length,
    channelCount: links.length,
    entryPointCount: entryPoints.length,
    syncCount,
    asyncCount,
    hubThreshold,
    hubCount: hubs.length,
    orphanCount: orphans.length,
    sinkCount: sinks.length,
    insight: parts.length ? parts.join(" · ") : "",
  };
}

// ── Channel → flows index ────────────────────────────────────────
// Which end-to-end flows traverse a channel (origin channels, emitted
// channels, and every hop edge). Used by channel cards to offer
// one-click flow navigation.
export function flowsByChannel(flows) {
  const map = {};
  (flows || []).forEach((f) => {
    const seen = new Set();
    const add = (ch) => {
      if (!ch) return;
      if (!map[ch]) map[ch] = [];
      if (!map[ch].includes(f)) map[ch].push(f);
    };
    add(f.originChannel);
    const walk = (step) => {
      if (!step || seen.has(step.entryId)) return;
      seen.add(step.entryId);
      (step.publishesTo || []).forEach(add);
      (step.children || []).forEach((c) => {
        add(c.channel);
        walk(c.step);
      });
    };
    walk(f.step);
  });
  return map;
}

// ── Channel payload types ────────────────────────────────────────
// Human-meaningful message types on a channel. For HTTP links the
// producer's message_type field is actually the verb — skip it.
const HTTP_VERBS = new Set(["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]);

export function channelMessageTypes(ch) {
  if (!ch || ch.kind === "http") return [];
  const seen = new Set();
  [...(ch.producers || []), ...(ch.consumers || [])].forEach((x) => {
    const t = (x.message_type || "").trim();
    if (t && !HTTP_VERBS.has(t.toUpperCase()) && !seen.has(t)) seen.add(t);
  });
  return [...seen];
}

// ── Per-entry emitted channels ───────────────────────────────────
// Channels an entry point can reach: any producer invoked from its
// call tree (walked transitively, same reachability as flow
// detection) — enriched with kind/verb and the far-side methods.
// Used by the call-path exit strip and the service view's emits column.
export function entryEmits(graph, entryPoint) {
  if (!entryPoint) return [];
  const links = graph.cross_repo_links || [];
  const producers = graph.producers || [];
  const prodById = new Map(producers.map((p) => [p.id, p]));
  const epById = new Map((graph.entry_points || []).map((e) => [e.id, e]));

  // Channels produced by this repo, with the class.method each originates from.
  const repoChannels = [];
  links.forEach((link) => {
    (link.producers || []).forEach((pid) => {
      const p = prodById.get(pid);
      if (!p || p.repo !== entryPoint.repo) return;
      repoChannels.push({ channel: link.channel, target: pid.split(":")[1] || "" });
    });
  });

  // Every class.method reachable from the entry (root + call tree).
  const reachable = new Set();
  const rootMethod = [entryPoint.class_name, entryPoint.method].filter(Boolean).join(".");
  if (rootMethod) reachable.add(rootMethod);
  const tree = entryPoint.call_tree;
  if (tree && typeof tree === "object") {
    const stack = [tree];
    while (stack.length) {
      const node = stack.pop();
      if (!node) continue;
      if (typeof node.method === "string" && node.method) {
        reachable.add(node.method);
        const mName = node.method.split(".").pop();
        if (node.class_name && mName) reachable.add(node.class_name + "." + mName);
      }
      if (Array.isArray(node.children)) stack.push(...node.children);
    }
  }

  const channels = new Set();
  repoChannels.forEach((rc) => { if (reachable.has(rc.target)) channels.add(rc.channel); });

  return [...channels].map((channel) => {
    const link = links.find((l) => l.channel === channel);
    const producerObjs = (link.producers || []).map((id) => prodById.get(id)).filter(Boolean)
      .map((o) => ({ id: o.id, repo: o.repo, method: o.method, message_type: o.message_type || "" }));
    const consumerObjs = (link.consumers || []).map((id) => epById.get(id)).filter(Boolean)
      .map((c) => ({ repo: c.repo, method: c.method, message_type: c.message_type || "" }));
    return {
      channel,
      kind: (link && link.kind) || "message",
      verb: (link && link.verb) || "",
      fromRepos: [entryPoint.repo],
      toRepos: [...new Set(consumerObjs.map((c) => c.repo))],
      producers: producerObjs,
      consumers: consumerObjs,
    };
  });
}

// ── End-to-end flow detection ────────────────────────────────────
// Computes end-to-end flows from graph.json. A flow is a chain:
// origin (REST or external event) → [publishes → channel → consumer → ...]
// Each step is { repo, entryId, method, type, channel, publishesTo, children }.

const PUBLISH_KEYWORDS = ["convertandsend", "send", "publish", "emit"];

const ORIGIN_KINDS = {
  "scheduled-task":   { tag: "SCHEDULED", cls: "scheduled", noun: "scheduled job" },
  "event-listener":   { tag: "EVENT",     cls: "event",     noun: "event listener" },
  websocket:          { tag: "WEBSOCKET", cls: "websocket", noun: "websocket" },
  "kafka-consumer":   { tag: "KAFKA",     cls: "kafka",     noun: "Kafka topic" },
  "rabbitmq-consumer":{ tag: "RABBITMQ",  cls: "rabbitmq",  noun: "RabbitMQ queue" },
  "jms-consumer":     { tag: "JMS",       cls: "jms",       noun: "JMS queue" },
  "sqs-consumer":     { tag: "SQS",       cls: "sqs",       noun: "SQS queue" },
  main:               { tag: "MAIN",      cls: "main",      noun: "application bootstrap" },
  lifecycle:          { tag: "LIFECYCLE", cls: "lifecycle", noun: "lifecycle hook" },
  servlet:            { tag: "SERVLET",   cls: "servlet",   noun: "servlet endpoint" },
  "soap-service":     { tag: "SOAP",      cls: "soap",      noun: "SOAP operation" },
  graphql:            { tag: "GRAPHQL",   cls: "graphql",   noun: "GraphQL resolver" },
  "grpc-service":     { tag: "GRPC",      cls: "grpc",      noun: "gRPC service method" },
  "cloud-function":   { tag: "FUNCTION",  cls: "function",  noun: "cloud function" },
};

function originDescriptor(entry, isRest) {
  if (isRest) return { kind: "rest", tag: "REST", cls: "rest", noun: "REST endpoint" };
  const meta = ORIGIN_KINDS[entry.type] || { tag: "EXTERNAL", cls: "external", noun: "external event" };
  return { kind: entry.type || "external", tag: meta.tag, cls: meta.cls, noun: meta.noun };
}

export function detectFlows(graph) {
  const entries = graph.entry_points || [];
  const links = graph.cross_repo_links || [];

  const entryById = {};
  entries.forEach((e) => { entryById[e.id] = e; });

  const consumersByChannel = {};
  entries.forEach((e) => {
    if (e.type !== "rest-endpoint") {
      const ch = e.channel || "";
      if (ch) {
        if (!consumersByChannel[ch]) consumersByChannel[ch] = [];
        consumersByChannel[ch].push(e.id);
      }
    }
  });

  const internalChannels = new Set(links.map((l) => l.channel));

  // Channels an entry can emit to (same reachability as the exit strip).
  const emitsChannels = (entry) => entryEmits(graph, entry).map((ch) => ch.channel);

  function buildSteps(entry, visited) {
    const entryId = entry.id;
    if (visited.has(entryId)) return null;
    const nextVisited = new Set(visited);
    nextVisited.add(entryId);

    const channels = emitsChannels(entry);
    const consumers = [];

    channels.forEach((ch) => {
      const consumerIds = consumersByChannel[ch] || [];
      consumerIds.forEach((cid) => {
        if (cid === entryId) return;
        const ce = entryById[cid];
        if (!ce) return;
        if (ce.repo === entry.repo) return;
        consumers.push({ channel: ch, entryId: cid });
      });
    });

    const children = consumers.map((c) => {
      const childStep = buildSteps(entryById[c.entryId], nextVisited);
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
      children,
    };
  }

  function reposInStep(step, set) {
    set.add(step.repo);
    step.children.forEach((c) => reposInStep(c.step, set));
  }

  function stepDepth(step) {
    if (step.children.length === 0) return 1;
    return 1 + Math.max(...step.children.map((c) => stepDepth(c.step)));
  }

  const seenOrigins = new Set();
  const flows = [];

  entries.forEach((entry) => {
    const isRest = entry.type === "rest-endpoint";
    const isExternal = !isRest && !internalChannels.has(entry.channel || "");
    if (!isRest && !isExternal) return;
    if (seenOrigins.has(entry.id)) return;
    seenOrigins.add(entry.id);

    const step = buildSteps(entry, new Set());
    const repos = new Set();
    reposInStep(step, repos);
    const depth = stepDepth(step);
    const hasCrossRepo = repos.size > 1;

    const desc = originDescriptor(entry, isRest);
    let name, originLabel;
    if (isRest) {
      name = entry.method || entry.id.split(":").pop();
      name = name.replace(/([A-Z])/g, " $1").replace(/^./, (s) => s.toUpperCase()).trim();
      originLabel = ((entry.method_type || "POST") + " ") + (entry.channel || "");
    } else if (desc.kind === "scheduled-task") {
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
    });
  });

  return flows;
}
