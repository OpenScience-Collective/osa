"""The fake OpenRouter server holds garbage collection off while it is up.

An abandoned LiteLLM stream finalized by the collector inside a later request takes
httpcore's non-reentrant pool lock on the thread that already holds it, and hangs the test
forever (see the comment in tests/helpers/openrouter.py). The hang is intermittent, so what
is pinned here is the mechanism that prevents it: no automatic collection while the server
is open, and the collector back on, with a collection made, when it closes.
"""

import gc
import weakref

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
