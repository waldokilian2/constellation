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
3. Generate `graph.json` from the test repos (if missing)
4. Start the server

### Requirements

- **Python 3.10+**
- **A modern browser** (for the web UI)
- **Optional:** `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` env var for AI features (works without — just disables AI chat)

---

## What It Does

### Entry Point Detection

Scans for framework-specific annotations and patterns:

| Pattern | Type | Framework |
|---------|------|-----------|
| `@RabbitListener(queues = "name")` | RabbitMQ Consumer | Spring AMQP |
| `@KafkaListener(topics = "name")` | Kafka Consumer | Spring Kafka |
| `@JmsListener(destination = "name")` | JMS Consumer | Spring JMS |
| `@GetMapping`, `@PostMapping`, etc. | REST Endpoint | Spring Web |
| `@RequestMapping` (class-level prefix) | REST Endpoint | Spring Web |
| `@EventListener` | Event Listener | Spring Events |

### Producer Detection

Finds message producers by matching method calls:

| Pattern | Type |
|---------|------|
| `rabbitTemplate.convertAndSend("queue", ...)` | RabbitMQ Producer |
| `kafkaTemplate.send("topic", ...)` / `template.send(...)` | Kafka Producer |
| `jmsTemplate.convertAndSend(...)` | JMS Producer |
| `applicationEventPublisher.publishEvent(...)` | Event Publisher |

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
│   ├── parser.py                   #   tree-sitter Java AST wrapper
│   ├── entry_detector.py           #   Spring annotation + producer scanner
│   ├── call_graph.py               #   BFS call tree builder (depth-limited)
│   ├── cross_repo.py               #   Queue/topic name matcher
│   ├── context_builder.py          #   Builds AI system prompts from graph data
│   ├── graph_tools.py              #   8 query functions (shared by all interfaces)
│   ├── mcp_server.py               #   MCP stdio server for coding agents
│   ├── models.py                   #   Data classes
│   └── constellation.py            #   CLI orchestrator
│
├── server.py                       # FastAPI web server + REST API
├── web/                            # React frontend (CDN, no build step)
│   ├── index.html
│   ├── app.js                      # Galaxy → Solar System → Path → Detail views
│   └── styles.css
│
├── tests/repos/                    # Sample Java microservice repos
│   ├── order-service/              #   REST + RabbitMQ producer + event listener
│   ├── fulfillment-service/        #   RabbitMQ consumer + Kafka producer
│   ├── notification-service/       #   Kafka + RabbitMQ consumers
│   └── sample-spring-kafka-microservices/  # Real cloned repo (3 services)
│
├── output/                         # Generated graphs
│   ├── graph.json                  #   Default graph (test repos)
│   └── real-repo-graph.json        #   Real repo graph
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

Three zoom levels:

| View | What You See |
|------|-------------|
| **Galaxy** | All repos as clusters, message channels as curved connections with channel names |
| **Solar System** | Entry points in a repo as stars (sized by complexity, colored by type) |
| **Path** | Call tree for one entry point — the full execution chain from request to response |
| **Detail Panel** | Source code with line highlighting, relationships, and AI chat |

### 2. REST API

```bash
# List all tools
curl http://localhost:8765/api/tools

# Search the codebase
curl "http://localhost:8765/api/tools/search?q=OrderService"

# Find all callers of a method
curl "http://localhost:8765/api/tools/callers?method=save"

# Get message channel flow
curl "http://localhost:8765/api/tools/channel/order-events"

# Architecture overview
curl http://localhost:8765/api/tools/overview

# Trace a path between two methods
curl "http://localhost:8765/api/tools/trace?from_method=createOrder&to_method=save"

# Execute any tool via POST
curl -X POST http://localhost:8765/api/tools/find_callers \
  -H "Content-Type: application/json" \
  -d '{"method_name": "save"}'
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
| `GET` | `/api/graph` | Full graph data |
| `GET` | `/api/graph/entry-points` | All entry points |
| `GET` | `/api/graph/entry-point/{id}` | Single entry point |
| `GET` | `/api/graph/cross-repo-links` | Cross-repo connections |
| `GET` | `/api/graph/repos` | Repo summary |
| `GET` | `/api/source?file_path=X` | Source file contents |
| `POST` | `/api/analyze` | Re-run engine on new repo paths |

### AI Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/ai/chat` | Conversational chat with tool-use (structured context) |
| `POST` | `/api/ai/explain` | Legacy single-call endpoint |
| `GET` | `/api/ai/models` | Available LLM models |

### Tool Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/tools` | List all tools + schemas |
| `POST` | `/api/tools/{name}` | Execute any tool with JSON args |
| `GET` | `/api/tools/search?q=X` | Quick search |
| `GET` | `/api/tools/callers?method=X` | Find callers |
| `GET` | `/api/tools/channels` | List channels |
| `GET` | `/api/tools/channel/{name}` | Channel flow |
| `GET` | `/api/tools/overview` | Architecture summary |
| `GET` | `/api/tools/trace?from_method=X&to_method=Y` | Path trace |

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
        ├──→ Annotation scan     ← finds @RabbitListener, @GetMapping, etc.
        ├──→ Producer scan       ← finds convertAndSend(), send(), publishEvent()
        ├──→ Call tree build     ← BFS through method invocations, depth-limited
        └──→ Channel matching    ← string comparison of queue/topic names
        │
        ▼
    graph.json
        │
        ├──→ Web UI             ← galaxy → solar system → path → detail
        ├──→ REST API           ← /api/tools/* endpoints
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
| `OPENAI_API_KEY` | — | API key for AI features |
| `OPENAI_BASE_URL` | `https://api.openai.com` | OpenAI-compatible base URL |
| `OPENAI_MODEL` | `nemotron-3-ultra-free` | Default model |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (takes priority if set) |

---

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| AST Parsing | [tree-sitter](https://tree-sitter.github.io/) + tree-sitter-java | Industry standard, pre-built wheels for Windows/Linux |
| Engine | Python 3.10+ | Cross-platform, tree-sitter bindings |
| API Server | [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn | Async, fast, auto-docs |
| Frontend | React 18 (CDN) + Babel standalone | No build step — just open the files |
| Graph Viz | SVG (custom) + CSS animations | Lightweight, no heavy dependencies |
| AI (optional) | OpenAI-compatible API or Anthropic | Proxied server-side, key never in frontend |
| MCP | JSON-RPC 2.0 over stdio | Standard protocol for coding agents |

---

## Roadmap

### In Progress
- **Import-aware call resolution** — resolve calls using Java import statements for accurate targeting
- **Python language support** — FastAPI entry detector + tree-sitter-python
- **API key hardening** — `.env` file support, no env var exports needed

### Planned
- TypeScript/Express, Go, C# language support
- Apache Camel DSL route detection
- Dynamic queue name resolution (config + concatenation)
- Agent tool-use for Anthropic API format
- Streaming AI responses (SSE)

---

## Limitations (Honest)

**What works well:**
- Java Spring Boot annotation-based detection (RabbitMQ, Kafka, JMS, REST, Events)
- Producer/consumer cross-repo linking via channel name matching
- Call tree extraction to depth 4 with cycle prevention
- Confidence tagging (`EXTRACTED` vs `INFERRED`)

**What doesn't work yet:**
- Dynamic dispatch (interface method → which implementation?) — follows most common match
- Cross-file call resolution without imports — uses name matching (can produce false positives)
- Apache Camel DSL routes, manual `channel.basicConsume`
- Non-Java languages (Python/TypeScript/Go support planned)
- True data flow / taint analysis (this is call-graph, not data-flow)

---

## License

MIT
