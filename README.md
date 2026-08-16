<p align="center">
  <img src="assets/title-banner.png" width="100%" alt="Constellation">
</p>

**Deterministically map JVM microservice architectures. Find every entry point, trace execution paths, and see how messages flow between services — all from static analysis, zero AI required.**

Constellation parses source code with tree-sitter ASTs to build a complete map of your architecture. No code runs, no LLMs are needed for the core analysis — every relationship is extracted directly from the source.

AI is an optional advisory layer on top: it gets structured graph context (not raw source) so it can answer questions about the system accurately.

---

## Quick Start

```bash
# Linux / macOS
./start.sh

# Windows
start.bat

# Or analyze your own repos
./start.sh /path/to/repo1 /path/to/repo2
```

Open **http://localhost:8765** in your browser.

The startup script will:
1. Create a Python virtual environment and install dependencies (if missing)
2. Generate the demo graphs from the bundled test repos (if missing)
3. Build the frontend (`npm install && npm run build`, if `web/dist/` is missing or stale)
4. Start the server

On first load the server seeds two demo projects:
- **Spring Boot** — `order-service`, `fulfillment-service`, `notification-service`, `analytics-service` (REST, RabbitMQ, Kafka, gRPC, GraphQL, scheduled tasks, and more)
- **Java EE** — `java-ee-order-service`, `java-ee-fulfillment-service`, `java-ee-notification-service` (JAX-RS, JMS MDB, CDI, EJB, WebSocket, Spring `@Scheduled`/`@MessageMapping`) with real cross-repo links

### Docker (all-in-one)

Prefer containers? The AIO image bundles the engine, server, and a pre-built UI — no Python or Node needed on the host:

```bash
docker compose up           # http://localhost:8765
```

- Works offline (React/marked are bundled into the built UI; Google Fonts fall back to system fonts).
- Graphs and cloned repos persist in the `constellation-output` named volume.
- Optional AI: `OPENCODE_API_KEY=sk-... docker compose up`.

#### Analyze repos you've already cloned

The container has its own filesystem, so a repo on your host isn't visible to it until you mount it. Constellation supports `local:<path>` repos (the same `local:` scheme the UI's "Add repo" field accepts), so:

1. Add a bind mount in `docker-compose.yml` (under `volumes`), pointing your host repos dir at `/repos` inside the container:
   ```yaml
   - ./path/to/your/repos:/repos:ro
   ```
2. `docker compose up`, then add a repo from the UI's "Add repo" field using the **absolute in-container path**:
   ```
   local:/repos/my-service
   ```
   …or via the API:
   ```bash
   curl -X POST http://localhost:8765/api/projects \
     -H "Content-Type: application/json" \
     -d '{"name":"My local stack","repos":["local:/repos/my-service"]}'
   ```

The `:ro` mount keeps your source read-only; drop it if you want Constellation to `git pull` updates into a git-backed checkout. For a one-off analysis with no server, run the engine directly over a mounted repo:

```bash
docker compose run --rm --entrypoint python constellation \
  -m engine.constellation /repos/my-service --output output/graph.json
```

### Requirements

- **Python 3.10+**
- **Node.js 18+** (for the Vite frontend build)
- **A modern browser** (for the web UI)
- **Optional:** an `OPENCODE_API_KEY` for AI chat (defaults to Zen, any OpenAI-compatible endpoint works). Copy `.env.example` to `.env` (git-ignored) and fill in your keys — the start scripts do this automatically on first run.

---

## What It Does

### Ingestion

Point Constellation at any mix of repos:

- **Git URLs** — cloned server-side; browse an org or user's repo list straight from GitHub, GitLab, or Bitbucket (`/api/remotes/repos`)
- **Local paths** — `local:/path/to/repo`, analyzed in place

Projects are multi-repo by design: repos are scanned independently, then linked through their message channels.

### Entry Point Detection

Framework-specific annotation and interface-contract scanning:

| Family | Detects |
|--------|---------|
| **Spring** | `@GetMapping`/`@PostMapping`/… REST endpoints, `@RabbitListener`, `@KafkaListener`, `@JmsListener`, `@SqsListener`, `@PulsarListener`, `@RocketMQMessageListener`, `@StreamListener`, `@EventListener`, `@Scheduled`, `@MessageMapping`/`@SubscribeMapping` (STOMP) |
| **Jakarta / Java EE** | JAX-RS (`@Path` + verbs), JMS MDB (`@MessageDriven`), CDI (`@Observes`), EJB timers (`@Schedule`/`@Schedules`/`@Timeout`), WebSocket (`@ServerEndpoint`) |
| **Interface contracts** | `public static void main`, lifecycle hooks (`@PostConstruct`, `CommandLineRunner`, `ApplicationRunner`, `InitializingBean`, …), Servlets & filters (`@WebServlet`/`@WebFilter`), SOAP (`@WebService`/`@WebMethod`), Spring for GraphQL (`@QueryMapping`/`@MutationMapping`/…), gRPC (`*ImplBase` service methods), Spring Cloud Function (`@Bean Function/Supplier/Consumer`) |
| **Apache Camel** | `RouteBuilder` DSL — `from(...)`/`to(...)` URIs, broker scheme selects the channel type |
| **Axon Framework** | CQRS/ES handlers routed by payload type (commands, events, queries) |
| **Quarkus / Micronaut** | SmallRye Reactive Messaging (`@Incoming`/`@Outgoing`), Micronaut listener annotations |
| **Custom bus facades** | `@MessageHandler`-style classes dispatched by payload type (`bus.send(...)`) |

Channels are resolved through the symbol index: string literals, `Class.CONST` / bare constant references, `${...}` placeholders (from `application.properties`/`.yml`), and `#{...}` SpEL (dynamic — preserved for display). Array arguments (`topics = {"a", "b"}`) produce one entry point per element.

### Producer Detection

Message producers are matched by **declared field type** of the receiver (not variable names — a plain `template.send(...)` doesn't false-match):

| Declared type / pattern | Producing methods | Type |
|-------------------------|-------------------|------|
| `KafkaTemplate` | `send(...)` | Kafka |
| `RabbitTemplate` / `AmqpTemplate` | `convertAndSend(...)`, `send(...)` | RabbitMQ |
| `JmsTemplate` | `convertAndSend(...)`, `send(...)` | JMS |
| `SqsTemplate` / `SqsClient` / `AmazonSQS` | `send(...)` | AWS SQS |
| `SnsTemplate` / `SnsClient` / `AmazonSNS` | `publish(...)` | AWS SNS |
| `PulsarTemplate` | `send(...)` | Pulsar |
| `NatsTemplate`-style clients | `publish(...)` | NATS |
| `ApplicationEventPublisher` | `publishEvent(...)` | Spring events |
| `StreamBridge` | `send(...)` | Cloud Stream (broker-agnostic) |
| `@SendTo` (STOMP) | handler methods | WebSocket replies |
| gRPC stubs (`*BlockingStub`/`*FutureStub`/`*Stub`) | service calls | gRPC |
| HTTP clients (Feign, `RestTemplate`, `WebClient`, …) | request methods | HTTP calls |
| Custom bus facade | `bus.send(payload)` | in-house bus |

### Cross-Repo Message Flow

When Repo A produces to `"order-events"` and Repo B consumes from `"order-events"`, Constellation links them. This is deterministic string matching on queue/topic names across parsed repos — no AI, no inference.

### Call Tree Extraction

For each entry point, Constellation builds a call tree by:
1. Parsing the handler method body with tree-sitter ASTs
2. Finding all method invocations
3. Resolving each call to its definition in the codebase
4. Recursing, depth-limited with cycle prevention
5. Marking each node with confidence: `EXTRACTED` (resolved) or `INFERRED` (unresolved)

### Graph Diff

Snapshot a project's graph and compare later runs (`/api/projects/{pid}/diff`, the `diff_graphs` tool, and the UI's compare toggle): added/removed/changed entry points and producers, with per-entry-point change summaries and a legend — so you can see exactly what an integration changed.

### AI Integration (Optional)

When an API key is configured, the web UI provides conversational AI:
- **Topology chat** — multi-turn conversations with a structured system prompt (architecture overview, call tree, cross-repo connections) and graph-tool access
- **Change planner** — a planning chat that can render validated Mermaid diagrams alongside the graph, with automatic repair for invalid syntax

The key is proxied server-side and never reaches the frontend.

---

## The Web UI

One screen, five modes (plus the compare overlay):

| Mode | What You See |
|------|-------------|
| **Topology** | Galaxy view (repos as clusters, message channels as curved links) → Solar System (entry points as stars, sized by complexity, colored by type) → Path (full call tree for one entry point) |
| **Flows** | Message-flow index and per-channel flow traces (producer → channel → consumer chains) |
| **Planner** | AI change-planning chat with Mermaid diagram preview |
| **Boards** | GitHub Projects kanban sync — connect a project board, two-way issue/card sync, comment and create cards from Constellation |
| **Dead Code** | Unreachable methods, thin handlers, isolated repos, half-wired channels (orphans), and repo-level cycles |

The detail panel shows source code with line highlighting, relationships, and the AI chat window. A compare pill in the header toggles baseline diff highlighting across the topology views.

## Architecture

```
constellation/
├── engine/                        # Deterministic analysis engine
│   ├── constellation.py           #   CLI orchestrator
│   ├── ast_parser.py              #   tree-sitter AST wrapper + structural helpers
│   ├── languages/                 #   Language specs + registry (Java today, extensible)
│   │   ├── java_ast.py            #     Java-specific AST helpers
│   │   └── specs/java.py          #     Java language spec
│   ├── frameworks/                #   Framework detectors (pluggable handlers)
│   │   ├── spring.py              #     Spring + Micronaut annotations
│   │   ├── jakarta.py             #     Java EE / Jakarta (JAX-RS, MDB, CDI, EJB, WS)
│   │   ├── camel.py               #     Apache Camel RouteBuilder DSL
│   │   ├── axon.py                #     Axon CQRS/ES handlers
│   │   ├── reactive.py            #     Quarkus / MicroProfile reactive messaging
│   │   ├── messagebus.py          #     Custom bus facade handlers
│   │   └── extra.py               #     main, lifecycle, servlets, SOAP, GraphQL, gRPC, cloud functions
│   ├── producers/jvm.py           #   Producer detection by declared receiver type
│   ├── symbol_index.py            #   Repo-wide symbol index (type-aware resolution)
│   ├── entry_detector.py          #   Entry-point scan driver
│   ├── call_graph.py              #   BFS call tree builder
│   ├── cross_repo.py              #   Queue/topic name matcher
│   ├── http_paths.py              #   REST path extraction
│   ├── git_hosts.py               #   GitHub / GitLab / Bitbucket repo browsing + clone URLs
│   ├── graph_tools.py             #   Graph query functions (shared by all interfaces)
│   ├── context_builder.py         #   Builds AI system prompts from graph data
│   ├── mermaid_validator.py       #   Mermaid diagram validation + repair (with .mjs checker)
│   ├── conversation_store.py      #   Persisted AI conversations + diagrams
│   ├── boards/                    #   GitHub Projects sync via the official GitHub MCP server
│   ├── project_store.py           #   Multi-project index, git-clone ingestion
│   ├── mcp_server.py              #   MCP server (stdio + streamable HTTP)
│   ├── models.py                  #   Data classes
│   └── paths.py                   #   Safe, root-confined source path resolution
│
├── server.py                      # FastAPI web server + REST API + MCP mount
├── win_accept_resilience.py       # Windows selector-loop fix (CPython #93821)
├── web/                           # React 18 + Vite frontend
│   └── src/
│       ├── app.jsx                #   All views/modes (topology, flows, planner, boards, dead)
│       ├── useConversationChat.js #   Streaming chat hook (SSE + tool-use loop)
│       ├── changePlanner.jsx      #   Planner mode + diagram preview
│       ├── galaxyLayout.js        #   Galaxy/solar layout
│       ├── flowLayout.js          #   Flow-view layout
│       └── mermaidRepair.js       #   Client-side diagram repair
│
├── tests/                         # Unit tests + bundled sample repos
│   ├── run_tests.py               #   stdlib test runner
│   ├── test_*.py                  #   Engine + server regression tests
│   ├── repos/                     #   14 sample Java repos (Spring Boot + Java EE demos)
│   └── e2e/                       #   Playwright end-to-end specs
│
├── scripts/flow-harness.mjs       #   Flow-layout dev harness
├── docker/                        #   Dockerfile + entrypoint
├── start.sh / start.bat           #   Startup scripts
└── .env.example                   #   Configuration template (copy to .env)
```

Generated graphs, the project store, and cloned repos live in `output/` (git-ignored, a Docker volume in the container).

---

## The Three Interfaces

The graph tools are pure functions in `engine/graph_tools.py`. They're exposed three ways:

```
                Graph Tools (pure functions)
                          │
         ┌────────────────┼────────────────┐
         ▼                ▼                ▼
   MCP Server        REST API         Web AI Chat
 (stdio + HTTP)   (/api/projects/…)   (tool-use loop)
         │                │                │
         ▼                ▼                ▼
  Claude Code      Debugging /        Browser
  Cursor, …        External use       chat + planner
```

### 1. Web UI

See [The Web UI](#the-web-ui) above.

### 2. REST API

All graph-dependent endpoints are **project-scoped** under `/api/projects/{pid}/...`:

```bash
# List projects
curl http://localhost:8765/api/projects

# List tools for a project (pid comes from the projects list)
curl http://localhost:8765/api/projects/<pid>/tools

# Search the codebase
curl "http://localhost:8765/api/projects/<pid>/tools/search?q=OrderService"

# Find all callers of a method
curl "http://localhost:8765/api/projects/<pid>/tools/callers?method=save"

# Get message channel flow
curl "http://localhost:8765/api/projects/<pid>/tools/channel/order-events"

# Architecture overview
curl http://localhost:8765/api/projects/<pid>/tools/overview

# Trace a path between two methods
curl "http://localhost:8765/api/projects/<pid>/tools/trace?from_method=createOrder&to_method=save"

# Execute any tool via POST
curl -X POST http://localhost:8765/api/projects/<pid>/tools/find_callers \
  -H "Content-Type: application/json" \
  -d '{"method_name": "save"}'
```

Projects are created through the same API (the UI uses these endpoints — git URLs are cloned server-side):

```bash
# Create a project from one or more git URLs (streams [clone]/[scan]/[link] progress)
curl -X POST http://localhost:8765/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "My Stack", "repos": ["https://github.com/me/a.git", "https://github.com/me/b.git"]}'

# Add a repo to an existing project
curl -X POST http://localhost:8765/api/projects/<pid>/repos \
  -H "Content-Type: application/json" \
  -d '{"repos": ["https://github.com/me/c.git"]}'
```

<details>
<summary><strong>Full endpoint reference</strong></summary>

**Projects & repos**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/projects` | List projects |
| `GET` | `/api/projects/{pid}` | Project metadata |
| `POST` | `/api/projects` | Create project from git URLs (streams `[clone]/[scan]/[link]`) |
| `DELETE` | `/api/projects/{pid}` | Delete a project |
| `POST` | `/api/projects/{pid}/repos` | Add repos to a project |
| `POST` | `/api/projects/{pid}/rescan` | Re-run the engine on the project |
| `GET` | `/api/projects/{pid}/updates` | Upstream change detection (stale repos) |
| `GET` | `/api/projects/{pid}/graph` | Full graph data |
| `GET` | `/api/projects/{pid}/source?file_path=X` | Source file contents |
| `GET` | `/api/projects/{pid}/diff` | Graph diff vs. a stored snapshot |
| `GET` | `/api/remotes/repos?url=…` | Browse a GitHub/GitLab/Bitbucket org or user's repos |
| `GET` | `/api/local/repos` | Discover local candidate repos |

**Tools**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/projects/{pid}/tools` | List all tools + schemas |
| `POST` | `/api/projects/{pid}/tools/{name}` | Execute any tool with JSON args |
| `GET` | `/api/projects/{pid}/tools/search?q=X` | Quick search |
| `GET` | `/api/projects/{pid}/tools/callers?method=X` | Find callers |
| `GET` | `/api/projects/{pid}/tools/channels` | List channels |
| `GET` | `/api/projects/{pid}/tools/channel/{name}` | Channel flow |
| `GET` | `/api/projects/{pid}/tools/overview` | Architecture summary |
| `GET` | `/api/projects/{pid}/tools/trace?from_method=X&to_method=Y` | Path trace |
| `GET` | `/api/projects/{pid}/tools/orphans` | Half-wired channels |
| `GET` | `/api/projects/{pid}/tools/cycles` | Repo-level dependency cycles |
| `GET` | `/api/projects/{pid}/tools/dead_code` | Dead-code candidates |
| `GET` | `/api/projects/{pid}/tools/diff` | Diff two graph snapshots |

**AI conversations**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/ai/models` | Available LLM models |
| `POST` | `/api/projects/{pid}/conversations` | Create a conversation |
| `GET` | `/api/projects/{pid}/conversations` | List conversations |
| `GET` | `/api/projects/{pid}/conversations/{cid}` | Conversation with messages |
| `DELETE` | `/api/projects/{pid}/conversations/{cid}` | Delete a conversation |
| `POST` | `/api/projects/{pid}/conversations/{cid}/chat/stream` | Send a message; streams the reply (SSE: `token`, `reasoning`, `tool_start`, `tool_result`, `task_complete`, `done`, `error`) |
| `GET/DELETE` | `/api/projects/{pid}/conversations/{cid}/diagrams[/{id}]` | Persisted planner diagrams |

**Boards (GitHub Projects sync)**

| Method | Path | Description |
|--------|------|-------------|
| `GET/POST` | `/api/projects/{pid}/boards` | List connected boards / connect one |
| `DELETE` | `/api/projects/{pid}/boards/{bid}` | Disconnect a board |
| `POST` | `/api/projects/{pid}/boards/{bid}/sync` | Two-way issue ↔ card sync |
| `POST` | `/api/projects/{pid}/boards/{bid}/items` | Move a card (status swim lane / issue open-close) |
| `POST` | `…/boards/{bid}/items/comment` | Comment on a synced item |
| `POST` | `…/boards/{bid}/items/create` | Create a card + issue |

Misc: `GET /api/graph` (legacy default-project graph), `GET /health`.

</details>

### 3. MCP Server (for coding agents)

Register Constellation with Claude Code, Cursor, or any MCP-compatible agent. Built on the official MCP Python SDK (v2); the protocol version is negotiated automatically. The MCP server is **multi-project aware**: it exposes every project the app knows about (the same projects the web UI shows). Call `list_projects` to discover them, then pass a `project` id to any graph tool to query that project (omit it to query the default — the most recently updated ready project). All graph tools are exposed and tagged read-only, plus the default project's graph as the `constellation://graph` resource.

**Docker / web-server hosting (Streamable HTTP):** the FastAPI web app mounts the same MCP server at `/mcp`, so the container serves MCP with just a URL and no stdio subprocess:

```json
// .mcp.json
{
  "mcpServers": {
    "constellation": {
      "type": "http",
      "url": "http://localhost:8765/mcp"
    }
  }
}
```

**Local / non-docker (stdio):**

```json
{
  "mcpServers": {
    "constellation": {
      "command": "python",
      "args": ["-m", "engine.mcp_server"],
      "cwd": "/path/to/constellation"
    }
  }
}
```

The agent can then ask questions like:
- "What service handles order-events messages?"
- "If I change the `save` method, what entry points are affected?"
- "Trace the path from the REST endpoint to the database write"

---

## Graph Tools

Thirteen tools, shared across all three interfaces:

| Tool | Description |
|------|-------------|
| `search_code` | Search entry points, producers, and files by name or pattern |
| `get_node` | Full details + call tree for a specific entry point |
| `find_callers` | Impact analysis — find all entry points that call a given method |
| `trace_path` | Trace the execution chain from method A to method B |
| `get_channel_flow` | Full message flow through a queue/topic (producers → consumers) |
| `list_channels` | All inter-service message channels |
| `get_source` | Source code with line numbers and optional highlighting |
| `get_architecture_overview` | System-level summary (repos, types, complexity metrics) |
| `find_orphans` | Message channels only half-wired (producer with no consumer, or vice versa) |
| `find_cycles` | Repo-level dependency cycles via cross-repo channel edges (A → B → A) |
| `find_dead_code` | Possible dead code: unreachable methods, thin handlers, isolated repos |
| `diff_graphs` | Diff two graph snapshots (added/removed/changed entry points) |
| `task_complete` | Planner signal — report status and next steps |

One additional tool, `render_diagram`, is exposed **only** to the planner chat (it's stateful — diagrams are persisted on the conversation).

---

## CLI Usage

Run the engine directly without the web server:

```bash
# Activate the venv
source .venv/bin/activate

# Analyze repos
python -m engine.constellation \
  /path/to/repo1 \
  /path/to/repo2 \
  --output output/graph.json

# Start the MCP server (serves every project in the local store)
python -m engine.mcp_server
```

---

## How It Works

```
Source Code (.java files)
        │
        ▼
  tree-sitter AST parsing        ← deterministic, no LLM
        │
        ├──→ Symbol index        ← classes, imports, fields, methods, config
        ├──→ Framework scan      ← Spring, Jakarta, Camel, Axon, Quarkus, servlets, SOAP, GraphQL, gRPC, …
        ├──→ Producer scan       ← by declared field type (KafkaTemplate, WebClient, gRPC stubs, …)
        ├──→ Call tree build     ← BFS through method invocations, depth-limited
        └──→ Channel matching    ← literals, constants, ${} placeholders → cross-repo links
        │
        ▼
    graph.json (per project)
        │
        ├──→ Web UI             ← topology / flows / planner / boards / dead-code views
        ├──→ REST API           ← /api/projects/{pid}/tools/* endpoints
        ├──→ MCP Server         ← stdio + streamable HTTP for coding agents
        └──→ AI Context         ← structured system prompt + tool-use loop
```

Every relationship in the graph is tagged with confidence:
- **`EXTRACTED`** — directly read from the source (annotation present, call resolved to a definition)
- **`INFERRED`** — derived by resolution (call name matched but couldn't confirm the target)
- **`AMBIGUOUS`** — multiple possible targets

---

## Configuration

Configuration lives in `.env` (created from `.env.example`; real env vars take precedence):

| Variable | Default | Description |
|----------|---------|-------------|
| `CONSTELLATION_PORT` | `8765` | Web server port |
| `CONSTELLATION_GRAPH` | `output/graph.json` | Graph file path (standalone MCP server) |
| `CONSTELLATION_API_TOKEN` | — | Optional bearer token; API is open when unset |
| `OPENCODE_API_KEY` | — | API key for AI chat (alias: `OPENAI_API_KEY`); empty disables AI gracefully |
| `OPENCODE_BASE_URL` | `https://opencode.ai/zen/v1` | Any OpenAI-compatible base URL (alias: `OPENAI_BASE_URL`) |
| `OPENCODE_MODEL` | `deepseek-v4-flash-free` | Default model (alias: `OPENAI_MODEL`) |
| `CONSTELLATION_FREE_MODELS_ONLY` | `true` | Only list free models in the chat dropdown |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | — | PAT for Boards sync (repo + project scopes); falls back to `gh auth token` locally |
| `GITHUB_MCP_TRANSPORT` | `http` | Boards GitHub MCP transport: `http` or `stdio` (Docker) |

---

## Testing

```bash
# Engine + server regression tests (stdlib runner, no pytest needed)
python tests/run_tests.py

# Single module
python tests/run_tests.py test_graph_diff

# End-to-end (Playwright; requires the server running)
cd tests/e2e && npm install && npm test
```

---

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| AST Parsing | [tree-sitter](https://tree-sitter.github.io/) + tree-sitter-java | Industry standard, pre-built wheels for Windows/Linux |
| Engine | Python 3.10+ (stdlib-only HTTP for LLM calls) | Cross-platform, minimal deps |
| API Server | [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn | Async, fast, auto-docs |
| Frontend | React 18 + Vite | `npm run dev` for HMR, `npm run build` for production |
| Graph Viz | SVG (custom) + CSS animations | Lightweight, no heavy dependencies |
| AI (optional) | OpenAI-compatible API (Zen by default) | Proxied server-side, key never in frontend |
| MCP | Official MCP Python SDK (v2), stdio + streamable HTTP | Standard protocol for coding agents |

---

## Limitations (Honest)

**What works well:**
- JVM framework detection — Spring, Jakarta EE, Camel, Axon, Quarkus/MicroProfile, Micronaut, servlets, SOAP, GraphQL, gRPC, cloud functions
- Producers matched by declared field type (no variable-name false positives)
- Cross-repo linking via channel names — literals, `Class.CONST`, `${...}` config placeholders
- Import-aware call resolution with interface→impl linking, plus local-variable and parameter-typed receivers (chained calls resolve to `EXTRACTED`)
- Confidence tagging (`EXTRACTED` vs `INFERRED` vs `AMBIGUOUS`)

**What doesn't work yet:**
- Non-JVM languages (the language-spec registry exists, but only Java ships today; Python/TypeScript/Go are planned)
- Overload resolution by parameter *types* (arity only)
- Manual `channel.basicConsume` style consumers without annotations
- True data flow / taint analysis (this is call-graph, not data-flow)
