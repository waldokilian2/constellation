"""Mermaid validation by shelling out to Node + the bundled mermaid package.

The Python engine has no Mermaid runtime, but the project's ``node_modules``
ships mermaid (the frontend renders with it). ``validate_mermaid`` spawns a
tiny Node script (``mermaid_validate.mjs``) that:

1. applies the **same repair pass the browser uses** (``web/src/mermaidRepair.js``),
2. runs ``mermaid.parse`` under jsdom on the repaired source, and
3. prints a JSON verdict plus the repaired code.

Because validation runs on the *repaired* source, a diagram the browser would
render (e.g. a bare edge label ``|GET /api/x/{id}|`` that repairMermaid
quotes) is accepted and stored in repaired form — it is never rejected at
tool-call time. Only genuinely broken syntax is returned as an error, so the
AI's ``render_diagram`` tool gets an authoritative error message and can
self-correct within the same turn instead of looping on fixable trivia.

Degradation: if Node, the script, or the mermaid/jsdom packages are missing or
error, validation accepts (returns ``ok=True`` and the raw code unchanged). The
feature must never block on a missing toolchain; the frontend's render-time
fallback still surfaces any residual failure.
"""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent / "mermaid_validate.mjs"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_NODE_TIMEOUT = 12  # seconds — generous; cold node + mermaid init is ~0.3s


def _node_bin() -> str:
    return os.environ.get("CONSTELLATION_NODE_BIN") or "node"


def is_available() -> bool:
    """True if Node and the validator script are present (cheap check)."""
    if not _SCRIPT.exists():
        return False
    try:
        subprocess.run(
            [_node_bin(), "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            cwd=str(_PROJECT_ROOT),
        )
        return True
    except Exception:
        return False


def validate_mermaid(code: str) -> tuple[bool, str, str]:
    """Validate Mermaid source. Returns ``(ok, error, code)``.

    * ``ok=True``  — valid (after repair) OR the validator could not run
      (degrade to accept). ``code`` is the repaired source to store so the
      panel renders exactly what was validated.
    * ``ok=False`` — definitely invalid; ``error`` carries the first line of
      the parse error for the AI to act on, ``code`` is unchanged.
    """
    if not code or not code.strip():
        return False, "Diagram code is empty.", code

    try:
        proc = subprocess.run(
            [_node_bin(), str(_SCRIPT)],
            input=code,
            capture_output=True,
            text=True,
            timeout=_NODE_TIMEOUT,
            cwd=str(_PROJECT_ROOT),
        )
    except FileNotFoundError:
        return True, "", code  # no node → accept
    except subprocess.TimeoutExpired:
        return False, "Mermaid validation timed out.", code
    except Exception:
        return True, "", code  # unknown infra failure → accept, FE handles

    # The script prints exactly one JSON line; be defensive about stray output.
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if not lines:
        return True, "", code  # script crashed before printing → accept
    try:
        verdict = json.loads(lines[-1])
    except json.JSONDecodeError:
        return True, "", code
    if verdict.get("valid"):
        repaired = verdict.get("code")
        return True, "", repaired if isinstance(repaired, str) and repaired else code
    return False, verdict.get("error") or "Invalid Mermaid syntax.", code
