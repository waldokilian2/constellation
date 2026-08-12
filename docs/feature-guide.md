# ⚡ Constellation — Feature Guide

**Map any microservice architecture from source. See every message flow. Answer any
"what breaks if I change this?" question — in seconds.**

Constellation is the codebase entry-point mapper for the microservices era. Point it at
your repos and it *deterministically* builds a living map of your architecture: every
entry point, every message channel, every sync HTTP call, every cross-service flow.
No code runs. No AI re-derives anything. No build step. Just source → map, instantly.

> **The one-liner to open with:** "Constellation reads your Java microservice source and
> draws the architecture — not the architecture you think you have, the architecture that
> actually exists, right now."

---

## Why it sells (the core story)

1. **Deterministic, not hallucinated.** Every relationship is extracted from source with
   a tree-sitter AST. AI is an *optional* advisory layer that reads the extracted graph —
   it never invents structure. When you present this, that's the trust card: *"every edge
   you see is read from code, so you can verify it."*
2. **Zero runtime, zero AI required.** No containers to run your services, no agents, no
   keys needed for the core analysis. `start.sh` → map. Runs offline, runs free.
3. **Confidence you can see.** Every call in a tree is tagged `EXTRACTED`, `INFERRED`,
   `AMBIGUOUS`, or `TRUNCATED`. We tell you *how sure we are* instead of silently guessing.
4. **One graph, three interfaces.** The same pure graph tools power the web UI, a REST
   API, and an MCP server — so humans *and* coding agents look at the same truth.

**Demo numbers to drop (bundled demo project):**
- 3 repos ingested, **10 entry points**, **5 producers**, 3 cross-repo links — analyzed in
  seconds.
- **7 end-to-end flows detected, 3 cross-repo** — including async *and* sync HTTP hops.

---

## Feature guide — talking points

### 1. Zero-friction project ingestion
**What it does:** Create a project from git URLs, a `local:` folder on disk, or a whole
**git-host org** (GitHub · GitLab · Bitbucket · Azure DevOps).

**Talking points:**
- **Git-host import** is the wow moment: paste one org link (`https://github.com/acme`),
  get a searchable, select-all repo picker, tick the repos you want, and Constellation
  clones + analyzes them *together* so cross-service links are found across the whole set.
- Add repos to an existing project anytime; the project is **re-analyzed as a whole** so
  nothing is missed.
- **Update** detects stale git-backed repos (`git pull`-able), **Rescan** re-runs the
  engine, **Delete** cleans up — full project lifecycle from the UI.
- Live SSE progress (`[clone]/[scan]/[link]`) with an always-visible bar — no frozen
  "is it stuck?" moments, even importing dozens of repos.
- Deterministic, stdlib-only, no LLM involvement. ("We clone, we parse, we link.")

### 2. Galaxy view — the architecture in one screen
**What it does:** Every repo is a cluster; every message channel is a curved connection.

**Talking points:**
- Async message edges *and* **sync HTTP edges** (solid mint) rendered together, so you see
  both how services talk over brokers and how they call each other directly.
- Channel labels on the edges (`order-events`, `shipment-events`) — the contract, visible.
- Per-repo entry-point counts + type badges; zoom/pan; click a repo to fly in.
- The **"N repos with gaps"** pill is a built-in health signal right on the overview.

### 3. Solar System view + the docked Consumes/Sends panel
**What it does:** Zoom into one repo; entry points orbit as **stars sized by call-tree
complexity, colored by type**. A docked channels panel shows everything that repo touches.

**Talking points:**
- Stars sized by complexity → the hot spots jump out immediately.
- Filter chips (REST / Kafka / RabbitMQ / Event…) to isolate one kind of entry point.
- The **Consumes / Sends panel** is the "what does this service touch?" answer:
  - **IN** cards — channels it consumes, the handler method, and the payload type.
  - **OUT** cards — channels it emits, with the *exact producer method* and **peers** it
    reaches ("to fulfillment-service, notification-service").
  - **REQUEST** cards — sync HTTP calls, with verb + path + the client method.
  - **"no producer found"** flags half-wired channels inline.
  - Every card deep-links (`↗`) straight to the flow or entry point — no hunting.

### 4. Path view — call trees with confidence
**What it does:** Click any entry point and see its full execution chain, depth-limited.

**Talking points:**
- BFS call tree from handler → repository → producer, with **source `file:line` on every
  node** and honest **confidence badges** (`EXTRACTED` / `INFERRED`).
- Expand/collapse per node; the tree is capped (depth 4, 50 nodes) so it never explodes.
- "Depth 3 · 9 nodes" metrics tell you how deep the rabbit hole goes before you jump in.

### 5. Detail panel — source + relationships
**What it does:** Click any node → source code with the exact line highlighted, plus who
calls it.

**Talking points:**
- No round-trip to the repo: the code is right there with line numbers and the relevant
  line highlighted.
- **"Called by"** gives instant impact context for the method you're staring at.

### 6. Flows — end-to-end traces, async and sync
**What it does:** Chains message hops *and* sync HTTP calls into complete business flows.

**Talking points:**
- Flow index with badges: `3 repos · 2 hops · cross-repo · ⚡ sync`.
- The trace renders a repo DAG: `POST /api/orders → order-service → order-events →
  fulfillment-service → shipment-events → notification-service`.
- **Sync HTTP round-trips render as request + response edges** — you see the call *and*
  what comes back.
- Click any repo node to jump to that entry point's call tree. Flows are the "show me the
  whole journey" feature — perfect for onboarding a new engineer.

### 7. Gaps — half-wired architecture, surfaced
**What it does:** Flags channels only half-wired: **orphan producers** and **orphan
consumers**, plus **dependency cycles**.

**Talking points:**
- "2 unconnected channels · 0 cycles" — a health check for your message topology.
- Each gap shows the channel, the service, the handler method, and `file:line`, and
  deep-links to the entry point.
- This is the "your producer never shipped / nobody reads this topic" finder.

### 8. Dead code — what you can delete
**What it does:** Finds **unreachable methods**, **thin handlers** (empty call trees), and
**isolated repos** (no cross-repo links).

**Talking points:**
- "4 unreachable of 78 methods · 74 reachable · 0 thin handlers" — a deletion backlog you
  can actually justify, because it's computed from the real call graph.
- Every item links to the source location.
- "What this means" explainers on every section — self-documenting.

### 9. AI Change Planner — plan changes, not just chat
**What it does:** A multi-turn chat that has the architecture as context and can *render*
its plan.

**Talking points:**
- The AI gets **structured graph context** (not raw source) and calls the same graph tools
  as everything else — so its answers are grounded in the extracted map.
- **`render_diagram`** tool: the AI drafts **Mermaid diagrams and HTML pages** that land
  live in the right-hand **Plan Preview** panel, with a remove/clear per diagram.
- **Reasoning blocks** (🧠) make the AI's thinking inspectable; tool-call steps are shown
  and expandable.
- Model picker with **free models only** (deepseek-v4-flash-free, hy3-free, …) — no paywall
  for a demo.
- **Past conversations** persist with message counts and are deletable — a real product
  feature, not a demo hack.

### 10. AI assistant everywhere
**What it does:** A global chat floating over any view, plus chat surfaces in the planner.

**Talking points:**
- Context = the architecture overview for the current project, so "which service consumes
  shipment-events?" gets a grounded answer.
- Streaming SSE; provider errors surface cleanly in the transcript (we saw a rate-limit
  error rendered as a clear message — the pipeline is honest end to end).
- Tool-use loop runs `search_code`, `get_channel_flow`, `trace_path`, etc. live.

### 11. Eleven graph tools — one source of truth
**What it does:** `search_code`, `get_node`, `find_callers`, `trace_path`,
`get_channel_flow`, `list_channels`, `get_source`, `get_architecture_overview`,
`find_orphans`, `find_cycles`, `find_dead_code` — pure, deterministic queries.

**Talking points:**
- The **same functions** power the web UI, the REST API, and the MCP server — no drift.
- `find_callers` = instant impact analysis ("if I change `save`, what entry points
  break?"). That single tool is a demo closer.

### 12. MCP server — your coding agent sees the architecture
**What it does:** Expose the whole thing to Claude Code / Cursor / any MCP agent.

**Talking points:**
- Built on the **official MCP SDK v2**; runs two ways:
  - **stdio** (`python -m engine.mcp_server`) for local agents, and
  - **Streamable HTTP at `/mcp`** served by the same FastAPI app — one URL, no subprocess.
- **Multi-project aware**: `list_projects`, then pass a `project` id to any tool; the
  default is the most recently updated project.
- An agent can now answer "trace the path from the REST endpoint to the DB write" with
  grounded graph data instead of guessing.

---

## The 3-interface story (diagram-worthy)

```
                Graph Tools (11 pure functions)
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
   Web UI (React)       REST API           MCP Server
   Galaxy→Solar→Path   /api/projects/*    stdio + /mcp
   Flows · Gaps ·       (for scripts,      (Claude Code,
   Dead code ·          debugging,         Cursor, agents)
   Planner · Chat       external use)
```

## Demo script (60–90 seconds)

1. **Landing:** "Pick a project to enter its galaxy" — two demo projects ready.
2. **Galaxy:** 3 services, channels `order-events` + `shipment-events`, sync HTTP edge
   visible, "1 repo with gaps" pill. *"This map is read from source — nothing is guessed."*
3. **Solar + Consumes/Sends:** click `order-service` → 6 stars; the panel shows
   "consumes 2 / sends 2 / 1 sync REQUEST". *"One glance: what does this service touch?"*
4. **Path:** click `createOrder` → call tree with `EXTRACTED` badges and `file:line`.
   *"Every call resolved to the real definition — confidence is displayed honestly."*
5. **Flows:** open **Create Order** → 3 repos, 2 hops. *"A full journey across services."*
6. **Gaps / Dead code:** flip tabs — *"we also tell you what's broken and what's dead."*
7. **Planner:** open a conversation; the AI drafts a Mermaid plan into the preview panel.
   *"AI as an advisor that reads the same map you see — never hallucinates structure."*

## On the roadmap (don't demo, but tease)

- **Python (FastAPI) support** — in progress, same deterministic pipeline.
- **TypeScript/Express, Go, C#** — planned.
- **Graph diff & compare** — snapshot your scans and overlay exactly what changed
  (green/amber/red) across every view.

---

*Constellation: the architecture you have, mapped from the code you trust.*
