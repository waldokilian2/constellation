"""Windows accept-resilience patch — classifier + loop behaviour.

Run with: python tests/run_tests.py
No pytest — stdlib only (repo convention: no extra deps).
"""
from __future__ import annotations
import asyncio
import socket

import win_accept_resilience as war


def test_transient_errnos_classified():
    assert war.is_transient_accept_error(OSError(64, "network name gone"))
    assert war.is_transient_accept_error(OSError(10054, "reset"))
    assert war.is_transient_accept_error(OSError(10053, "aborted"))
    assert war.is_transient_accept_error(OSError(10050, "net down"))


def test_fatal_errnos_not_classified():
    assert not war.is_transient_accept_error(OSError(22, "EINVAL"))
    assert not war.is_transient_accept_error(OSError(10048, "EADDRINUSE"))
    assert not war.is_transient_accept_error(ValueError("nope"))
    assert not war.is_transient_accept_error(None)


def test_install_returns_bool():
    assert isinstance(war.install(), bool)


def test_patched_loop_survives_aborted_accept_and_keeps_serving():
    """The reimplemented accept loop still serves, even after a client
    connection is aborted (RST) before being accepted."""
    if not war.install():
        return  # non-Windows / other Python: nothing to verify

    async def scenario():
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        accepted = []

        async def handler(reader, writer):
            accepted.append(writer)
            writer.close()
            await writer.wait_closed()

        server.close()
        server = await asyncio.start_server(handler, "127.0.0.1", port)

        # Aborted first connection: SO_LINGER 0 forces an RST, so the accept
        # callback observes a failed future — the patched loop must survive.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                     __import__("struct").pack("ii", 1, 0))
        s.connect(("127.0.0.1", port))
        s.close()

        # A normal connection must still be accepted afterwards.
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.close()
        await asyncio.sleep(0.2)
        assert len(accepted) >= 1, "server failed to accept after aborted connection"

    asyncio.run(scenario())
