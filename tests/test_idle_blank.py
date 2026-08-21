"""`device_boot.IdleBlank` -- the shared power-save gate.

All three boards construct it and nothing off-glass ever ran it: the only
executable driver was `tests/test_tdeck_on_glass.py`, opt-in and hardware-gated,
and `test_dev_channel`'s FakeIdle says in its own docstring that it mimics only
the shape. Its class docstring names three behaviours a hand-rolled second copy
got wrong; those three are what this pins.
"""

from runtime.device_boot import IdleBlank


class _Ptr:
    def __init__(self):
        self.down = True


class _WS:
    def __init__(self):
        self._psave_asleep = False
        self._dirty = False


def _blank(timeout_ms=1000):
    log = []
    ib = IdleBlank(log.append, timeout_ms=timeout_ms)
    return ib, log


def test_it_blanks_only_after_the_whole_timeout_of_quiet():
    ib, log = _blank(1000)
    ws = _WS()
    ib.wake(0)
    ib.tick(999, False, ws)
    assert log == [] and not ib.asleep
    ib.tick(1000, False, ws)
    assert log == [False] and ib.asleep and ws._psave_asleep


def test_activity_keeps_pushing_the_deadline_out():
    ib, log = _blank(1000)
    ws = _WS()
    ib.wake(0)
    for t in range(500, 5000, 500):
        ib.tick(t, True, ws)
    assert log == [] and not ib.asleep


def test_the_waking_touch_is_swallowed_so_it_presses_nothing():
    """(1) in the class docstring: otherwise a wake tap launches a cart."""
    ib, log = _blank(1000)
    ws = _WS()
    ib.wake(0)
    ib.tick(1000, False, ws)
    assert ib.asleep
    ptr = _Ptr()
    assert ib.tick(1100, True, ws, pointer=ptr, click=True) is False
    assert ptr.down is False
    assert log == [False, True] and not ib.asleep


def test_a_click_while_awake_is_passed_straight_through():
    ib, _log = _blank(1000)
    ws = _WS()
    ib.wake(0)
    ptr = _Ptr()
    assert ib.tick(10, True, ws, pointer=ptr, click=True) is True
    assert ptr.down is True          # only the WAKING touch is swallowed


def test_waking_marks_the_console_dirty():
    """(2): the panel may still hold a pre-blank frame, and partial paint would
    happily leave it there."""
    ib, _log = _blank(1000)
    ws = _WS()
    ib.wake(0)
    ib.tick(1000, False, ws)
    ws._dirty = False
    ib.tick(1100, True, ws)
    assert ws._dirty is True and ws._psave_asleep is False


def test_an_explicit_blank_outranks_the_activity_of_asking_for_it():
    """(3): `power off` arrives on the serial channel, which is itself activity,
    so without this it wakes again in the same iteration."""
    ib, log = _blank(1000)
    ws = _WS()
    ib.wake(0)
    ib.blank()
    ib.tick(10, True, ws)            # active=True, the command's own traffic
    assert ib.asleep and log == [False] and ws._psave_asleep


def test_an_explicit_blank_is_a_one_shot():
    ib, log = _blank(1000)
    ws = _WS()
    ib.blank()
    ib.tick(0, True, ws)
    assert log == [False]
    ib.tick(10, True, ws)            # the next activity wakes normally
    assert log == [False, True] and not ib.asleep


def test_a_zero_timeout_disables_the_gate():
    ib, log = _blank(0)
    ws = _WS()
    ib.wake(0)
    ib.tick(10 ** 9, False, ws)
    assert log == [] and not ib.asleep


def test_blanking_twice_lights_the_panel_once_per_transition():
    ib, log = _blank(1000)
    ws = _WS()
    ib.wake(0)
    ib.tick(1000, False, ws)
    ib.tick(2000, False, ws)
    ib.tick(3000, False, ws)
    assert log == [False]            # not one per quiet tick
