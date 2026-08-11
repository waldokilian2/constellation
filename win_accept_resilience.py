"""Windows accept-resilience patch for asyncio's proactor event loop.

On Windows, a transient network-stack failure — WinError 64 "The specified
network name is no longer available", WSAECONNRESET/ABORTED etc. — can make
the proactor's AcceptEx callback fail. These happen during Wi-Fi blips,
adapter resets and sleep/wake cycles, and are especially likely while the
engine is cloning repos over the network. CPython's default handling for an
accept failure CLOSES the listening socket: the server process keeps running
but can never accept another connection, which looks exactly like the server
dying.

This module installs a subclassed proactor loop whose accept loop treats
transient errnos as retryable — it logs a warning and reschedules the accept
instead of closing the socket. Installed from ``server.py`` at import time,
before uvicorn creates the event loop. The classifier is a pure function so
the behaviour is unit-testable; the loop reimplementation is version-guarded
(CPython 3.13, the version this repo is developed against).
"""
from __future__ import annotations

import asyncio
import sys

# ── transient-error classification (pure) ─────────────────────────

# errnos that indicate a transient network blip rather than a fatal server
# error: ERROR_NETNAME_DELETED, WSAENETDOWN, WSAENETRESET, WSAECONNABORTED,
# WSAECONNRESET, WSAENOTCONN.
TRANSIENT_ACCEPT_ERRNOS = frozenset({64, 10050, 10052, 10053, 10054, 10057})


def is_transient_accept_error(exc: BaseException) -> bool:
    """True when an accept failure is a retryable network blip."""
    return isinstance(exc, OSError) and exc.errno in TRANSIENT_ACCEPT_ERRNOS


# ── resilient proactor loop (CPython 3.13, Windows only) ──────────

if sys.platform == "win32" and sys.version_info[:2] == (3, 13):
    from asyncio import proactor_events as _proactor_events
    from asyncio import windows_events as _windows_events

    def _resilient_start_serving(
        self,
        protocol_factory,
        sock,
        sslcontext=None,
        server=None,
        backlog=100,
        ssl_handshake_timeout=None,
        ssl_shutdown_timeout=None,
    ):
        """Copy of ProactorEventLoop._start_serving that retries transient errors.

        Kept in lock-step with CPython 3.13's implementation; the only change
        is the transient-error branch (retry via call_later) in the OSError
        handler. The version guard above prevents this from running on a
        Python whose internals differ.
        """
        def loop(f=None):
            try:
                if f is not None:
                    conn, addr = f.result()
                    if self._debug:
                        _proactor_events.logger.debug(
                            "%r got a new connection from %r: %r",
                            server, addr, conn,
                        )
                    protocol = protocol_factory()
                    if sslcontext is not None:
                        self._make_ssl_transport(
                            conn, protocol, sslcontext, server_side=True,
                            extra={"peername": addr}, server=server,
                            ssl_handshake_timeout=ssl_handshake_timeout,
                            ssl_shutdown_timeout=ssl_shutdown_timeout,
                        )
                    else:
                        self._make_socket_transport(
                            conn, protocol,
                            extra={"peername": addr}, server=server,
                        )
                if self.is_closed():
                    return
                f = self._proactor.accept(sock)
            except OSError as exc:
                if is_transient_accept_error(exc):
                    # Network blip: keep the listener alive and retry shortly
                    # instead of letting the default handler close the socket
                    # (which makes the server unreachable for good).
                    _proactor_events.logger.warning(
                        "Transient accept error ignored (retrying): %r", exc
                    )
                    if not self.is_closed():
                        self.call_later(1.0, loop)
                    return
                if sock.fileno() != -1:
                    self.call_exception_handler({
                        "message": "Accept failed on a socket",
                        "exception": exc,
                        "socket": _proactor_events.trsock.TransportSocket(sock),
                    })
                    sock.close()
                elif self._debug:
                    _proactor_events.logger.debug(
                        "Accept failed on socket %r", sock, exc_info=True
                    )
            except asyncio.CancelledError:
                sock.close()
            else:
                self._accept_futures[sock.fileno()] = f
                f.add_done_callback(loop)

        self.call_soon(loop)

    class _ResilientProactorEventLoop(_windows_events.ProactorEventLoop):
        _start_serving = _resilient_start_serving

    class _ResilientWindowsPolicy(asyncio.WindowsProactorEventLoopPolicy):
        def new_event_loop(self):
            return _ResilientProactorEventLoop()

    def install() -> bool:
        """Install the resilient loop policy. Returns True when applied."""
        asyncio.set_event_loop_policy(_ResilientWindowsPolicy())
        return True

else:  # pragma: no cover - only reached on non-Windows or other Python versions

    def install() -> bool:
        """No-op on platforms/Pythons without the patched proactor internals."""
        return False
