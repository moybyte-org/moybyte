"""The board-model desktop MicroPython runs its threads under a GIL.

Every board's MicroPython takes turns between threads under one GIL, and the
T-Deck's input poller is written for that: it stages keyboard state into the
object the frame loop reads, adds attributes to it, and is gated by a lock the
loop releases. The unix port's own default is parallel threads with no GIL
(`ports/unix/mpconfigport.mk`), and on that build the poller and the loop
race: a map rehashed while the other thread reads it, a collection that frees
what a thread mid-store is holding. `tests/test_frame_alloc.py` lost runs to a
segfault or a lost attribute, the driver reporting nothing.

`make unix-micropython` builds the board-model binary with the GIL on, in a
directory named for the model, and `tests/unix_mp.py` probes the binary it is
about to hand out. These tests hold the probe to both answers: the board model
passes it, and the plain desktop build -- parallel threads -- fails it, so the
probe cannot drift into answering yes to everything.
"""

import pytest

from unix_mp import has_gil, require_unix_mp


def test_the_board_model_binary_runs_its_threads_under_a_gil():
    exe = require_unix_mp(
        board_model=True,
        why="Suites measured on the boards' model assume its threads take "
            "turns, as a board's do; a binary built without the GIL lets the "
            "input poller and the frame loop race.")
    assert has_gil(exe), exe


def test_the_probe_tells_parallel_threads_from_a_gil():
    exe = require_unix_mp(
        why="The probe's negative control is the plain desktop build, which "
            "runs threads in parallel.")
    if has_gil(exe):
        pytest.skip("the plain desktop build runs under a GIL too: nothing "
                    "here to tell the probe apart from")
    assert not has_gil(exe)
