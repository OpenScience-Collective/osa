"""The fake OpenRouter server holds garbage collection off while it is up.

An abandoned LiteLLM stream finalized by the collector inside a later request takes
httpcore's non-reentrant pool lock on the thread that already holds it, and hangs the test
forever (see the comment in tests/helpers/openrouter.py). The hang is intermittent, so what
is pinned here is the mechanism that prevents it: no automatic collection while the server
is open, and the collector back on, with a collection made, when it closes.
"""

import gc
import weakref

import pytest

from tests.helpers.openrouter import FakeOpenRouter


def test_automatic_collection_is_off_while_the_server_is_up_and_back_on_after() -> None:
    was_enabled = gc.isenabled()
    gc.enable()
    try:
        server = FakeOpenRouter()
        try:
            assert not gc.isenabled()
        finally:
            server.close()
        assert gc.isenabled()
    finally:
        if not was_enabled:
            gc.disable()


def test_a_collector_that_was_already_off_stays_off() -> None:
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        server = FakeOpenRouter()
        server.close()
        assert not gc.isenabled()
    finally:
        if was_enabled:
            gc.enable()


def test_closing_collects_what_the_disabled_collector_left() -> None:
    class Cycle:
        def __init__(self) -> None:
            self.me = self  # a reference cycle: only the collector can free it

    server = FakeOpenRouter()
    try:
        ref = weakref.ref(Cycle())
        assert ref() is not None, "with automatic collection off, the cycle is still there"
    finally:
        server.close()
    assert ref() is None, "close() collected it"


def test_the_context_manager_puts_collection_back_when_the_body_raises() -> None:
    """A fixture is written `with FakeOpenRouter() as server`, so a failure in it cleans up."""
    was_enabled = gc.isenabled()
    gc.enable()
    try:
        with pytest.raises(RuntimeError, match="the body failed"), FakeOpenRouter():
            assert not gc.isenabled()
            raise RuntimeError("the body failed")
        assert gc.isenabled()
    finally:
        if not was_enabled:
            gc.disable()


def test_collection_comes_back_even_when_stopping_the_server_fails(monkeypatch) -> None:
    was_enabled = gc.isenabled()
    gc.enable()
    try:
        server = FakeOpenRouter()
        real_shutdown = server._server.shutdown

        def failing_shutdown() -> None:
            real_shutdown()  # the server really stops; the failure is reported after it
            raise OSError("shutdown failed")

        monkeypatch.setattr(server._server, "shutdown", failing_shutdown)
        with pytest.raises(OSError, match="shutdown failed"):
            server.close()
        assert gc.isenabled()
    finally:
        if not was_enabled:
            gc.disable()


def test_a_server_that_cannot_start_leaves_collection_alone(monkeypatch) -> None:
    """Construction fails before collection is switched off, so there is nothing to undo."""
    import threading

    was_enabled = gc.isenabled()
    gc.enable()
    try:

        def cannot_start(_thread) -> None:
            raise RuntimeError("cannot start a thread")

        monkeypatch.setattr(threading.Thread, "start", cannot_start)
        with pytest.raises(RuntimeError, match="cannot start a thread"):
            FakeOpenRouter()
        assert gc.isenabled()
    finally:
        if not was_enabled:
            gc.disable()
