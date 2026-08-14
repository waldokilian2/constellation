# AGENTS.md

Guidance for AI coding agents working in this repository. Keep this file up to date as the codebase evolves — it is the primary onboarding doc for agents.

## Project Overview

**Constellation** deterministically maps Java Spring Boot microservice architectures. It parses source code with the tree-sitter AST (no LLM, no runtime) to extract entry points (message handlers, REST endpoints, event listeners), message producers, per-entry-point call trees, and cross-repo links via shared queue/topic names. The result is a single `graph.json`. AI is an optional advisory layer that queries that graph.

The **core analysis is deterministic** — every relationship is read from source. AI never re-derives structure; it gets the extracted graph as structured context.

## Requirements & Dependencies

- **Python 3.10+** (uses `from __future__ import annotations`).
- Runtime deps are listed in `requirements.txt` (installed by `start.sh` / `start.bat` / the Docker image):
  - `tree-sitter`, `tree-sitter-java` (AST parsing)
  - `fastapi`, `uvicorn` (web server)
  - `mcp` (MCP stdio server — official SDK v2; pulls transitive deps like `anyio`/`httpx2`, used only by `engine/mcp_server.py`)
- **Test suite: stdlib-only.** `tests/run_tests.py` discovers and runs `tests/test_*.py`
  modules (no pytest, no deps — run `python tests/run_tests.py`). `tests/repos/` holds sample
  Java repos used as analysis input (`tests/repos/{order-service,fulfillment-service,notification-service}`
  plus `java-ee-*`).
- Frontend is **React 18 built with Vite** (esbuild transform, Rollup production build). Run `npm install` then `npm run build` to produce `web/dist/`. **`npm run dev`** starts Vite's dev server (HMR, port 5173) with API proxying to the Python backend on :8765.
- LLM calls use stdlib `urllib` only (no `requests`/`httpx`). Keep it that way — adding an HTTP dep is a regression against the "no extra deps" convention.

## Quick Commands

```bash
./start.sh                        # create venv, install deps, gen graph, build frontend, start server on :8765
./start.sh /path/to/repo1 ...     # analyze custom repos instead of test repos
start.bat                         # Windows equivalent

# Analyze without the server (activate .venv first)
source .venv/bin/activate
python -m engine.constellation /path/to/repo1 /path/to/repo2 --output output/graph.json

# Frontend (Vite)
npm install                       # install React + Vite (first time only)
npm run dev                       # Vite dev server with HMR on :5173 (API proxied to :8765)
npm run build                     # production build → web/dist/ (served by server.py)

# MCP stdio server (for Claude Code / Cursor)
python -m engine.mcp_server        # CONSTELLATION_GRAPH optionally points at a graph.json

# Mock backend for frontend-only work (no engine required)
python web/mock_server.py
```

The startup scripts only regenerate `output/graph.json` when it is missing or custom repo args are passed. `output/graph.json` is gitignored (as are `tests/repos/sample-spring-kafka-microservices/` and `web/dist/`).

## Repository Layout

```
engine/            # Deterministic analysis engine (Python)
  parser.py          # tree-sitter Java AST wrapper + query helpers
  entry_detector.py  # Spring annotation + producer scanner
  call_graph.py      # BFS call-tree builder (depth-limited)
  cross_repo.py      # queue/topic name matcher between repos
  context_builder.py # builds AI system prompts from graph data
  graph_tools.py     # 11 pure query functions (single source of truth)
  mcp_server.py      # MCP server (official `mcp` SDK v2, low-level Server): stdio via CLI + Streamable HTTP /mcp via server.py
  models.py          # dataclasses for the graph
  paths.py           # safe, root-confined source path resolution
  project_store.py   # multi-project index, git-clone ingestion, engine-run w/ log capture
  constellation.py   # CLI orchestrator + ConstellationEngine
server.py            # FastAPI app: REST + AI proxy + static frontend
web/                 # React 18 + Vite frontend
  index.html         # Vite entry (loads /src/main.jsx as ES module)
  src/
    main.jsx         # entry point: createRoot(<App />)
    app.jsx          # Projects → Galaxy → Solar System → Path → Detail (~3600 lines)
    galaxyLayout.js  # deterministic Galaxy layout (components + force relaxation,
                     # post-fit resolver incl. island-vs-edge-curve clearance,
                     # placeEdgePills = coordinated pill placement shared with the
                     # renderer — pills render exactly where the layout placed them,
                     # no deps)
    styles.css       # SVG + CSS visualization styles
  dist/              # Vite build output (gitignored, created by npm run build)
  mock_server.py     # static in-memory backend for frontend dev
tests/repos/         # sample Java microservices (input data, not tests)
  order-service / fulfillment-service / notification-service / analytics-service
  payment-service / inventory-service / shipping-service / user-service /
  recommendation-service / reporting-service / legacy-monolith
                     #   Spring Boot demo repos (seeded as the "Spring Boot" project).
                     #   The 8 services beyond the original three form a 9-repo
                     #   connected constellation (order-events hub, payment/shipping
                     #   chains, user<->recommendation + order<->analytics +
                     #   order<->inventory cycles, Feign/RestTemplate HTTP links)
                     #   plus 2 isolated islands: reporting-service (scheduled +
                     #   REST, no channels) and legacy-monolith (orphan "legacy-jobs"
                     #   JMS producer, dead LegacyReport).
                     #   Spring Boot demo repos (seeded as the "Spring Boot" project).
                     #   analytics-service is the Tier 1/2 + gaps/dead-code fixture: it
                     #   exercises GraphQL, gRPC, SOAP, Servlet, Cloud Function, lifecycle,
                     #   main, SQS, WebSocket, JMS/event/Pulsar/NATS/StreamBridge producers,
                     #   Camel routes, WebClient/Apache/async HTTP clients, a deliberate
                     #   order<->analytics cycle, orphan channels, and LegacyReportFormatter
                     #   dead code + a thin /ping handler.
  java-ee-order-service / java-ee-fulfillment-service / java-ee-notification-service
                     #   Java EE / Jakarta annotations demo (JAX-RS, MDB, CDI, EJB,
                     #   WebSocket, @Scheduled, @MessageMapping) across THREE repos
                     #   with cross-repo links (order-events, shipment-events) —
                     #   seeded as "Java EE"
output/              # graphs + project store (gitignored)
  graph.json         # legacy single-graph (start.sh seed; imported as "Spring Boot")
  graph-java-ee.json # Java EE test graph (start.sh seed; imported as "Java EE")
  projects.json      # multi-project index
  projects/<pid>/    # per-project: graph.json + cloned repos/
start.sh / start.bat # bootstrap + server launchers
```

## How Data Flows

The app is **multi-project**: each project is an isolated collection of repos with its own graph (`output/projects/<pid>/graph.json`); projects have no relationship to each other. On first load, pre-existing legacy graphs are imported once as named projects — `output/graph.json` → "Spring Boot" and `output/graph-java-ee.json` → "Java EE" (`ProjectStore.ensure_legacy_seed`). The seed **copies** each recorded test repo into `output/projects/<pid>/repos/` (same per-project structure as URL-cloned repos) and rewrites the graph's `repo_roots` to point at the copies, so source reads resolve through the per-project roots.

1. **Ingest** — the UI creates a project (`POST /api/projects`, git URLs) or adds repos to one (`POST /api/projects/{pid}/repos`). `engine/project_store.py` shallow-clones each repo into `output/projects/<pid>/repos/`, then re-runs the engine over the project's full repo set (cross-repo linking needs all repos together), streaming `[clone]/[scan]/[link]` progress over SSE. A `local:<path>` repo spec registers an existing directory in place (git-backed ones are tracked for updates via `check_updates`/`pull_repos`). Projects are renamed metadata-only via `PATCH /api/projects/{pid}` (`ProjectStore.rename`) — the id and on-disk layout never change; the UI exposes it as a ✎ Rename item in each card's Manage menu.
2. **Parse** — `entry_detector.py` scans `*.java` files (skipping `/test/` and `*Test*` paths) via `ast_parser.py` (tree-sitter wrapper) + `languages/java_ast.py` helpers, delegating to `frameworks/` handlers for annotated methods and producer call patterns.
3. **Index** — methods are indexed for import-aware call resolution.
4. **Call trees** — `call_graph.py` builds a depth-limited (MAX_DEPTH=4, MAX_NODES=50) BFS tree per entry point, resolving each invocation to a definition and tagging confidence. The trivial-call filter (`_is_trivial`) drops get/set/is/has-prefixed names as POJO accessors ONLY at arity 0 — `getOrderStatus(id)` is a real business call and is traced (the old name-only check silently truncated such trees).
5. **Cross-repo links (two passes)** — `cross_repo.py` pass 1 matches async producers→consumers by exact channel name, **but only broker consumer types** (`kafka`/`rabbitmq`/`jms`/`sqs-consumer`, `event-listener`, `websocket`) participate — non-broker entry kinds (REST/SOAP/GraphQL/gRPC/servlet/lifecycle/main/cloud-function/scheduled) use synthetic/semantic channels and are excluded so a GraphQL op named "orders" can't collide with a Kafka topic named "orders". Pass 2 matches `HTTP_CALL` producers (Feign/RestTemplate/WebClient/HttpClient/Apache HttpComponents/async-http-client/etc.) to REST endpoints by **normalized path template** (`/api/orders/123` ≡ `/api/orders/{id}`), cross-repo only, producing `kind:"http"` links with an HTTP `verb`. HTTP links render as solid mint edges in the galaxy view; the frontend flow detector (`detectFlows` in `web/src/app.jsx`) chains sync HTTP hops into flows too — REST endpoints are indexed as HTTP consumers, so a flow can show both async message hops (cyan) and sync request hops (mint).
6. **Serialize** — `constellation.py` assembles a `ConstellationGraph` → the project's `graph.json` with `repo_roots` for safe path resolution. Producer entries carry `message_type` (message payload type — for `http-call` producers this is the HTTP verb) and `response_type` (the Feign client method's return type).
7. **Serve** — `server.py` serves each project's graph via **project-scoped** REST + AI endpoints (`/api/projects/{pid}/...`); the single legacy `/api/graph` returns the first ready project. `mcp_server.py` is **multi-project aware** (backed by `ProjectStore`): a `list_projects` tool lists every project, and each graph tool takes an optional `project` arg dispatching to that project's graph (default = most recently updated ready project). It runs two ways: **stdio** (`python -m engine.mcp_server`, the local/non-docker path) and **Streamable HTTP** at `/mcp` (mounted by `server.py` via `mount_streamable_http(app, store=PROJECT_STORE)`, so the docker container serves MCP over the same :8765 port with a URL). Both transports share the same resolver.

## Confidence Tags (important for correctness)

Every call-tree node carries a confidence value:
- **`EXTRACTED`** — call resolved to a concrete definition in the codebase.
- **`INFERRED`** — call name matched but target couldn't be confirmed (cross-file resolution without imports).
- **`AMBIGUOUS`** — multiple possible targets, first one chosen.
- **`TRUNCATED`** — node limit hit during traversal.

These are set in `call_graph.py` (`_resolve_call`, `_is_trivial`, `_expand_node`). Do not silently change the semantics of `EXTRACTED`/`INFERRED` — the AI prompts and UI both display them.

## Graph Tools (single source of truth)

`engine/graph_tools.py` defines **11 pure functions** (`TOOL_DEFINITIONS`) consumed by three interfaces: the MCP server, the REST API (`/api/projects/{pid}/tools/*`), and the web AI tool-loop. `execute_tool(graph, name, args)` is the dispatcher and `_filter_args` validates args against the schema.

The 11 tools: `search_code`, `get_node`, `find_callers`, `trace_path`, `get_channel_flow`, `list_channels`, `get_source`, `get_architecture_overview`, `find_orphans`, `find_cycles`, `find_dead_code`.

- **Graph-query tools** (pure): `search_code`, `get_node`, `find_callers`, `trace_path`, `get_channel_flow`, `list_channels`, `get_source`, `get_architecture_overview`, `find_orphans`, `find_cycles`, `find_dead_code`. These are pure data-in/data-out over the graph dict — register them in `TOOL_DEFINITIONS` **and** the `execute_tool` dispatch table.
- **Signalling / stateful tools**: `task_complete` (a passthrough the server's tool loop inspects to decide stop-vs-continue) and `render_diagram` (planner-only; drives the right-side preview panel and is persisted on the conversation). These are **not** in the `execute_tool` dispatch table — they are special-cased in the streaming layer (`server._stream_llm_events`), which is the only place with the conversation id needed to persist panel diagrams via `ConversationStore`. `task_complete` is a pure echo; `render_diagram` is intentionally stateful.

**Rule:** if you add a *graph-query* tool, register it in `TOOL_DEFINITIONS` **and** the `execute_tool` dispatch table (plus `_filter_args`, which is schema-driven). If you add a *planner-only / stateful* tool, add it to `TOOL_DEFINITIONS` and special-case its execution in `_stream_llm_events` — and keep it out of `get_tool_definitions()` unless `include_planner_tools=True`.

`get_tool_definitions(include_planner_tools=False)` returns the always-applicable set; the planner chat passes `include_planner_tools=True` to also expose `render_diagram`. The MCP server and global topology chat never receive planner-only tools.

### Mermaid validation (render_diagram)

`engine/mermaid_validator.py` + `engine/mermaid_validate.mjs` validate Mermaid **at tool-call time**: the Python wrapper shells out to Node (jsdom + the bundled `mermaid` package, already in `node_modules`), runs `mermaid.parse`, and the streaming loop returns the parse error **in the `render_diagram` tool result** (`ok: false`, diagram NOT stored) so the AI self-corrects in the same turn. If Node/mermaid/jsdom is unavailable it degrades to accept — the frontend's render-time fallback (`repairMermaid` in `web/src/mermaid.jsx`) is the last resort. Env override: `CONSTELLATION_NODE_BIN`.

## Conventions

- **Python:** module-level docstrings, `from __future__ import annotations`, type hints, and `# ── Section ──` comment separators. Dataclasses for the graph model (`models.py`). Follow these when editing engine/server code.
- **Style:** section-header comments use the `# ── ... ──` pattern (see any `engine/*.py`). Docstrings are used liberally — keep them.
- **Build step:** frontend edits require a Vite rebuild (`npm run build`) to be visible in the production server. For rapid iteration with HMR, use `npm run dev` (Vite dev server on :5173, proxies API to :8765). The source lives in `web/src/` — `app.jsx` (components/logic) + `styles.css` + `main.jsx` (entry point). React, `marked`, and all deps are imported as ES modules (no global CDN scripts).
- **Security:** source reads are confined to recorded `repo_roots` (`engine/paths.py`, `server.py:_resolve_source_path`). Keep arbitrary-file-read surfaces closed. The graph stores **repo-relative** paths for portability.
- **Env vars:** `CONSTELLATION_PORT` (8765), `CONSTELLATION_GRAPH` (graph path for MCP), `OPENCODE_API_KEY`/`OPENCODE_BASE_URL`/`OPENCODE_MODEL` (AI chat; **Zen by default** — `OPENCODE_BASE_URL=https://opencode.ai/zen/v1`, model `deepseek-v4-flash-free`; `OPENAI_*` accepted as aliases). `server.py` loads a committed `.env` template at startup; the start scripts mark it `skip-worktree` so local secrets are never committed. Only free models (ids ending in `-free`) are exposed via `/api/ai/models`.

## Known Gotchas & Latent Bugs

- Previously `server.py` referenced undefined names (`run_in_threadpool`, `Request`, `all_models`, `_API_TOKEN`, `_USER_AGENT`, `queue`/`threading`/`asyncio`) that made `/api/analyze`, `/api/ai/models`, auth, and streaming fail at call time. These are **fixed**: the imports/consts are defined, `/api/analyze` is replaced by the streaming ingest endpoints, and `ai_models` now parses the real `/models` response (filtering to ids ending in `-free`) and falls back to `FREE_MODELS` when the provider can't be reached. AI is **OpenAI-compatible only** (Zen by default); the Anthropic branch was removed. `CONSTELLATION_API_TOKEN` auth is still optional (open when unset) and is documented in code but **not** in the README's env-var table.
- All graph-dependent endpoints are **project-scoped** under `/api/projects/{pid}/...` (graph, source, tools, conversations). The flat `/api/graph`, `/api/source`, `/api/tools/*`, `/api/ai/*` routes no longer exist; only `/api/ai/models` and `/api/graph` (legacy alias → first ready project) remain non-scoped. The frontend builds these via `projPath(pid, rest)` in `web/src/app.jsx`. AI chat is **streaming-only**: every message goes through `POST /api/projects/{pid}/conversations/{cid}/chat/stream` — there is no non-streaming chat variant.
- The MCP server loads `graph.json` once at startup; restart to pick up graph changes.
- Call resolution is import-aware for single types plus interface→impl and local-variable/parameter receivers (see `engine/java_index.py`), and resolves methods up a **multi-level supertype chain** via `find_methods_in_hierarchy` (used as a fallback in `resolve_call`). It still can produce false positives when a simple name maps to multiple unimported classes across repos — see the "Limitations" section of `README.md`. Do not silently change the `EXTRACTED`/`INFERRED`/`AMBIGUOUS` semantics that the tests and UI depend on.
- Chat persistence: `_stream_llm_events` previously streamed the model's **final text-only reply** (no tool calls) without appending it to the conversation history, so prose answers vanished after refresh. **Fixed**: the no-tools branch now appends `content_acc` to `full_messages` before yielding done. Keep this: every assistant turn must land in `full_messages` (persisted by `replace_messages`), not just tool-call turns.
- **Seed repos are one-time copies.** `ensure_legacy_seed` copies `tests/repos/*` into `output/projects/<pid>/repos/` once at import. If a fixture under `tests/repos/` is later edited, the project's clones are NOT re-synced — a `rescan` re-analyses the **stale clone**, so call-tree/dead-code results silently reflect old source. Symptom: entry points show as thin/no-op or unreachable even after editing the fixtures. Fix: copy the updated files into `output/projects/<pid>/repos/` (or delete + re-import the project) before rescanning. `rescan?pull=true` only re-fetches git-remote repos, not local seeds.
- **Conversations are kind-scoped** (`engine/conversation_store.py`): each `Conversation` carries a `kind` of `"chat"` (the per-page assistant, `GlobalChat`/`useConversationChat({planner:false})`) or `"planner"` (the AI Change Planner, `changePlanner.jsx`/`useConversationChat({planner:true})`). The two surfaces have **different system prompts** (`_build_chat_prompt` → `build_planner_prompt` vs `_build_ai_context`) and must **not share history**. `create`/`list`/`get_or_create_default` are scoped by `kind`; the hook derives `kind` 1:1 from its `planner` flag and passes `?kind=` / `{kind}` on list + create. The planner-only `render_diagram` state lives on planner conversations only. Legacy conversation files without a `kind` field load as `"chat"`.

## Roadmap Context (README)

- Shipped (PR #9, 2026-08-08): **sync HTTP inter-service call detection** — `HTTP_CALL`
  producer type, two-pass linker with normalized-path matching, solid mint galaxy edges,
  same-pair edge fan-out, REST entry points carry their real HTTP verb (`method_type`,
  e.g. `GET /api/fulfillment/status/{orderId}`).
- Shipped: **broad Java framework coverage** — entry points beyond Spring/Java EE:
  `main()`, lifecycle hooks (`@PostConstruct`, `CommandLineRunner`/`ApplicationRunner`/
  `InitializingBean`), Servlet API (`@WebServlet`/`@WebFilter`), SOAP (JAX-WS
  `@WebService`/`@WebMethod`), Spring for GraphQL (`@QueryMapping`/…), gRPC
  (`extends *ImplBase`), Spring Cloud Function (`@Bean` `Function`/`Supplier`/`Consumer`);
  producers for Pulsar (`PulsarTemplate`) and NATS (`Connection.publish`); **Apache Camel**
  `RouteBuilder` `from()`/`to()` routes (broker schemes → real entry/producer types, linked
  cross-repo); STOMP return-side `@SendTo` producers; Apache HttpComponents (`execute`)
  and async-http-client (`prepare*`) HTTP clients. New `EntryPointType` values: `servlet`,
  `soap-service`, `graphql`, `grpc-service`, `lifecycle`, `main`, `cloud-function`; new
  `ProducerType` values: `pulsar-producer`, `nats-producer`. Frontend `TYPE_META`/
  `ORIGIN_KINDS` updated; unmapped types still fall back gracefully.
- Parser fixes that shipped with the above: `scoped_type_identifier` (nested types like
  `Outer.Inner`) is now captured in supertypes, field/param/return/local types; chained
  method calls (`from(x).to(y)`) parse correctly in `parse_method_invocation`.
- Shipped: `.env` template (committed, auto `skip-worktree` via the start scripts) + Zen-by-default AI config (OpenAI-compatible only; free-model filtering on `/api/ai/models`). In progress: Python (FastAPI) support.
- Planned: TypeScript/Express, Go, C#; dynamic queue-name resolution; Anthropic tool-use; SSE streaming.

When implementing anything on the roadmap, prefer extending the existing deterministic pipeline (new detector entries, new tools) and keep the "no LLM in the core" principle intact.
