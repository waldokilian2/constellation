@echo off
REM ============================================================
REM Constellation — Startup Script (Windows)
REM ============================================================
REM Sets up the Python venv if missing, installs dependencies,
REM generates the demo graphs (graph.json + graph-java-ee.json)
REM from the test repos if missing, and starts the web server.
REM
REM Usage:
REM   start.bat                  REM start with default repos
REM   start.bat C:\path\to\repo1 REM custom repos
REM ============================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Switch the console to UTF-8 so the box-drawing / checkmark glyphs below
REM render correctly instead of as mojibake (the file is saved as UTF-8).
chcp 65001 >nul

echo.
echo ╔══════════════════════════════════════════════╗
echo ║  Constellation — Codebase Entry Point Mapper ║
echo ╚══════════════════════════════════════════════╝
echo.

REM ── Check Python ────────────────────────────────────────────────
set "PYTHON="
for %%c in (python py) do (
    where %%c >nul 2>&1 && (
        for /f "tokens=*" %%v in ('%%c --version 2^>^&1') do (
            REM findstr /r has no grouping/alternation, so use space-separated
            REM terms (space == OR): 3.10-3.19 OR 3.20-3.99.
            echo %%v | findstr /r "3\.1[0-9] 3\.[2-9][0-9]" >nul && (
                set "PYTHON=%%c"
                set "PYVER=%%v"
                goto :found_python
            )
        )
    )
)

echo Error: Python 3.10+ is required.
echo        Install from: https://www.python.org/downloads/
pause
exit /b 1

:found_python
echo ✓ Python: !PYVER!

REM ── Create venv if missing ──────────────────────────────────────
if not exist ".venv" (
    echo → Creating virtual environment...
    %PYTHON% -m venv .venv
)

REM ── Activate venv and install deps ──────────────────────────────
call ".venv\Scripts\activate.bat"

echo ✓ Virtual environment ready

REM Check if deps need installing. Use the venv `python` (post-activation)
REM so the probe inspects the venv, not a `py`-launcher pick. requirements.txt
REM now also requires the MCP SDK (mcp>=2.0), so probe for it too.
python -c "import tree_sitter, fastapi, mcp" 2>nul
if errorlevel 1 (
    echo → Installing dependencies...
    pip install --quiet -r requirements.txt
)
echo ✓ Dependencies ready

REM ── Generate demo graphs if missing or custom repos passed ───────
REM The server seeds two demo projects on first load (ProjectStore
REM ensure_legacy_seed): output\graph.json → "Spring Boot" and
REM output\graph-java-ee.json → "Java EE". Match start.sh and produce
REM both, so the Java EE demo isn't missing on Windows.
if exist "output\graph.json" if exist "output\graph-java-ee.json" if "%~1"=="" goto :have_graph

echo → Analyzing codebase...

if "%~1"=="" (
    REM Default: use built-in test repos
    if exist "tests\repos\order-service" (
        if not exist "output\graph.json" (
            echo    Using built-in test repos...
            python -m engine.constellation tests\repos\order-service tests\repos\fulfillment-service tests\repos\notification-service --output output\graph.json 2>&1 | findstr /v "RuntimeWarning"
        )
        REM Second demo project: Java EE / Jakarta annotations coverage.
        if exist "tests\repos\java-ee-order-service" (
            if not exist "output\graph-java-ee.json" (
                echo    Analyzing java-ee services - Java EE annotations...
                python -m engine.constellation tests\repos\java-ee-order-service tests\repos\java-ee-fulfillment-service tests\repos\java-ee-notification-service --output output\graph-java-ee.json 2>&1 | findstr /v "RuntimeWarning"
            )
        )
    ) else if exist "tests\repos\sample-spring-kafka-microservices" (
        if not exist "output\graph.json" (
            echo    Using sample-spring-kafka-microservices...
            python -m engine.constellation tests\repos\sample-spring-kafka-microservices\order-service tests\repos\sample-spring-kafka-microservices\payment-service tests\repos\sample-spring-kafka-microservices\stock-service --output output\graph.json 2>&1 | findstr /v "RuntimeWarning"
        )
    ) else (
        echo Error: No repos found to analyze.
        echo        Pass repo paths as arguments: start.bat C:\path\to\repo1
        pause
        exit /b 1
    )
) else (
    REM Custom repos passed as arguments
    set "REPO_ARGS="
    :parse_args
    if "%~1"=="" goto :run_analyze
    set "REPO_ARGS=!REPO_ARGS! %~1"
    shift
    goto :parse_args
    :run_analyze
    python -m engine.constellation !REPO_ARGS! --output output\graph.json 2>&1 | findstr /v "RuntimeWarning"
)

:have_graph
echo ✓ Graph data ready

REM ── Build frontend ──────────────────────────────────────────────
REM Always run npm install (idempotent). Gating it on `node_modules`
REM existing silently broke the build whenever package.json gained a
REM dependency after the last install (e.g. dompurify in Markdown.jsx),
REM which left a stale web/dist being served. Abort on a failed build,
REM matching start.sh's `set -e`.
if exist "package.json" (
    echo → Building frontend...
    call npm install --silent --no-audit --no-fund
    call npm run build
    if errorlevel 1 (
        echo.
        echo ✗ Frontend build failed - the server would serve a stale UI.
        echo   Fix the npm error above, then re-run start.bat.
        pause
        exit /b 1
    )
    echo ✓ Frontend ready
)

REM ── Start server ────────────────────────────────────────────────
if "%CONSTELLATION_PORT%"=="" set "CONSTELLATION_PORT=8765"

echo.
echo ═══════════════════════════════════════════════
echo  Constellation is running!
echo  Open: http://localhost:%CONSTELLATION_PORT%
echo  API:  http://localhost:%CONSTELLATION_PORT%/api/graph
echo  Docs: http://localhost:%CONSTELLATION_PORT%/docs
echo  Press Ctrl+C to stop
echo ═══════════════════════════════════════════════
echo.

python -m uvicorn server:app --host 0.0.0.0 --port %CONSTELLATION_PORT% --reload
