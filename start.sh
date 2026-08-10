#!/usr/bin/env bash
# ============================================================
# Constellation — Startup Script (Linux/macOS)
# ============================================================
# Sets up the Python venv if missing, installs dependencies,
# generates graph.json from the test repos if missing, and
# starts the web server.
#
# Usage:
#   ./start.sh                  # start with default repos
#   ./start.sh /path/to/repo1 /path/to/repo2  # custom repos
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Protect local .env edits from being committed ────────────────
# .env ships as a committed template; mark it skip-worktree so each
# user's secrets stay local and are never pushed. Harmless if not a
# git repo or .env is absent.
if [ -f ".env" ]; then
    git update-index --skip-worktree .env 2>/dev/null || true
fi

# ── Colors ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  Constellation — Codebase Entry Point Mapper ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ── Check Python ──────────────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
    if command -v $cmd &>/dev/null; then
        version=$($cmd --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
        major=$(echo $version | cut -d. -f1)
        minor=$(echo $version | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ] 2>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${RED}Error: Python 3.10+ is required.${NC}"
    echo "       Found: $(python3 --version 2>&1 || echo 'not found')"
    echo "       Install from: https://www.python.org/downloads/"
    exit 1
fi
echo -e "${GREEN}✓${NC} Python: $($PYTHON --version)"

# ── Create venv if missing ────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}→${NC} Creating virtual environment..."
    $PYTHON -m venv .venv
fi

# ── Activate venv ─────────────────────────────────────────────────
source .venv/bin/activate
echo -e "${GREEN}✓${NC} Virtual environment: $(which python)"

# ── Install dependencies ──────────────────────────────────────────
NEEDS_INSTALL=false
if ! python -c "import tree_sitter" 2>/dev/null; then
    NEEDS_INSTALL=true
fi
if ! python -c "import fastapi" 2>/dev/null; then
    NEEDS_INSTALL=true
fi

if [ "$NEEDS_INSTALL" = true ]; then
    echo -e "${YELLOW}→${NC} Installing dependencies..."
    pip install --quiet -r requirements.txt 2>&1 | grep -v "already satisfied" || true
fi
echo -e "${GREEN}✓${NC} Dependencies ready"

# ── Generate graph.json if missing ────────────────────────────────
if [ ! -f "output/graph.json" ] || [ ! -f "output/graph-java-ee.json" ] || [ "$1" != "" ]; then
    echo -e "${YELLOW}→${NC} Analyzing codebase..."

    if [ "$1" != "" ]; then
        # Custom repos passed as arguments
        REPO_ARGS=""
        for repo_path in "$@"; do
            abs_path="$(cd "$repo_path" 2>/dev/null && pwd)" || abs_path="$repo_path"
            REPO_ARGS="$REPO_ARGS \"$abs_path\""
        done
        eval "python -m engine.constellation $REPO_ARGS --output output/graph.json" 2>&1 | grep -v RuntimeWarning
    else
        # Default: use built-in test repos
        if [ -d "tests/repos/order-service" ]; then
            if [ ! -f "output/graph.json" ]; then
                echo "   Using built-in test repos..."
                python -m engine.constellation \
                    tests/repos/order-service \
                    tests/repos/fulfillment-service \
                    tests/repos/notification-service \
                    --output output/graph.json 2>&1 | grep -v RuntimeWarning
            fi
            # Second demo project: Java EE / Jakarta annotations coverage.
            if [ -d "tests/repos/java-ee-order-service" ]; then
                echo "   Analyzing java-ee services (Java EE annotations)..."
                python -m engine.constellation \
                    tests/repos/java-ee-order-service \
                    tests/repos/java-ee-fulfillment-service \
                    tests/repos/java-ee-notification-service \
                    --output output/graph-java-ee.json 2>&1 | grep -v RuntimeWarning
            fi
        elif [ -d "tests/repos/sample-spring-kafka-microservices" ]; then
            echo "   Using sample-spring-kafka-microservices..."
            python -m engine.constellation \
                tests/repos/sample-spring-kafka-microservices/order-service \
                tests/repos/sample-spring-kafka-microservices/payment-service \
                tests/repos/sample-spring-kafka-microservices/stock-service \
                --output output/graph.json 2>&1 | grep -v RuntimeWarning
        else
            echo -e "${RED}Error: No repos found to analyze.${NC}"
            echo "       Pass repo paths as arguments: ./start.sh /path/to/repo1 /path/to/repo2"
            exit 1
        fi
    fi
fi
echo -e "${GREEN}✓${NC} Graph data ready"

# ── Build frontend ─────────────────────────────────────────────────
if [ -f "package.json" ]; then
    if [ ! -d "web/dist" ] || [ web/index.html -nt web/dist/index.html ] 2>/dev/null || \
       [ web/src/app.jsx -nt web/dist/index.html ] 2>/dev/null || \
       [ web/src/styles.css -nt web/dist/index.html ] 2>/dev/null; then
        echo -e "${YELLOW}→${NC} Building frontend..."
        if [ ! -d "node_modules" ]; then
            npm install --silent 2>&1 | tail -3
        fi
        npm run build 2>&1 | tail -3
    fi
    echo -e "${GREEN}✓${NC} Frontend ready"
fi

# ── Start server ──────────────────────────────────────────────────
PORT="${CONSTELLATION_PORT:-8765}"
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════${NC}"
echo -e "${CYAN}  Constellation is running!${NC}"
echo -e "${CYAN}  Open: http://localhost:${PORT}${NC}"
echo -e "${CYAN}  API:  http://localhost:${PORT}/api/graph${NC}"
echo -e "${CYAN}  Docs: http://localhost:${PORT}/docs${NC}"
echo -e "${CYAN}  Press Ctrl+C to stop${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════${NC}"
echo ""

exec python -m uvicorn server:app --host 0.0.0.0 --port "$PORT" --reload
