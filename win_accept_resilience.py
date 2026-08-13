"""Event-loop policy setup — avoid the Windows proactor accept bug.

The problem (CPython #93821, open since 2022, unfixed in main through 3.14)
---------------------------------------------------------------------------
On Windows Python's default loop is the *proactor* loop (IOCP). Its accept
loop, ``BaseProactorEventLoop._start_serving``, treats *any* ``OSError`` from an
overlapped ``AcceptEx`` as fatal and closes the listening socket. A transient
client abort — ``WinError 64`` (``ERROR_NETNAME_DELETED``) / ``WSAECONNRESET``,
common on this app's multi-poll landing page — therefore makes the server
unreachable while it keeps looking alive. ``GetOverlappedResult`` reports these
as the generic ``ERROR_NETNAME_DELETED`` rather than the specific WSA error, so
the loop can't tell a fatal failure from a benign client RST.

The fix
-------
The *selector* loop does not have this bug: ``BaseSelectorEventLoop._accept_connection``
discards ``ConnectionAbortedError``, retries resource-exhaustion errors, and
re-raises+ignores other ``OSError`` without ever closing the listener. It is the
default on Linux/macOS. So we simply use the selector loop on Windows too. This
eliminates the whole class of failure with **no coupling to CPython internals**
— no private-method override, no version-pinning, no drift tests.

Trade-offs (acceptable for this local dev server)
-------------------------------------------------
* Loses IOCP's scaling and the Windows ``select()`` ``FD_SETSIZE`` ceiling
  (~512 sockets). Immaterial here: this is a developer tool whose connection
  counts stay far below that.
* The proactor is the only loop that supports ``asyncio`` subprocesses on
  Windows. Not used by this server — it runs blocking ``subprocess.run`` in a
  threadpool (``run_in_executor``), which works on any loop.

Installed from ``server.py`` at import time, before uvicorn creates its loop.
"""
from __future__ import annotations

import asyncio
import sys

# On Windows we force the selector loop to dodge the proactor accept-close bug
# (CPython #93821). Anywhere else the selector loop is already the default.
TARGET_POLICY_KIND: str | None = "selector" if sys.platform.startswith("win") else None


def desired_policy_kind(platform: str = sys.platform) -> str | None:
    """Pure: which loop-policy kind to install for the given platform.

    Returns ``"selector"`` on Windows, ``None`` elsewhere. Pure and
    platform-parametric so it is unit-testable on any OS.
    """
    return "selector" if platform.startswith("win") else None


def install() -> bool:
    """Install the desired loop policy. Returns True when one was applied.

    No-op on non-Windows (the selector loop is already the default there).
    """
    kind = desired_policy_kind()
    if kind is None:
        return False
    if kind == "selector":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        return True
    return False  # pragma: no cover - no other kinds are defined
