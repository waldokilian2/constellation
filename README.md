# ⚡ Constellation

**Deterministically map Java Spring Boot microservice architectures. Find every entry point, trace execution paths, and see how messages flow between services — all from static analysis, zero AI required.**

Constellation parses source code with tree-sitter AST to build a complete map of your microservice architecture. No code runs, no LLMs needed for the core analysis — every relationship is extracted directly from the source.

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
1. Create a Python virtual environment (if missing)
2. Install dependencies (if missing)
3. Generate the demo graphs from the bundled test repos (if missing)
4. Build the frontend (`npm install && npm run build`, if `web/dist/` is missing or stale)
5. Start the server

On first load the server seeds two demo projects:
- **Spring Boot** — `order-service`, `fulfillment-service`, `notification-service` (Spring Messaging / REST / Kafka / RabbitMQ)
- **Java EE** — `java-ee-order-service`, `java-ee-fulfillment-service`, `java-ee-notification-service` (JAX-RS, JMS MDB, CDI, EJB, WebSocket, Spring `@Scheduled`/`@MessageMapping`) with real cross-repo links

### Requirements

- **Python 3.10+**
- **Node.js 18+** (for the Vite frontend build)
- **A modern browser** (for the web UI)
- **Optional:** `OPENCODE_API_KEY` env var for AI features (defaults to Zen; works without — just disables AI chat). Add it to the committed `.env` template.

---

## What It Does

### Entry Point Detection

Scans for framework-specific annotations and patterns:

| Pattern | Type | Framework |
|---------|------|-----------|
| `@RabbitListener(queues = "name")` | RabbitMQ Consumer | Spring AMQP |
| `@KafkaListener(topics = "name")` | Kafka Consumer | Spring Kafka |
| `@JmsListener(destination = "name")` | JMS Consumer | Spring JMS |
| `@RocketMQMessageListener(topic = "name")` | Kafka-style Consumer | RocketMQ |
| `@StreamListener(value = "name")` | Kafka-style Consumer | Spring Cloud Stream |
| `@GetMapping`, `@PostMapping`, etc. | REST Endpoint | Spring Web |
| `@RequestMapping` (class-level prefix) | REST Endpoint | Spring Web |
| `@EventListener` / `@TransactionalEventListener` | Event Listener | Spring Events |
| `@Scheduled` | Scheduled Task | Spring |
| `@MessageMapping` / `@SubscribeMapping` | WebSocket / STOMP | Spring Messaging |
| `@Path` + `@GET`/`@POST`/… | REST Endpoint | JAX-RS (Jakarta EE) |
| `@MessageDriven` + `activationConfig` | JMS MDB consumer | Jakarta EE |
| `@Observes` (parameter) | Event Listener | CDI |
| `@Schedule` / `@Schedules` | Scheduled Task | EJB |
| `@ServerEndpoint` + `@OnMessage`/`@OnOpen`/… | WebSocket endpoint | Jakarta WebSocket |

Channels are resolved through the symbol index: string literals, `Class.CONST` / bare constant
references, `${...}` placeholders (from `application.properties`/`.yml`), and `#{...}` SpEL
(dynamic — preserved for display). Array arguments (`topics = {"a", "b"}`) produce one entry
point per element.

### Producer Detection

Finds message producers by matching the **declared field type** of the receiver (not the variable name — a plain `template.send(...)` no longer false-matches):

| Declared field type | Producing methods | Type |
|---------------------|-------------------|------|
| `KafkaTemplate` | `send(...)` | Kafka Producer |
| `RabbitTemplate` / `AmqpTemplate` | `convertAndSend(...)`, `send(...)` | RabbitMQ Producer |
| `JmsTemplate` | `convertAndSend(...)`, `send(...)` | JMS Producer |
| `ApplicationEventPublisher` | `publishEvent(...)` | Event Publisher |
| `StreamBridge` | `send(...)` | Cloud Stream (broker-agnostic) |

### Cross-Repo Message Flow

When Repo A produces to `"order-events"` and Repo B consumes from `"order-events"`, Constellation links them. This is deterministic string matching on queue/topic names across parsed repos — no AI, no inference.

### Call Tree Extraction

For each entry point, Constellation builds a call tree by:
1. Parsing the handler method body with tree-sitter AST
2. Finding all method invocations
3. Resolving each call to its definition in the codebase
4. Recursing up to depth 4 (configurable)
5. Marking each node with confidence: `EXTRACTED` (resolved) or `INFERRED` (unresolved)

### AI Integration (Optional)

When an API key is configured, the web UI provides a conversational AI assistant that:
- Gets a **structured system prompt** with the architecture overview, call tree, and cross-repo connections
- Can call **graph tools** to search the codebase, find callers, and trace paths
- Works as a multi-turn chat with follow-up questions

---

## Architecture

```
constellation/
├── engine/                         # Deterministic analysis engine
│   ├── parser.py                   #   tree-sitter Java AST wrapper + structural helpers
│   ├── java_index.py               #   repo-wide symbol index (type-aware resolution)
│   ├── entry_detector.py           #   Spring + Java EE annotation + producer scanner
│   ├── call_graph.py               #   BFS call tree builder (depth-limited)
│   ├── cross_repo.py               #   Queue/topic name matcher
│   ├── context_builder.py          #   Builds AI system prompts from graph data
│   ├── graph_tools.py              #   8 query functions (shared by all interfaces)
│   ├── mcp_server.py               #   MCP stdio server for coding agents
│   ├── models.py                   #   Data classes
│   ├── paths.py                    #   Safe, root-confined source path resolution
│   ├── project_store.py            #   Multi-project index, git-clone ingestion
│   └── constellation.py            #   CLI orchestrator
│
├── server.py                       # FastAPI web server + REST API
├── web/                            # React 18 + Vite frontend
│   ├── index.html                  # Vite entry point
│   ├── src/
│   │   ├── main.jsx                # createRoot entry
│   │   ├── app.jsx                 # Projects → Galaxy → Service → Path → Detail views
│   │   ├── derived.js             # pure view analytics (flows, roles, stats)
│   │   └── styles.css              # visualization styles
│   └── dist/                       # Vite build output (gitignored)
│
├── tests/repos/                    # Sample Java microservice repos
│   ├── order-service/              #   Spring Boot demo: REST + RabbitMQ producer + event listener
│   ├── fulfillment-service/        #   Spring Boot demo: RabbitMQ consumer + Kafka producer
│   ├── notification-service/       #   Spring Boot demo: Kafka + RabbitMQ consumers
│   ├── java-ee-order-service/      #   Java EE demo (app1): JAX-RS, @MessageMapping,
│   │                               #     @Scheduled; producers → order-events
│   ├── java-ee-fulfillment-service/ #   Java EE demo (app2): JMS MDB, array-topics Kafka;
│   │                               #     producer → shipment-events
│   ├── java-ee-notification-service/ #   Java EE demo (app3): CDI @Observes, EJB @Schedule,
│   │                               #     WebSocket @ServerEndpoint; Kafka consumer
│   └── sample-spring-kafka-microservices/  # Real cloned repo (3 services)
│
├── output/                         # Generated graphs + project store (gitignored)
│   ├── graph.json                  #   Spring Boot demo graph (test repos)
│   ├── graph-java-ee.json          #   Java EE demo graph (cross-repo links)
│   ├── projects.json               #   Multi-project index
│   └── projects/<pid>/             #   Per-project: graph.json + cloned repos/
│
├── start.sh                        # Linux/macOS startup
├── start.bat                       # Windows startup
└── PLAN.md                         # Architecture + roadmap
```

---

## The Three Interfaces

The graph tools are pure functions in `engine/graph_tools.py`. They're exposed three ways:

```
                    Graph Tools (pure functions)
                            │
           ┌────────────────┼────────────────┐
           ▼                ▼                ▼
     MCP Server        REST API         Web AI Chat
   (stdio JSON-RPC)   (/api/tools/*)   (tool-use loop)
           │                │                │
           ▼                ▼                ▼
    Claude Code       Debugging /       Browser
    Cursor            External use       Detail Panel
```

### 1. Web UI

Projects list, then per-project zoom levels:

| View | What You See |
|------|-------------|
| **Projects** | All ingested projects (each is its own graph — e.g. "Spring Boot", "Java EE") |
| **Galaxy** | All services as orbs — size = call complexity, halo ring = entry-type mix, role pill, hub/isolated/sink badges; headline stats + insight line; channels as curved connections |
| **Service** | One service: entry-point star map, sortable entry-point table, inbound/outbound channel cards with partners + payload types, flows it participates in |
| **Path** | Call tree for one entry point — the full execution chain from request to response |
| **Detail Panel** | Source code with line highlighting, relationships, and AI chat |

### 2. REST API

All graph-dependent endpoints are **project-scoped** under `/api/projects/{pid}/...` (the legacy flat `/api/graph`, `/api/tools/*`, `/api/ai/*` routes were replaced):

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

Projects can also be created/via API (UI-driven ingestion clones git repos):

```bash
# Create a project from one or more git URLs
curl -X POST http://localhost:8765/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "My Stack", "repos": ["https://github.com/me/a.git", "https://github.com/me/b.git"]}'

# Add a repo to an existing project
curl -X POST http://localhost:8765/api/projects/<pid>/repos \
  -H "Content-Type: application/json" \
  -d '{"repos": ["https://github.com/me/c.git"]}'
```

### 3. MCP Server (for coding agents)

Register Constellation with Claude Code, Cursor, or any MCP-compatible agent:

```json
// .mcp.json
{
  "mcpServers": {
    "constellation": {
      "command": "python",
      "args": ["-m", "engine.mcp_server"],
      "cwd": "/path/to/constellation",
      "env": {
        "CONSTELLATION_GRAPH": "/path/to/constellation/output/graph.json"
      }
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

Eight tools, shared across all three interfaces:

| Tool | Description |
|------|-------------|
| `search_code` | Search entry points, producers, and files by name or pattern |
| `get_node` | Get full details + call tree for a specific entry point |
| `find_callers` | Impact analysis — find all entry points that call a given method |
| `trace_path` | Trace the execution chain from method A to method B |
| `get_channel_flow` | Full message flow through a queue/topic (producers → consumers) |
| `list_channels` | All inter-service message channels |
| `get_source` | Source code with line numbers and optional highlighting |
| `get_architecture_overview` | System-level summary (repos, types, complexity metrics) |

---

## API Reference

### Graph Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/projects` | List projects |
| `GET` | `/api/projects/{pid}` | Project metadata |
| `POST` | `/api/projects` | Create project from git URLs (streams `[clone]/[scan]/[link]`) |
| `POST` | `/api/projects/{pid}/repos` | Add repos to a project |
| `POST` | `/api/projects/{pid}/rescan` | Re-run the engine on the project |
| `GET` | `/api/projects/{pid}/updates` | Upstream change detection (stale repos) |
| `DELETE` | `/api/projects/{pid}` | Delete a project |
| `GET` | `/api/projects/{pid}/graph` | Full graph data |
| `GET` | `/api/projects/{pid}/source?file_path=X` | Source file contents |

### AI Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/projects/{pid}/ai/chat` | Conversational chat with tool-use (structured context) |
| `POST` | `/api/projects/{pid}/ai/chat/stream` | Streaming SSE variant (token + tool-call events) |
| `POST` | `/api/projects/{pid}/ai/explain` | Legacy single-call endpoint |
| `GET` | `/api/ai/models` | Available LLM models |

### Tool Endpoints

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

# Start the MCP server
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
        ├──→ Symbol index        ← one pass: classes, imports, fields, methods, config (java_index.py)
        ├──→ Annotation scan     ← Spring + Java EE: @RabbitListener, @GetMapping, JAX-RS, MDB, CDI, EJB, WS
        ├──→ Producer scan       ← by declared field type (KafkaTemplate, RabbitTemplate, …)
        ├──→ Call tree build     ← BFS through method invocations, depth-limited
        └──→ Channel matching    ← literals, constants, ${} placeholders → cross-repo links
        │
        ▼
    graph.json (per project)
        │
        ├──→ Web UI             ← projects → galaxy → service → path → detail
        ├──→ REST API           ← /api/projects/{pid}/tools/* endpoints
        ├──→ MCP Server         ← stdio JSON-RPC for coding agents
        └──→ AI Context         ← structured system prompt + tool-use loop
```

Every relationship in the graph is tagged with confidence:
- **`EXTRACTED`** — directly read from the source (annotation present, call resolved to a definition)
- **`INFERRED`** — derived by resolution (call name matched but couldn't confirm the target)
- **`AMBIGUOUS`** — multiple possible targets

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CONSTELLATION_PORT` | `8765` | Web server port |
| `CONSTELLATION_GRAPH` | `output/graph.json` | Graph file path (MCP server) |
| `CONSTELLATION_API_TOKEN` | — | Optional bearer token; API is open when unset |
| `OPENCODE_API_KEY` | — | API key for AI features (alias: `OPENAI_API_KEY`) |
| `OPENCODE_BASE_URL` | `https://opencode.ai/zen/v1` | OpenAI-compatible base URL — Zen by default (alias: `OPENAI_BASE_URL`) |
| `OPENCODE_MODEL` | `deepseek-v4-flash-free` | Default model (alias: `OPENAI_MODEL`) |

---

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| AST Parsing | [tree-sitter](https://tree-sitter.github.io/) + tree-sitter-java | Industry standard, pre-built wheels for Windows/Linux |
| Engine | Python 3.10+ | Cross-platform, tree-sitter bindings |
| API Server | [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn | Async, fast, auto-docs |
| Frontend | React 18 + Vite | `npm run dev` for HMR, `npm run build` for production |
| Graph Viz | SVG (custom) + CSS animations | Lightweight, no heavy dependencies |
| AI (optional) | OpenAI-compatible API or Anthropic | Proxied server-side, key never in frontend |
| MCP | JSON-RPC 2.0 over stdio | Standard protocol for coding agents |

---

## Roadmap

### In Progress
- **Python language support** — FastAPI entry detector + tree-sitter-python
- **Local-variable type tracking** — chained calls on locals now resolve to `EXTRACTED` (params + local declarations fed into call resolution)
- **Overload resolution by parameter types** — only arity matching today

### Planned
- TypeScript/Express, Go, C# language support
- Apache Camel DSL route detection
- `@Bean` / Spring Cloud Stream consumer discovery
- Java EE SOAP (`@WebService`) and Servlets (`@WebServlet`, `HttpServlet` overrides)
- Dynamic queue name resolution (config + concatenation)
- Agent tool-use for Anthropic API format

---

## Limitations (Honest)

**What works well:**
- Java Spring + Java EE / Jakarta annotation detection (RabbitMQ, Kafka, JMS, REST, JAX-RS, Events, CDI, EJB, WebSocket, Scheduled)
- Producers matched by declared field type (no variable-name false positives)
- Cross-repo linking via channel names — literals, `Class.CONST`, `${...}` config placeholders
- Import-aware call resolution with interface→impl linking, plus local-variable and parameter-typed receivers (chained calls resolve to `EXTRACTED`)
- Call tree extraction to depth 4 with cycle prevention
- Confidence tagging (`EXTRACTED` vs `INFERRED` vs `AMBIGUOUS`)

**What doesn't work yet:**
- Overload resolution by parameter *types* (arity only)
- Apache Camel DSL routes, manual `channel.basicConsume`
- `@Bean`/Cloud Stream consumer discovery, Java EE SOAP + Servlets
- Non-Java languages (Python/TypeScript/Go support planned)
- True data flow / taint analysis (this is call-graph, not data-flow)

---

## License

MIT
