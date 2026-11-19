"""native/moy_kernel/moy_screen_slot.c on the host's threads: a program's
frame is TAKEN by the console's frame call and stays taken, its pixels
readable in the slot, until a present waits with a newer one. A native
wallpaper's desk scales the kept frame from there (runtime/run/elf_run.py's
`kept`), so a paint that took no new frame shows the last one again."""

import ctypes
import threading

import pytest

from runtime import moy_evq

face_ctypes = pytest.importorskip("runtime.face_ctypes")

_SIGS = (
    ("moy_screen_slot_open", [ctypes.c_uint32, ctypes.c_int, ctypes.c_int, ctypes.c_int],
     ctypes.c_int),
    ("moy_screen_slot_close", [ctypes.c_uint32], None),
    ("moy_screen_slot_present", [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t],
     ctypes.c_int),
)
SHOWN = 2
PID = 7341


@pytest.fixture
def slot():
    try:
        lib = face_ctypes.lib(_SIGS)
    except ImportError as exc:
        pytest.skip(str(exc))
    assert lib.moy_screen_slot_open(PID, 4, 2, 1) == 0
    try:
        yield lib
    finally:
        lib.moy_screen_slot_close(PID)


def _frame(max_ms):
    return moy_evq.screen_frame(PID, 0, 0, 0, max_ms) & SHOWN


def test_the_frame_taken_stays_until_a_newer_one_waits(slot):
    px = moy_evq.screen_pixels(PID)
    a, b = bytes(range(8)), bytes(range(8, 16))
    assert slot.moy_screen_slot_present(PID, a, 8) == 0     # a free slot takes it at once
    assert _frame(50)
    assert bytes(px) == a
    for wait in (0, 5):                     # nothing newer: still the frame taken
        assert not _frame(wait)
        assert bytes(px) == a

    rc = []
    t = threading.Thread(target=lambda: rc.append(slot.moy_screen_slot_present(PID, b, 8)))
    t.start()
    try:
        t.join(0.1)
        assert t.is_alive()                 # it waits for a frame call to let `a` go
        assert bytes(px) == a
        assert _frame(1000)
    finally:
        t.join(5)
    assert rc == [0] and bytes(px) == b


def test_a_frame_call_that_lets_go_waits_for_the_frame_past_its_time(slot):
    px = moy_evq.screen_pixels(PID)
    assert slot.moy_screen_slot_present(PID, bytes(8), 8) == 0
    assert _frame(50)
    rc = []
    t = threading.Thread(target=lambda: rc.append(
        slot.moy_screen_slot_present(PID, b"\x07" * 8, 8)))
    t.start()
    try:
        t.join(0.1)
        # a call of no time still takes the frame it let go for
        assert _frame(0)
    finally:
        t.join(5)
    assert rc == [0] and bytes(px) == b"\x07" * 8
